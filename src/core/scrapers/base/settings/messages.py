"""Single-home user-facing messages for invalid scraper settings.

Kept in one place (and short, to fit a panel footnote) so wording and bounds never
drift between the resolver, the settings panel and the silent log. The plain
"unsupported value" wording is delegated to the shared
:func:`core.settings.unsupported_value_message` helper (so ``execution_interval`` reads
the same as every other setting); ``log_retention_days`` and ``notify_scraping_errors``
keep bespoke phrasing where it carries extra detail (bounds, the specific default).
"""

from core.settings import unsupported_value_message
from core.settings.normalizers import (
    MIN_LOG_RETENTION_DAYS, MAX_LOG_RETENTION_DAYS, DEFAULT_LOG_RETENTION_DAYS,
)


def interval_warning_message() -> str:
    """The single user-facing message for an unsupported ``execution_interval``.

    Uses the shared helper with no default display: the effective default is
    plugin-specific and the settings row already shows it, so the footnote only needs to
    flag that the configured value was rejected.
    """
    return unsupported_value_message("execution_interval")


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
