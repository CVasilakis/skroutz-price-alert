"""Shared input builders for scenarios.

Thin factories that construct the *real* production input types (``SettingView``,
``ResolvedSettings``, ``TargetLoad``, the systemd property dicts) so scenarios stay terse
and use genuine display formatters / warning strings rather than re-spelling them. Kept
separate from the scenario-registry machinery in :mod:`_base` for readability.
"""

import logging
from collections.abc import Sequence
from typing import Any

from core.ui.config_check import config_view, ConfigView
from core.scrapers.settings import framework_setting_specs
from core.settings import (
    ResolvedSetting, ResolvedSettings, SettingStatus, SettingView, setting_view,
    resolve_settings,
)

SPEC_INTERVAL, SPEC_RETENTION, SPEC_NOTIFY = framework_setting_specs("1h")
STATUS_OK = SettingStatus.OK
STATUS_DEFAULT = SettingStatus.DEFAULT
STATUS_INVALID = SettingStatus.INVALID

# A throwaway currency symbol used across price scenarios (matches the scraper default).
CURRENCY = "€"

# Faithful persistence/environment error messages, shared across UI scenarios.
# by the CONFIG (.env), STATUS and RUN (products-config) scenarios.
STORAGE_MISSING = "The config/skroutz.json file is missing or not a file"
STORAGE_PERMS = "The config/skroutz.json file has wrong permissions"
STORAGE_BAD_JSON = "The config/skroutz.json file contains invalid JSON format"
ENV_NONE = "No .env file found or unreadable"


def stub_logger() -> logging.Logger:
    """A no-op logger for ``start_target`` (the interactive strategy never writes to it)."""
    lg = logging.getLogger("ui_test.stub")
    lg.handlers = [logging.NullHandler()]
    lg.propagate = False
    return lg


# --- SettingView builders (the RUN settings section) --------------------------------
# A view is built from the real spec + a synthetic ResolvedSetting, so its label, display
# formatting and (for invalid) warning footnote are exactly what production produces.

def _view(spec, value: Any, status: SettingStatus, raw: Any = None) -> SettingView:
    return setting_view(spec, ResolvedSetting(value, status, raw))


# ``raw`` is the user's raw config value (any type, or None when unset), matching
# ``ResolvedSetting.raw: Any`` — annotated Any so an unset (None) raw is accepted.
def interval_view(value: str = "1h", status: SettingStatus = STATUS_OK, raw: Any = "1h") -> SettingView:
    return _view(SPEC_INTERVAL, value, status, raw)


def retention_view(value: int = 7, status: SettingStatus = STATUS_OK, raw: Any = 7) -> SettingView:
    return _view(SPEC_RETENTION, value, status, raw)


def notify_view(value: bool = True, status: SettingStatus = STATUS_OK, raw: Any = True) -> SettingView:
    return _view(SPEC_NOTIFY, value, status, raw)


def views_all_ok() -> list[SettingView]:
    """Every built-in setting explicitly set to a valid value."""
    return [
        interval_view("2h", STATUS_OK, "2h"),
        retention_view(14, STATUS_OK, 14),
        notify_view(False, STATUS_OK, False),
    ]


def views_all_default() -> list[SettingView]:
    """Every built-in setting unset (showing its active default)."""
    return [
        interval_view("1h", STATUS_DEFAULT, None),
        retention_view(7, STATUS_DEFAULT, None),
        notify_view(True, STATUS_DEFAULT, None),
    ]


def views_one_invalid_each() -> list[SettingView]:
    """Each built-in setting invalid at once (every row carries its warning footnote)."""
    return [
        interval_view("1h", STATUS_INVALID, "3h"),
        retention_view(7, STATUS_INVALID, 99),
        notify_view(True, STATUS_INVALID, "maybe"),
    ]


# --- ResolvedSettings (the STATUS settings section) ---------------------------------

_Triple = tuple[object, SettingStatus, object]


def resolved_settings(
    interval: _Triple = ("1h", STATUS_OK, "1h"),
    retention: _Triple = (7, STATUS_OK, 7),
    notify: _Triple = (True, STATUS_OK, True),
) -> ResolvedSettings:
    """A ``ResolvedSettings`` for ``--status``, built from synthetic ``(value, status, raw)``."""
    pairs = [
        (SPEC_INTERVAL, ResolvedSetting(*interval)),
        (SPEC_RETENTION, ResolvedSetting(*retention)),
        (SPEC_NOTIFY, ResolvedSetting(*notify)),
    ]
    return ResolvedSettings(pairs)


# --- systemd property dicts (the STATUS systemd rows) -------------------------------

def timer_props(active: bool = True,
                next_elapse: str = "Mon 2026-06-29 13:00:00 UTC") -> dict:
    """A ``<target>-scraper.timer`` property dict."""
    return {
        "ActiveState": "active" if active else "inactive",
        "NextElapseUSecRealtime": next_elapse,
    }


def service_props(running: bool = False, result: str = "success", exec_status: str = "0",
                  exec_start: str = "Sun 2026-06-28 13:00:00 UTC") -> dict:
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
    """A healthy products-config summary (``✅ N loaded``)."""
    return config_view(count)


def config_faulty(count: int = 8, faulty_indices: Sequence[int] = (2, 5)) -> ConfigView:
    """A products-config summary with some misconfigured items (``🟡``)."""
    return config_view(count, list(faulty_indices))


def config_failed(error: str) -> ConfigView:
    """A failed products-config load (``❗ Failed`` + the storage error footnote)."""
    return config_view(0, (), error)
