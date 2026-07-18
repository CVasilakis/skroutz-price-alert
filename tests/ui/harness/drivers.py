"""Per-surface drivers: build a :class:`BuildResult` from synthetic inputs.

Each driver exercises the *real* production panel-building code with controlled inputs, so
the captured snapshot reflects exactly what the application renders:

* ``drive_run`` replays a script of public ``InteractiveRunReporter`` calls (with
  ``rich.live.Live`` stubbed) and captures the resulting panel — the same panel the
  orchestrator drives at runtime.
* ``drive_service`` / ``drive_not_installed`` / ``drive_orphan`` call the pure builders
  extracted into ``status.py``.
* ``drive_ping`` calls the pure builder extracted into ``ping.py``.
* ``drive_config`` calls the existing ``config_check._append_*`` helpers with the four
  external seams (update check, general-settings resolution, env check, URL
  classification) patched.
"""

import datetime
import io
import json
import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Sequence
from unittest import mock

from rich.console import Console
from rich.text import Text

from core.ui import tui
from core import status
from core import ping
from core.ui import config_check
from core import logger as core_logger
from core.exceptions import UpdateCheckError, EnvFileError
from core.general import ReminderService
from core.general.settings import (
    GENERAL_SETTING_SPECS, KEY_REMINDER, KEY_REMINDER_DAY, KEY_REMINDER_TIME,
)
from core.logger import setup_global_logging
from core.ui.panel import StatusPanelBuilder
from core.settings import ResolvedSettings, resolve_spec

from ui.catalog._base import BuildResult
# NB: ``ui.harness.rendering`` is imported lazily inside drive_startup, not here:
# rendering imports ui.catalog._base, whose package __init__ imports the scenario modules,
# which import this module — a top-level import here would close that cycle.


# The healthy 'Config' row every real Service Status panel leads with; overridden per
# scenario (faulty / failed) or set to None (no row, e.g. missing dependencies).
_DEFAULT_CONFIG = config_check.config_view(5)


# --- RUN: interactive scraping panel ------------------------------------------------

class _FakeLive:
    """A no-op stand-in for ``rich.live.Live`` so the strategy accumulates state without
    starting a real live display (which would emit to the terminal and animate)."""

    def __init__(self, *args, **kwargs):
        pass

    def start(self, *args, **kwargs):
        pass

    def stop(self, *args, **kwargs):
        pass

    def update(self, *args, **kwargs):
        pass


def drive_run(script: Callable[[tui.InteractiveRunReporter], None]) -> BuildResult:
    """Runs ``script`` against a real strategy and captures its final panel.

    The script calls the strategy's public methods (``start_target``, ``log_price_result``,
    ``start_sleep``, ``log_interrupt``, ...) in the exact order the orchestrator would, and
    ends at the visual state to snapshot. A scenario depicting a *finished* target ends its
    script with ``strat.complete_target()`` to settle the final border color; a mid-flight
    scenario (spinner, sleeping) simply stops earlier.

    Returns:
        BuildResult: the panel from ``_generate_panel()`` and its ``border_style``.
    """
    with mock.patch.object(tui, "Live", _FakeLive):
        strat = tui.InteractiveRunReporter()
        # Absorb the blank line complete_target prints; the captured panel comes from
        # _generate_panel(), not this console.
        strat.console = Console(file=io.StringIO())
        script(strat)
        panel = strat._generate_panel()
    return BuildResult(panel, str(panel.border_style))


# --- E2E_RUN: the same panel, driven by the real orchestrator ------------------------

