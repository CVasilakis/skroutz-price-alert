"""Per-surface drivers: build a :class:`BuildResult` from synthetic inputs.

Each driver exercises the *real* production panel-building code with controlled inputs, so
the captured snapshot reflects exactly what the application renders:

* ``drive_run`` replays one script through both production reporters and captures the
  interactive panel plus the background target log.
* ``drive_service`` / ``drive_not_installed`` / ``drive_orphan`` call the pure builders
  in ``core.tui.status``.
* ``drive_ping`` calls the pure builder in ``core.tui.ping``.
* ``drive_config`` calls ``config_check.build_config_panel`` with an
  immutable synthetic general-config load.
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

from core.application import orchestrator as orchestrator_module
from core.application.contracts import ConfigOutcome, RunReporter
from core.application.reporting import SilentRunReporter
from core.general import ReminderService
from core.general.configuration import GeneralConfigLoad
from core.general.reminder_state import ReminderStateRepository
from core.general.settings import (
    GENERAL_SETTING_SPECS,
    KEY_REMINDER,
    KEY_REMINDER_DAY,
    KEY_REMINDER_TIME,
)
from core.infrastructure import logging as core_logger
from core.infrastructure.locking import StateLockManager
from core.infrastructure.logging import setup_global_logging
from core.notifications.configuration import NotificationConfig
from core.settings import ResolvedSettings
from core.tui import config_check, ping, run_reporter, status
from ui.catalog._base import BuildResult
from ui.harness.output_logs import OutputLogCapture

# NB: ``ui.harness.rendering`` is imported lazily inside drive_startup, not here:
# rendering imports ui.catalog._base, whose package __init__ imports the scenario modules,
# which import this module — a top-level import here would close that cycle.


# The healthy 'Config' row every real Service Status panel leads with; overridden per
# scenario (faulty / failed) or set to None (no row, e.g. missing dependencies).
_DEFAULT_CONFIG = config_check.config_view(5)


# --- RUN: interactive scraping panel ------------------------------------------------


class _FakeLive:
    """A no-op stand-in for ``rich.live.Live`` so the reporter accumulates state without
    starting a real live display (which would emit to the terminal and animate)."""

    def __init__(self, *args, **kwargs):
        pass

    def start(self, *args, **kwargs):
        pass

    def stop(self, *args, **kwargs):
        pass

    def update(self, *args, **kwargs):
        pass


class _CapturedSilentReporter(SilentRunReporter):
    """Replace a scripted scenario's no-op logger with its real target file logger."""

    def __init__(self, capture: OutputLogCapture) -> None:
        super().__init__()
        self._capture = capture

    def start_target(
        self,
        target_name: str,
        target_logger: logging.Logger,
        settings: ResolvedSettings,
        config: ConfigOutcome,
    ) -> None:
        prefix = "scraper."
        if not target_logger.name.startswith(prefix):
            raise AssertionError(
                f"Scripted target logger must identify itself as {prefix}<target>: "
                f"{target_logger.name}"
            )
        target = target_logger.name.removeprefix(prefix)
        super().start_target(target_name, self._capture.logger_for(target), settings, config)


def _drive_run(
    script: Callable[[RunReporter], None],
    capture: OutputLogCapture,
) -> BuildResult:
    """Runs ``script`` against a real reporter and captures its final panel.

    The script calls the reporter's public methods (``start_target``, ``log_price_result``,
    ``start_sleep``, ``log_interrupt``, ...) in the exact order the application workflow
    would, and ends at the visual state to snapshot. A scenario depicting a *finished*
    target ends its script with ``reporter.complete_target()`` to settle the final border color; a mid-flight
    scenario (spinner, sleeping) simply stops earlier.

    Returns:
        BuildResult: the panel from ``_generate_panel()`` and its ``border_style``.
    """
    with mock.patch.object(run_reporter, "Live", _FakeLive):
        reporter = run_reporter.InteractiveRunReporter()
        # Absorb the blank line complete_target prints; the captured panel comes from
        # _generate_panel(), not this console.
        reporter.console = Console(file=io.StringIO())
        script(reporter)
        panel = reporter._generate_panel()

    script(_CapturedSilentReporter(capture))
    return BuildResult(panel, str(panel.border_style), output_logs=capture.artifacts())


