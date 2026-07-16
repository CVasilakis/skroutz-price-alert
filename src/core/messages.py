"""User-facing strings emitted by the scraping run (stdlib only).

The single home for every note, warning, and error-detail phrase the run produces —
the orchestrator's row footnotes and the HTTP status details raised by the base
client. The UI snapshot catalog (``tests/ui/catalog``) and the unit tests import
these same names, so a reword here is a one-place edit: the snapshot suite then
pins the final rendered text (regenerate with ``UPDATE_SNAPSHOTS=1``).

Import-light on purpose (no Rich, no transport libraries): the UI catalog and any
test can import this module without a plugin's dependency stack. Fixed strings are
UPPER_CASE constants; parametrized strings are functions. Presentation-only text
(panel titles, spinner labels) stays in ``ui/`` — it already lives in exactly one
production place and is pinned by the snapshots.
"""

# --- Success-row notes (orchestrator) ------------------------------------------------

NOTE_NOTIFIED_OK = "Notification delivered to all valid apprise URL(s)."
NOTE_NOTIFIED_FAIL = "Notification delivery failed for some apprise URL(s)."
NOTE_NOTIFIED_NONE = "No notification sent (.env not configured)."
NOTE_CORRUPTED_TIMESTAMP = "Corrupted timestamp! Updated to current time."


def stale_note(last_checked: str, hours: int) -> str:
    """The footnote flagging a product whose last successful scrape is too old.

    Args:
        last_checked (str): The stored UTC timestamp of the last successful check.
        hours (int): The staleness threshold in hours (``OLD_ENTRY_HOURS``).

    Returns:
        str: The footnote wording.
    """
    return f"Stale: last scraped {last_checked} UTC (over {hours}h ago)."


def succeeded_on_attempt(attempt: int, max_retries: int) -> str:
    """The note recording that a product succeeded only after retries.

    Args:
        attempt (int): The 1-based attempt number that succeeded.
        max_retries (int): The configured maximum number of attempts.

    Returns:
        str: The note wording.
    """
    return f"Succeeded on attempt {attempt}/{max_retries}"


def invalid_target_price(raw: object, currency: str) -> str:
    """The note surfacing an unparseable ``target_price`` config value.

    Args:
        raw: The raw config value that failed to parse (truncated for display).
        currency (str): The currency symbol of the scraped price.

    Returns:
        str: The note wording.
    """
    return f"Invalid target price '{str(raw)[:15]}'. Defaulting to 0.0 {currency}"


def missing_target_price(currency: str) -> str:
    """The note surfacing a config row with no ``target_price`` field at all.

    Args:
        currency (str): The currency symbol of the scraped price.

    Returns:
        str: The note wording.
    """
    return f"Missing target price. Defaulting to 0.0 {currency}"


# --- Listing-type (multi-advert) success rows (orchestrator) --------------------------

# The price-column wording for a listing check that completed fine but matched
# no advert (a normal steady state for a rare-item filter, not a failure).
ROW_NO_MATCH = "No matching advert"


def advert_matches_note(total: int, below: int) -> str:
    """The note summarizing a listing check's matches on the product row.

    Args:
        total (int): How many adverts survived the row's filters.
        below (int): How many of them are priced below the target.

    Returns:
        str: The note wording.
    """
    return f"{total} advert(s) matched the filters; {below} below target."


def advert_notified_ok(count: int) -> str:
    """The note recording that every per-advert notification was delivered.

    Args:
        count (int): How many adverts were notified about.

    Returns:
        str: The note wording.
    """
    return f"Notification delivered for {count} advert(s)."


def advert_notified_fail(failed: int, total: int) -> str:
    """The note recording that some per-advert notifications failed to deliver.

    Args:
        failed (int): How many notifications failed.
        total (int): How many adverts were notified about.

    Returns:
        str: The note wording.
    """
    return f"Notification delivery failed for {failed} of {total} advert(s)."


# --- Skips, warnings, and failures (orchestrator) -------------------------------------

NOTE_SKIP_FIELD = "The skip field was set to true in the configuration file."
WARN_INVALID_URL = "Invalid URL. Skipping product..."
WARN_STALE_NOTIFICATION_FAILED = "Failed to deliver the stale-products notification."
WARN_ERROR_NOTIFICATION_FAILED = "Failed to deliver the scraping-errors notification."
NOTE_RATE_LIMIT_ABORTED = "Rate limit reached; scraping aborted."
ERR_LOCK_HELD = "Another instance is currently running. Aborting..."


def skipping_warning(error_type: str) -> str:
    """The warning heading for a terminal, non-retryable skip error.

    Args:
        error_type (str): The exception type name (e.g. ``ProductNotFoundError``).

    Returns:
        str: The warning wording.
    """
    return f"Skipping ({error_type})"


def attempt_note(attempt: int, error_type: str) -> str:
    """The collapsed per-attempt footnote on a retried product's row.

    Args:
        attempt (int): The 1-based attempt number that failed.
        error_type (str): The exception type name of that attempt.

    Returns:
        str: The footnote wording.
    """
    return f"Attempt {attempt}: {error_type}"


def errors_log_pointer(target: str) -> str:
    """The footnote pointing at a target's error log.

    Args:
        target (str): The scraper target name (its logs subdirectory).

    Returns:
        str: The footnote wording.
    """
    return f"See logs/{target}/errors.txt for details."


def plugin_dependency_detail(name: str, missing: str | None = None) -> str:
    """The error detail for a plugin whose deferred dependencies are not installed.

    Args:
        name (str): The plugin name (also its ``./install.sh`` flag).
        missing (str | None): The unimportable module name, when known.

    Returns:
        str: The error wording.
    """
    missing_note = f" (missing module: {missing})" if missing else ""
    return (
        f"Scraper '{name}' requires dependencies that are not installed{missing_note}. "
        f"Install them with: ./install.sh --{name}"
    )


def save_failed(config_filename: str) -> str:
    """The error message for a config file that could not be written back.

    Args:
        config_filename (str): The plugin's config filename (e.g. ``skroutz.json``).

    Returns:
        str: The error wording.
    """
    return f"Failed to update config/{config_filename} file!"


# --- HTTP status details (base HTTP client) -------------------------------------------

EMPTY_RESPONSE_DETAIL = "Empty response or no status code received from server"


def not_found_detail(status_code: int) -> str:
    """The detail for a removed/not-found HTTP status (default 404, 410)."""
    return f"Product not found or removed (HTTP {status_code})."


def rate_limited_detail(status_code: int) -> str:
    """The detail for a blocked/rate-limited HTTP status (default 401, 403, 429)."""
    return f"Blocked or rate limited (HTTP {status_code})"


def server_error_detail(status_code: int) -> str:
    """The detail for a transient 5xx server-side HTTP status."""
    return f"Server error (HTTP {status_code}), retrying..."


def http_failed_detail(status_code: int) -> str:
    """The detail for any other unexpected HTTP status."""
    return f"HTTP request failed with status code {status_code}"