def drive_orchestrated_run(products: list[dict],
                           results_by_url: dict[str, list],
                           *, has_services: bool = False,
                           delivery_ok: bool = True) -> BuildResult:
    """Runs the *real* ``ScrapingOrchestrator`` over a scripted store and captures the
    finished interactive panel.

    Where :func:`drive_run` replays a hand-written script of strategy calls (and so can
    depict any rendering state), this driver closes the loop the other way: the notes,
    warnings, and footnotes on the captured panel are whatever the production
    orchestrator actually emits (via ``core.messages``) for the given scrape outcomes —
    nothing is hand-fed to the UI. Only the pacing sleep, the signal-handler install,
    and the per-target file logger are patched.

    Args:
        products: The config rows written to the temp ``fakestore.json``.
        results_by_url: ``url -> [outcome, ...]`` where each outcome is a
            ``PriceResult`` or an exception instance to raise; consecutive attempts
            consume the list and the last entry repeats.
        has_services / delivery_ok: The notifier double's gates (see
            ``support.mock_notifier``), controlling which notification note appears.

    Returns:
        BuildResult: the finished panel and its settled border color.
    """
    from core import orchestrator as orchestrator_module
    from core.orchestrator import ScrapingOrchestrator
    from core.scrapers.api import ScraperClient
    from core.scrapers.registry import ClientLoader
    from core.preflight import load_targets
    from support import catalog_sandbox, fake_plugin, mock_notifier

    scripts = {url: list(outcomes) for url, outcomes in results_by_url.items()}

    class _ScriptedClient(ScraperClient):
        def scrape(self, item):
            script = scripts[item.url]
            outcome = script.pop(0) if len(script) > 1 else script[0]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    stub = logging.getLogger("ui.catalog.e2e-stub")
    stub.handlers[:] = [logging.NullHandler()]
    stub.propagate = False

    cfg_dir = tempfile.mkdtemp()
    try:
        canonical_products = []
        for index, product in enumerate(products, 1):
            canonical_products.append(
                {"id": f"item-{index}", "skip": False, **product}
                if isinstance(product, dict) else product
            )
        with open(os.path.join(cfg_dir, "fakestore.json"), "w") as f:
            json.dump({"settings": {}, "items": canonical_products}, f)

        plugin = fake_plugin(name="fakestore", domains=("fake-store.example",),
                             client_class=_ScriptedClient)
        with catalog_sandbox(plugin) as catalog, mock.patch.object(tui, "Live", _FakeLive):
            strat = tui.InteractiveRunReporter()
            strat.console = Console(file=io.StringIO())

            loader = ClientLoader()
            loads = load_targets([catalog.get("fakestore")], cfg_dir)
            orch = ScrapingOrchestrator(
                target_loads=loads, client_loader=loader,
                notifier=mock_notifier(has_services=has_services, delivery_ok=delivery_ok),
                quiet=False, reporter=strat,
            )
            with mock.patch("core.execution.ItemExecutor.sleep_with_jitter"), \
                 mock.patch.object(orchestrator_module.signal, "signal"), \
                 mock.patch.object(orchestrator_module, "get_target_logger",
                                   lambda *a, **k: stub):
                orch.run()
            panel = strat._generate_panel()
        return BuildResult(panel, str(panel.border_style))
    finally:
        shutil.rmtree(cfg_dir, ignore_errors=True)


# --- STATUS: service / not-installed / orphan panels --------------------------------

def drive_service(target: str, timer: dict, service: dict, resolved: ResolvedSettings,
                  config_filename: str = "skroutz.json",
                  expected_oncalendar: str = "", active_oncalendar: str = "",
                  config: config_check.ConfigView = _DEFAULT_CONFIG) -> BuildResult:
    """Builds a per-plugin Service Status panel via ``status.build_service_panel``.

    ``config`` is the leading 'Config' row (products-config health); it defaults to a
    healthy load so the common case is exercised, and is overridden with a faulty/failed
    :class:`ConfigView` per scenario.
    """
    panel = status.build_service_panel(
        target, timer, service, resolved,
        config_filename, expected_oncalendar, active_oncalendar, config,
        display_name=target.capitalize(),
        interval_spec=__import__("ui.catalog.inputs", fromlist=["SPEC_INTERVAL"]).SPEC_INTERVAL,
    )
    return BuildResult(panel, panel.get_panel_color())