def drive_run(script: Callable[[RunReporter], None]) -> BuildResult:
    """Run one script through both production reporters in isolated environments."""
    with OutputLogCapture() as capture:
        return _drive_run(script, capture)


# --- E2E_RUN: the same panel, driven by the real application workflow ----------------


def drive_orchestrated_run(
    items: list[dict],
    results_by_url: dict[str, list],
    *,
    has_services: bool = False,
    delivery_ok: bool = True,
) -> BuildResult:
    """Runs the *real* ``ScrapingOrchestrator`` over a scripted store and captures the
    finished interactive panel and quiet file log.

    Where :func:`drive_run` replays a hand-written script of reporter calls (and so can
    depict any rendering state), this driver closes the loop the other way: the notes,
    warnings, and footnotes on the captured panel are whatever the production
    application workflow actually emits (via ``core.messages``) for the given scrape outcomes —
    nothing is hand-fed to the UI. Only the pacing sleep, the signal-handler install,
    and signal-handler installation are patched.

    Args:
        items: The config rows written to the temp ``fakestore.json``.
        results_by_url: ``url -> [outcome, ...]`` where each outcome is a
            ``PriceResult`` or an exception instance to raise; consecutive attempts
            consume the list and the last entry repeats.
        has_services / delivery_ok: The notifier double's gates (see
            ``support.mock_notifier``), controlling which notification note appears.

    Returns:
        BuildResult: the finished panel and its settled border color.
    """
    from support import catalog_sandbox, fake_plugin, mock_notifier

    from core.application.orchestrator import ScrapingOrchestrator
    from core.application.preflight import load_target_configs
    from core.scrapers.api import ScraperClient, UrlField
    from core.scrapers.framework.clients import ClientLoader

    url_field = UrlField("url", domains=("fake-store.example",), accepts_url=lambda _url: True)

    stub = logging.getLogger("ui.catalog.e2e-stub")
    stub.handlers[:] = [logging.NullHandler()]
    stub.propagate = False

    def execute(*, quiet: bool, reporter, get_logger) -> object | None:
        scripts = {url: list(outcomes) for url, outcomes in results_by_url.items()}

        class _ScriptedClient(ScraperClient):
            def scrape(self, item):
                script = scripts[item[url_field]]
                outcome = script.pop(0) if len(script) > 1 else script[0]
                if isinstance(outcome, Exception):
                    raise outcome
                return outcome

        cfg_dir = tempfile.mkdtemp()
        canonical_items = []
        for index, item in enumerate(items, 1):
            canonical_items.append(
                {"id": f"item-{index}", "skip": False, **item} if isinstance(item, dict) else item
            )
        with open(os.path.join(cfg_dir, "fakestore.json"), "w") as f:
            json.dump(
                {
                    "schema_version": 1,
                    "plugin_schema_version": 1,
                    "settings": {},
                    "items": canonical_items,
                },
                f,
            )

        try:
            plugin = fake_plugin(
                name="fakestore",
                domains=("fake-store.example",),
                client_class=_ScriptedClient,
                url_field=url_field,
            )
            with (
                catalog_sandbox(plugin) as catalog,
                mock.patch.object(run_reporter, "Live", _FakeLive),
                mock.patch.object(orchestrator_module, "get_target_logger", get_logger),
            ):
                loader = ClientLoader()
                loads = load_target_configs([catalog.get("fakestore")], cfg_dir)
                orch = ScrapingOrchestrator(
                    target_loads=loads,
                    client_loader=loader,
                    notifier=mock_notifier(has_services=has_services, delivery_ok=delivery_ok),
                    quiet=quiet,
                    reporter=reporter,
                    state_dir=os.path.join(cfg_dir, "state"),
                )
                with (
                    mock.patch("core.application.pacing.Pacer.sleep"),
                    mock.patch.object(orchestrator_module.signal, "signal"),
                ):
                    orch.run()
                if isinstance(reporter, run_reporter.InteractiveRunReporter):
                    return reporter._generate_panel()
                return None
        finally:
            shutil.rmtree(cfg_dir, ignore_errors=True)

    with OutputLogCapture() as capture:
        interactive = run_reporter.InteractiveRunReporter()
        interactive.console = Console(file=io.StringIO())
        panel = execute(quiet=False, reporter=interactive, get_logger=lambda *a, **k: stub)
        execute(quiet=True, reporter=SilentRunReporter(), get_logger=capture.logger_for)
        if panel is None:
            raise AssertionError("Interactive orchestrated run did not produce a panel")
        return BuildResult(
            panel,
            str(panel.border_style),
            output_logs=capture.artifacts(),
        )


