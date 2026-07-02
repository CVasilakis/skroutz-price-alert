"""Per-surface drivers: build a :class:`BuildResult` from synthetic inputs.

Each driver exercises the *real* production panel-building code with controlled inputs, so
the captured snapshot reflects exactly what the application renders:

* ``drive_run`` replays a script of public ``InteractiveExecutionStrategy`` calls (with
  ``rich.live.Live`` stubbed) and captures the resulting panel — the same panel the
  orchestrator drives at runtime.
* ``drive_service`` / ``drive_not_installed`` / ``drive_orphan`` call the pure builders
  extracted into ``status.py``.
* ``drive_ping`` calls the pure builder extracted into ``ping.py``.
* ``drive_config`` calls the existing ``config_check._append_*`` helpers with the three
  external seams (update check, env check, URL classification) patched.
"""

import io
from typing import Callable, Sequence, Tuple
from unittest import mock

from rich.console import Console

import tui
import status
import ping
import config_check
from exceptions import UpdateCheckError, EnvFileError
from panel import StatusPanelBuilder
from scrapers.base.settings import ResolvedSettings

from ui.catalog._base import BuildResult


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


def drive_run(script: Callable[[tui.InteractiveExecutionStrategy], None]) -> BuildResult:
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
        strat = tui.InteractiveExecutionStrategy()
        # Absorb the blank line complete_target prints; the captured panel comes from
        # _generate_panel(), not this console.
        strat.console = Console(file=io.StringIO())
        script(strat)
        panel = strat._generate_panel()
    return BuildResult(panel, str(panel.border_style))


# --- STATUS: service / not-installed / orphan panels --------------------------------

def drive_service(target: str, timer: dict, service: dict, resolved: ResolvedSettings,
                  config_filename: str = "skroutz.json",
                  expected_oncalendar: str = "", active_oncalendar: str = "",
                  config=_DEFAULT_CONFIG) -> BuildResult:
    """Builds a per-plugin Service Status panel via ``status.build_service_panel``.

    ``config`` is the leading 'Config' row (products-config health); it defaults to a
    healthy load so the common case is exercised, and is overridden with a faulty/failed
    :class:`ConfigView` — or ``None`` (no row, e.g. missing dependencies) — per scenario.
    """
    panel = status.build_service_panel(
        target, timer, service, resolved,
        config_filename, expected_oncalendar, active_oncalendar, config,
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

def drive_ping(url_entries: Sequence[Tuple[str, bool]],
               test_results: Sequence[Tuple[str, bool]] = (),
               env_error_msg: str = "") -> BuildResult:
    """Builds the Notification Check Results panel via ``ping.build_ping_panel``."""
    panel, color = ping.build_ping_panel(list(url_entries), list(test_results), env_error_msg)
    return BuildResult(panel, color)


# --- CONFIG: configuration check panel ----------------------------------------------

def drive_config(version_state: str = "uptodate",
                 valid_count: int = 0, invalid_count: int = 0,
                 env_error: str = "") -> BuildResult:
    """Builds the Configuration Check panel (global checks only), patching its seams.

    Per-scraper products-config health is no longer on this panel — it now leads each
    Service Status (STATUS surface) and Scraping (RUN surface) panel — so this drives only
    the version row and the ``.env`` row.

    Args:
        version_state (str): ``"uptodate"`` / ``"available"`` / ``"error"`` — controls the
            patched ``check_for_updates`` (return False / return True / raise).
        valid_count (int): number of valid notification URLs the .env row reports.
        invalid_count (int): number of invalid notification URLs.
        env_error (str): a ``.env`` error message; when set (and no URLs), the .env row
            renders the 'Not configured' state with this message.
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

    with mock.patch.object(config_check, "check_for_updates", check_for_updates), \
         mock.patch.object(config_check, "check_env_file", check_env_file), \
         mock.patch.object(config_check, "classify_notification_urls", classify):
        config_check._append_version_row(panel)
        config_check._append_env_row(panel)

    return BuildResult(panel, panel.get_panel_color())