def drive_not_installed(target: str) -> BuildResult:
    """Builds the red 'service not installed' panel via ``status.build_not_installed_panel``."""
    return BuildResult(status.build_not_installed_panel(target), "red")


def drive_orphan(name: str) -> BuildResult:
    """Builds the orphaned-unit panel via ``status.build_orphan_panel``."""
    panel = status.build_orphan_panel(name)
    return BuildResult(panel, panel.get_panel_color())


# --- PING: notification check panel -------------------------------------------------

def drive_ping(url_entries: Sequence[tuple[str, bool]],
               test_results: Sequence[tuple[str, bool]] = (),
               env_error_msg: str = "") -> BuildResult:
    """Builds the Notification Check Results panel via ``ping.build_ping_panel``."""
    panel, color = ping.build_ping_panel(list(url_entries), list(test_results), env_error_msg)
    return BuildResult(panel, color)


# --- CONFIG: configuration check panel ----------------------------------------------

def drive_config(version_state: str = "uptodate",
                 valid_count: int = 0, invalid_count: int = 0,
                 env_error: str = "", reminder_raw: object = None,
                 reminder_day_raw: object = None,
                 reminder_time_raw: object = None) -> BuildResult:
    """Builds the Configuration Check panel (global checks only), patching its seams.

    Per-scraper products-config health is no longer on this panel — it now leads each
    Service Status (STATUS surface) and Scraping (RUN surface) panel — so this drives
    the version row, the ``.env`` row, and the general (project-wide) settings rows (in
    that on-panel order: the general settings follow the .env row).

    Args:
        version_state (str): ``"uptodate"`` / ``"available"`` / ``"error"`` — controls the
            patched ``check_for_updates`` (return False / return True / raise).
        valid_count (int): number of valid notification URLs the .env row reports.
        invalid_count (int): number of invalid notification URLs.
        env_error (str): a ``.env`` error message; when set (and no URLs), the .env row
            renders the 'Not configured' state with this message.
        reminder_raw (object): the raw ``reminder`` value the patched general-settings
            resolution sees; ``None`` (unset) renders the active default, an unsupported
            value renders the invalid-value row.
        reminder_day_raw (object): the raw ``reminder_day`` value (same semantics).
        reminder_time_raw (object): the raw ``reminder_time`` value (same semantics).
    """
    panel = StatusPanelBuilder("Configuration Check")

    def check_for_updates() -> bool:
        if version_state == "error":
            raise UpdateCheckError("could not reach the update endpoint")
        return version_state == "available"

    def check_env_file() -> None:
        if env_error:
            raise EnvFileError(env_error)

    def classify(_urls):
        return (["valid"] * valid_count, ["invalid"] * invalid_count)

    def resolve_general(_config_dir) -> ResolvedSettings:
        # The real resolve machinery against a synthetic settings block, so each row
        # renders with production status/default/invalid logic but no file I/O. The
        # Use the production resolver so valid/default/invalid rows match runtime.
        from core.settings import resolve_settings
        block = {
            KEY_REMINDER: reminder_raw,
            KEY_REMINDER_DAY: reminder_day_raw,
            KEY_REMINDER_TIME: reminder_time_raw,
        }
        return resolve_settings(GENERAL_SETTING_SPECS, block)

    with mock.patch.object(config_check, "check_for_updates", check_for_updates), \
         mock.patch.object(config_check, "check_env_file", check_env_file), \
         mock.patch.object(config_check, "classify_notification_urls", classify), \
         mock.patch.object(config_check, "resolve_general_settings", resolve_general):
        config_check._append_version_row(panel)
        config_check._append_env_row(panel)
        config_check._append_general_rows(panel)

    return BuildResult(panel, panel.get_panel_color())


# --- STARTUP: full interactive pre-scrape console transcript -------------------------