# --- STATUS: service / not-installed / orphan panels --------------------------------


def drive_service(
    target: str,
    timer: dict,
    service: dict,
    resolved: ResolvedSettings,
    config_filename: str = "skroutz.json",
    expected_oncalendar: str = "",
    active_oncalendar: str = "",
    config: config_check.ConfigView = _DEFAULT_CONFIG,
    state_failure_detail: str | None = None,
) -> BuildResult:
    """Builds a per-plugin Service Status panel via ``status.build_service_panel``.

    ``config`` is the leading 'Config' row (target-configuration health); it defaults to a
    healthy load so the common case is exercised, and is overridden with a faulty/failed
    :class:`ConfigView` per scenario.
    """
    panel = status.build_service_panel(
        target,
        timer,
        service,
        resolved,
        config_filename,
        expected_oncalendar,
        active_oncalendar,
        config,
        display_name=target.capitalize(),
        interval_spec=__import__("ui.catalog.inputs", fromlist=["SPEC_INTERVAL"]).SPEC_INTERVAL,
        state_failure_detail=state_failure_detail,
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


def drive_ping(
    url_entries: Sequence[tuple[str, bool]],
    test_results: Sequence[tuple[str, bool]] = (),
    config_error_msg: str = "",
) -> BuildResult:
    """Builds the Notification Check Results panel via ``ping.build_ping_panel``."""
    panel, color = ping.build_ping_panel(list(url_entries), list(test_results), config_error_msg)
    return BuildResult(panel, color)


# --- CONFIG: configuration check panel ----------------------------------------------


def drive_config(
    version_state: str = "uptodate",
    valid_count: int = 0,
    invalid_count: int = 0,
    config_error: str = "",
    settings_error: str = "",
    permission_warning: str = "",
    reminder_raw: object = None,
    reminder_day_raw: object = None,
    reminder_time_raw: object = None,
) -> BuildResult:
    """Build the Configuration Check panel from synthetic collected inputs.

    Per-scraper target-configuration health is no longer on this panel — it now leads each
    Service Status (STATUS surface) and Scraping (RUN surface) panel — so this drives
    the version row, notification row, and general settings rows in production order.

    Args:
        version_state (str): ``"uptodate"`` / ``"available"`` / ``"fixes"`` / ``"error"``
            selects a deterministic, already-collected software-version result.
        valid_count (int): number of valid notification URLs.
        invalid_count (int): number of invalid notification URLs.
        config_error (str): notification-section failure shown when no URLs are usable.
        settings_error (str): isolated general-settings failure.
        permission_warning (str): advisory general-config permission footnote.
        reminder_raw (object): the raw ``reminder`` value the patched general-settings
            resolution sees; ``None`` (unset) renders the active default, an unsupported
            value renders the invalid-value row.
        reminder_day_raw (object): the raw ``reminder_day`` value (same semantics).
        reminder_time_raw (object): the raw ``reminder_time`` value (same semantics).
    """
    from core.settings import resolve_settings

    block = {
        KEY_REMINDER: reminder_raw,
        KEY_REMINDER_DAY: reminder_day_raw,
        KEY_REMINDER_TIME: reminder_time_raw,
    }
    resolved = resolve_settings(GENERAL_SETTING_SPECS, block)
    general = GeneralConfigLoad(
        notifications=NotificationConfig(
            configured_urls=tuple(["valid"] * valid_count + ["invalid"] * invalid_count),
            valid_urls=tuple(["valid"] * valid_count),
            invalid_urls=tuple(["invalid"] * invalid_count),
            error=config_error or None,
        ),
        settings=None if settings_error else resolved,
        settings_error=settings_error or None,
        permission_warning=permission_warning or None,
    )

    from core.infrastructure.updates import SoftwareVersionStatus

    version_status = {
        "uptodate": SoftwareVersionStatus("1.7.0", False),
        "available": SoftwareVersionStatus("1.7.0", True, "1.8.0"),
        "fixes": SoftwareVersionStatus("1.7.0", True),
        "error": SoftwareVersionStatus("1.7.0", None),
        "branch": SoftwareVersionStatus("1.7.0", None, non_release_branch="beta"),
    }[version_state]
    panel = config_check.build_config_panel(general, version_status)

    return BuildResult(panel, panel.get_panel_color())


# --- STARTUP: full interactive pre-scrape console transcript -------------------------


def _emit_reminder(console: Console, reminder_raw: object, capture: OutputLogCapture) -> None:
    """Runs the *real* ``ReminderService.run_once()`` with root logging wired to ``console``.

    Mirrors an interactive run's logging setup (``setup_global_logging(quiet=False)`` points
    the root Rich handler at the console). The reminder logs only to its own file
    (``propagate=False``), so nothing should reach ``console`` — but if that isolation ever
    regresses, the stray line is captured onto the shared transcript exactly where a terminal
    user would see it, between the Configuration Check and Scraping panels.

    Runs offline and confines mutations to temporary state: a mock notifier, a stubbed
    (False) update check, and a fixed clock cause the missing reminder state to be anchored
    to the current slot without sending. An invalid ``reminder`` value still exercises the
    warning path. ``LOGS_DIR`` is already redirected by the autouse conftest fixture.
    """
    temp_root = tempfile.mkdtemp()
    now = datetime.datetime(2026, 7, 4, 14, 0, 0)  # Saturday 14:00, just after the 13:00 slot
    from core.settings import resolve_settings

    settings = resolve_settings(
        GENERAL_SETTING_SPECS,
        {} if reminder_raw is None else {"reminder": reminder_raw},
    )

    reminder_logger = logging.getLogger("scraper.reminder")
    saved = (logging.root.handlers[:], logging.root.level, reminder_logger.handlers[:])
    try:
        # Drop any cached handler so a fresh one is created under the redirected LOGS_DIR.
        reminder_logger.handlers[:] = []
        with (
            mock.patch.object(core_logger, "console", console),
            mock.patch("core.general.reminder.get_target_logger", side_effect=capture.logger_for),
        ):
            setup_global_logging(quiet=False)  # interactive: root Rich handler -> console
            ReminderService(
                settings,
                ReminderStateRepository(os.path.join(temp_root, "state", "general.json")),
                notifier=mock.Mock(),
                acquire_lock_fn=StateLockManager(os.path.join(temp_root, "state")).acquire,
                now_fn=lambda: now,
                update_check_fn=lambda: False,
            ).run_once()
    finally:
        for handler in reminder_logger.handlers:
            if handler not in saved[2]:
                handler.close()
        logging.root.handlers[:], logging.root.level, reminder_logger.handlers[:] = saved
        shutil.rmtree(temp_root, ignore_errors=True)


def drive_startup(
    run_script: Callable[[RunReporter], None],
    *,
    reminder_raw: object = None,
    version_state: str = "uptodate",
    valid_count: int = 1,
    invalid_count: int = 0,
    config_error: str = "",
) -> BuildResult:
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
        version_state / valid_count / invalid_count / config_error: Forwarded to
            :func:`drive_config` for the Configuration Check panel.
    """
    # Lazy import to avoid a module-load cycle (see the note at the top of this file).
    from ui.harness.rendering import make_recording_console, paint

    with OutputLogCapture() as capture:
        config_result = drive_config(
            version_state,
            valid_count=valid_count,
            invalid_count=invalid_count,
            config_error=config_error,
            reminder_raw=reminder_raw,
        )
        run_result = _drive_run(run_script, capture)

        console = make_recording_console()
        console.print()  # main() prints a leading blank line
        paint(console, config_result)
        console.print()  # blank between preflight and the run
        _emit_reminder(console, reminder_raw, capture)  # logs to file only; a leak would land here
        paint(console, run_result)
        output_logs = capture.artifacts()

    text = console.export_text(styles=False)
    transcript = "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")
    return BuildResult(
        renderable=Text(transcript),
        border_color=run_result.border_color,
        output_logs=output_logs,
    )
