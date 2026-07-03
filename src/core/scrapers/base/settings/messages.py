"""Single-home user-facing messages for invalid settings.

Kept in one place (and short, to fit a panel footnote) so wording and bounds never
drift between the resolver, the settings panel and the silent log.
"""

from core.scrapers.base.settings.normalizers import (
    MIN_LOG_RETENTION_DAYS, MAX_LOG_RETENTION_DAYS, DEFAULT_LOG_RETENTION_DAYS,
)


def interval_warning_message() -> str:
    """The single user-facing message for an unsupported ``execution_interval``.

    The settings row already shows the effective default value, so this only needs to
    flag that the configured value was rejected.
    """
    return "Unsupported execution_interval. Using the default."


def retention_warning_message() -> str:
    """The single user-facing message for an invalid ``log_retention_days``.

    Keeps the wording and the 1/30/7 bounds in one place.
    """
    return (
        f"log_retention_days must be {MIN_LOG_RETENTION_DAYS}-{MAX_LOG_RETENTION_DAYS}. "
        f"Using default {DEFAULT_LOG_RETENTION_DAYS}."
    )


def notify_errors_warning_message() -> str:
    """The single user-facing message for an invalid ``notify_scraping_errors``."""
    return "Invalid notify_scraping_errors setting. Defaulting to true."