def _emit_reminder(console: Console, reminder_raw: object) -> None:
    """Runs the *real* ``ReminderService.run_once()`` with root logging wired to ``console``.

    Mirrors an interactive run's logging setup (``setup_global_logging(quiet=False)`` points
    the root Rich handler at the console). The reminder logs only to its own file
    (``propagate=False``), so nothing should reach ``console`` — but if that isolation ever
    regresses, the stray line is captured onto the shared transcript exactly where a terminal
    user would see it, between the Configuration Check and Scraping panels.

    Runs offline and non-mutating: a mock notifier, a stubbed (False) update check, and a
    fixed clock with ``last_reminder`` on the current slot so the reminder is never due
    (no send, no network), while an invalid ``reminder`` value still exercises the warning
    path. ``LOGS_DIR`` is already redirected to a temp dir by the autouse conftest fixture.
    """
    cfg_dir = tempfile.mkdtemp()
    now = datetime.datetime(2026, 7, 4, 14, 0, 0)  # Saturday 14:00, just after the 13:00 slot
    settings = {} if reminder_raw is None else {"reminder": reminder_raw}
    with open(os.path.join(cfg_dir, "general.json"), "w") as f:
        json.dump({"settings": settings, "last_reminder": "04-07-2026 13:00:00"}, f)

    reminder_logger = logging.getLogger("scraper.reminder")
    saved = (logging.root.handlers[:], logging.root.level, reminder_logger.handlers[:])
    try:
        # Drop any cached handler so a fresh one is created under the redirected LOGS_DIR.
        reminder_logger.handlers[:] = []
        with mock.patch.object(core_logger, "console", console):
            setup_global_logging(quiet=False)  # interactive: root Rich handler -> console
            ReminderService(cfg_dir, notifier=mock.Mock(),
                            now_fn=lambda: now, update_check_fn=lambda: False).run_once()
    finally:
        logging.root.handlers[:], logging.root.level, reminder_logger.handlers[:] = saved
        shutil.rmtree(cfg_dir, ignore_errors=True)


def drive_startup(run_script: Callable[[tui.InteractiveRunReporter], None], *,
                  reminder_raw: object = None, version_state: str = "uptodate",
                  valid_count: int = 1, invalid_count: int = 0,
                  env_error: str = "") -> BuildResult:
    """Captures the full interactive pre-scrape console transcript, as ``main()`` emits it.

    Reproduces the console output of an interactive run's startup onto *one* recording
    console: the Configuration Check panel, the once-per-run ``ReminderService.run_once()``
    (wired to the root console handler exactly as an interactive run configures it), then
    the interactive Scraping panel produced by ``run_script``. Because everything shares a
    single console, any text a component prints *between* the panels lands in the transcript
    right where a terminal user would see it — this is the regression surface for "a line
    printed outside a panel during an interactive run" (see ``rendering.lines_outside_panels``).

    Args:
        run_script: The ``drive_run``-style script driving the Scraping panel.
        reminder_raw: The raw ``reminder`` value the Configuration Check panel and the live
            reminder check both see (``None`` = unset/default).
        version_state / valid_count / invalid_count / env_error: Forwarded to
            :func:`drive_config` for the Configuration Check panel.
    """
    # Lazy import to avoid a module-load cycle (see the note at the top of this file).
    from ui.harness.rendering import make_recording_console, paint

    config_result = drive_config(version_state, valid_count=valid_count,
                                 invalid_count=invalid_count, env_error=env_error,
                                 reminder_raw=reminder_raw)
    run_result = drive_run(run_script)

    console = make_recording_console()
    console.print()                          # main() prints a leading blank line
    paint(console, config_result)
    console.print()                          # blank between preflight and the run
    _emit_reminder(console, reminder_raw)    # logs to file only; a leak would land here
    paint(console, run_result)

    text = console.export_text(styles=False)
    transcript = "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")
    return BuildResult(renderable=Text(transcript), border_color=run_result.border_color)
