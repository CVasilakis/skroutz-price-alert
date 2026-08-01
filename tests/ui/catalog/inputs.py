"""Shared input builders for scenarios.

Thin factories that construct real resolved-setting entries,
``ResolvedSettings``, ``ConfigView``, and systemd property dicts so
scenarios stay terse and use genuine display formatters and warning strings
rather than re-spelling them. Kept separate from the scenario-registry
machinery in :mod:`_base` for readability.
"""

import logging
from collections.abc import Sequence
from typing import Any

from core.scrapers.framework.settings import framework_setting_specs
from core.settings import ResolvedSetting, ResolvedSettings, SettingStatus
from core.tui.config_check import ConfigView, config_view

SPEC_INTERVAL, SPEC_RETENTION, SPEC_NOTIFY, SPEC_SUPPRESS_REPEATED = framework_setting_specs("1h")
STATUS_OK = SettingStatus.OK
STATUS_DEFAULT = SettingStatus.DEFAULT
STATUS_INVALID = SettingStatus.INVALID

# A throwaway currency symbol used across price scenarios (matches the scraper default).
CURRENCY = "€"

# Faithful persistence/configuration error messages shared across UI scenarios.
STORAGE_MISSING = "Create missing `config/skroutz.json` from the plugin example."
STORAGE_PERMS = "Cannot read `config/skroutz.json`; check its permissions."
STORAGE_BAD_JSON = "Fix JSON in `config/skroutz.json` at line 12, column 4."
STATE_BAD_JSON = "Fix JSON in `state/skroutz.json` at line 8, column 2."
NOTIFICATIONS_NONE = "No notification URLs found in `config/general.json`."


def stub_logger(target: str = "stub") -> logging.Logger:
    """A target-identifying no-op logger for scripted reporter scenarios."""
    lg = logging.getLogger(f"scraper.{target}")
    lg.handlers = [logging.NullHandler()]
    lg.propagate = False
    return lg


# --- Resolved-setting entry builders (the RUN settings section) ---------------------
# Each entry uses the real spec plus a synthetic ResolvedSetting, so the production
# presentation mapper supplies the exact label, display formatting, and warning.


def _view(spec, value: Any, status: SettingStatus, _raw: Any = None) -> tuple:
    return spec, ResolvedSetting(value, status)


def interval_view(value: str = "1h", status: SettingStatus = STATUS_OK, raw: Any = "1h") -> tuple:
    return _view(SPEC_INTERVAL, value, status, raw)


def retention_view(value: int = 7, status: SettingStatus = STATUS_OK, raw: Any = 7) -> tuple:
    return _view(SPEC_RETENTION, value, status, raw)


def notify_view(value: bool = True, status: SettingStatus = STATUS_OK, raw: Any = True) -> tuple:
    return _view(SPEC_NOTIFY, value, status, raw)


def suppress_repeated_view(
    value: bool = False, status: SettingStatus = STATUS_OK, raw: Any = False
) -> tuple:
    return _view(SPEC_SUPPRESS_REPEATED, value, status, raw)


def views_all_ok() -> list[tuple]:
    """Every built-in setting explicitly set to a valid value."""
    return [
        interval_view("2h", STATUS_OK, "2h"),
        retention_view(14, STATUS_OK, 14),
        notify_view(False, STATUS_OK, False),
        suppress_repeated_view(True, STATUS_OK, True),
    ]


def views_all_default() -> list[tuple]:
    """Every built-in setting unset (showing its active default)."""
    return [
        interval_view("1h", STATUS_DEFAULT, None),
        retention_view(7, STATUS_DEFAULT, None),
        notify_view(True, STATUS_DEFAULT, None),
        suppress_repeated_view(False, STATUS_DEFAULT, None),
    ]


def views_one_invalid_each() -> list[tuple]:
    """Each built-in setting invalid at once (every row carries its warning footnote)."""
    return [
        interval_view("1h", STATUS_INVALID, "3h"),
        retention_view(7, STATUS_INVALID, 99),
        notify_view(True, STATUS_INVALID, "maybe"),
        suppress_repeated_view(False, STATUS_INVALID, "maybe"),
    ]


# --- ResolvedSettings (the STATUS settings section) ---------------------------------

_Triple = tuple[object, SettingStatus, object]


def resolved_settings(
    interval: _Triple = ("1h", STATUS_OK, "1h"),
    retention: _Triple = (7, STATUS_OK, 7),
    notify: _Triple = (True, STATUS_OK, True),
    suppress_repeated: _Triple = (False, STATUS_OK, False),
) -> ResolvedSettings:
    """A ``ResolvedSettings`` for status, built from synthetic ``(value, status, raw)``."""
    pairs = [
        (SPEC_INTERVAL, ResolvedSetting(interval[0], interval[1])),
        (SPEC_RETENTION, ResolvedSetting(retention[0], retention[1])),
        (SPEC_NOTIFY, ResolvedSetting(notify[0], notify[1])),
        (
            SPEC_SUPPRESS_REPEATED,
            ResolvedSetting(suppress_repeated[0], suppress_repeated[1]),
        ),
    ]
    return ResolvedSettings(pairs)


# --- systemd property dicts (the STATUS systemd rows) -------------------------------


def timer_props(active: bool = True, next_elapse: str = "Mon 2026-06-29 13:00:00 UTC") -> dict:
    """A ``<target>-scraper.timer`` property dict."""
    return {
        "ActiveState": "active" if active else "inactive",
        "NextElapseUSecRealtime": next_elapse,
    }


def service_props(
    running: bool = False,
    result: str = "success",
    exec_status: str = "0",
    exec_start: str = "Sun 2026-06-28 13:00:00 UTC",
) -> dict:
    """A ``<target>-scraper.service`` property dict.

    Pass ``exec_start=""`` to model a service that has never run (no Last Execution rows).
    """
    return {
        "ActiveState": "active" if running else "inactive",
        "Result": result,
        "ExecMainStatus": exec_status,
        "ExecMainStartTimestamp": exec_start,
    }


# --- ConfigView (the CONFIG row atop Service Status / Scraping panels) --------------


def config_ok(count: int = 5) -> ConfigView:
    """A healthy target-configuration summary (``✅ N loaded``)."""
    return config_view(count)


def config_faulty(count: int = 8, faulty_indices: Sequence[int] = (2, 5)) -> ConfigView:
    """A target configuration with some misconfigured items (``🟡``)."""
    return config_view(
        count,
        list(faulty_indices),
        source_path="config/skroutz.json",
    )


def config_failed(error: str) -> ConfigView:
    """A failed target-configuration load (``❗ Failed`` + the storage error footnote)."""
    return config_view(0, (), error)
