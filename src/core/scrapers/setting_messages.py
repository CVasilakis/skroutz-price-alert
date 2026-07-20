"""Messages for invalid framework-owned scraper settings."""

from core.settings import unsupported_value_message
from core.settings.normalizers import (
    DEFAULT_LOG_RETENTION_DAYS,
    MAX_LOG_RETENTION_DAYS,
    MIN_LOG_RETENTION_DAYS,
)


def interval_warning_message() -> str:
    return unsupported_value_message("execution_interval")


def retention_warning_message() -> str:
    return (
        f"log_retention_days must be {MIN_LOG_RETENTION_DAYS}-{MAX_LOG_RETENTION_DAYS}. "
        f"Using default {DEFAULT_LOG_RETENTION_DAYS}."
    )


def notify_errors_warning_message() -> str:
    return "Invalid notify_scraping_errors setting. Defaulting to true."
