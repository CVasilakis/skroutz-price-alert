"""Framework-owned scraper settings and systemd schedule vocabulary."""

from typing import Any

from core.scrapers.api import SettingSpec
from core.scrapers.framework.intervals import (
    SUPPORTED_INTERVALS,
    normalize_interval,
    oncalendar_for,
)
from core.scrapers.framework.setting_messages import (
    interval_warning_message,
    notify_errors_warning_message,
    retention_warning_message,
)
from core.settings import (
    DEFAULT_LOG_RETENTION_DAYS,
    normalize_bool,
    normalize_retention_days,
)

KEY_INTERVAL = "execution_interval"
KEY_RETENTION = "log_retention_days"
KEY_NOTIFY = "notify_scraping_errors"


def _decoded(normalizer, raw: object) -> Any:
    value = normalizer(raw)
    if value is None:
        raise ValueError("unsupported value")
    return value


def framework_setting_specs(default_interval: str) -> tuple[SettingSpec[Any], ...]:
    """Build specs with the owning plugin's concrete interval default."""
    return (
        SettingSpec(
            key=KEY_INTERVAL,
            label="Execution Interval",
            decode=lambda raw: _decoded(normalize_interval, raw),
            display=str,
            warning=interval_warning_message(),
            default=default_interval,
            is_unset=lambda raw: not raw,
        ),
        SettingSpec(
            key=KEY_RETENTION,
            label="Log Retention",
            decode=lambda raw: _decoded(normalize_retention_days, raw),
            display=lambda days: f"{days} day{'s' if days != 1 else ''}",
            warning=retention_warning_message(),
            default=DEFAULT_LOG_RETENTION_DAYS,
        ),
        SettingSpec(
            key=KEY_NOTIFY,
            label="Notify On Errors",
            decode=lambda raw: _decoded(normalize_bool, raw),
            display=lambda value: "true" if value else "false",
            warning=notify_errors_warning_message(),
            default=True,
        ),
    )


__all__ = [
    "KEY_INTERVAL",
    "KEY_RETENTION",
    "KEY_NOTIFY",
    "SUPPORTED_INTERVALS",
    "oncalendar_for",
    "framework_setting_specs",
]
