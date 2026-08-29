"""The four settings the framework contributes to every scraper target.

Declarations only. The interval vocabulary they build on lives in
:mod:`core.scrapers.framework.intervals`, and is imported from there rather than
re-exported here, so each name has exactly one place it comes from.
"""

from typing import Any

from core.scrapers.api import SettingSpec
from core.scrapers.framework.intervals import normalize_interval
from core.scrapers.framework.setting_messages import (
    interval_warning_message,
    notify_errors_warning_message,
    retention_warning_message,
    suppress_repeated_price_alerts_warning_message,
)
from core.settings import (
    DEFAULT_LOG_RETENTION_DAYS,
    normalize_bool,
    normalize_retention_days,
)

KEY_INTERVAL = "execution_interval"
KEY_RETENTION = "log_retention_days"
KEY_NOTIFY = "notify_scraping_errors"
KEY_SUPPRESS_REPEATED_PRICE_ALERTS = "suppress_repeated_price_alerts"


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
        SettingSpec(
            key=KEY_SUPPRESS_REPEATED_PRICE_ALERTS,
            label="Repeat Alerts",
            decode=lambda raw: _decoded(normalize_bool, raw),
            display=lambda value: "true" if value else "false",
            warning=suppress_repeated_price_alerts_warning_message(),
            default=False,
        ),
    )


__all__ = [
    "KEY_INTERVAL",
    "KEY_RETENTION",
    "KEY_NOTIFY",
    "KEY_SUPPRESS_REPEATED_PRICE_ALERTS",
    "framework_setting_specs",
]
