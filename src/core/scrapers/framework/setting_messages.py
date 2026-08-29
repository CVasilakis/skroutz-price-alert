"""Messages for invalid framework-owned scraper settings.

Separated from the declarations in ``framework/setting_specs.py`` so wording can be
reviewed in one place, and so the settings module stays a description of behavior
rather than a mix of behavior and prose. Each message states the accepted values
and the fallback, since it is shown precisely when a user got the value wrong.
"""

from core.settings import (
    DEFAULT_LOG_RETENTION_DAYS,
    MAX_LOG_RETENTION_DAYS,
    MIN_LOG_RETENTION_DAYS,
    unsupported_value_message,
)


def interval_warning_message() -> str:
    """Warn that an execution interval is not one of the supported cadences."""
    return unsupported_value_message("execution_interval")


def retention_warning_message() -> str:
    """Warn that log retention is outside the supported day range."""
    return (
        f"log_retention_days must be {MIN_LOG_RETENTION_DAYS}-{MAX_LOG_RETENTION_DAYS}. "
        f"Using default {DEFAULT_LOG_RETENTION_DAYS}."
    )


def notify_errors_warning_message() -> str:
    """Warn that the error-notification toggle is not a boolean."""
    return "Invalid notify_scraping_errors setting. Defaulting to true."


def suppress_repeated_price_alerts_warning_message() -> str:
    """Warn that the repeat-alert toggle is not a boolean."""
    return "Invalid repeated-alert setting. Using default false."
