"""User-facing strings emitted by the scraping run (stdlib only).

The single home for every note, warning, and error-detail phrase the run produces —
the application workflow's row footnotes and the HTTP status details raised by the
shared HTTP helper. The UI snapshot catalog (``tests/ui/catalog``) and the unit tests import
these same names, so a reword here is a one-place edit: the snapshot suite then
pins the final rendered text (regenerate with ``UPDATE_SNAPSHOTS=1``).

Import-light on purpose (no Rich, no transport libraries): the UI catalog and any
test can import this module without a plugin's dependency stack. Fixed strings are
UPPER_CASE constants; parametrized strings are functions. Presentation-only text
(panel titles, spinner labels) stays in ``tui/`` — it already lives in exactly one
production place and is pinned by the snapshots.
"""

# --- Success-row notes (application execution) ---------------------------------------

NOTE_NOTIFIED_OK = "Notification delivered to all valid apprise URL(s)."
NOTE_NOTIFIED_FAIL = "Notification failed; test with `./scripts/run.sh --ping`."
NOTE_NOTIFIED_NONE = "No notification sent (notifications not configured)."
NOTE_REPEATED_PRICE_ALERT_SUPPRESSED = "Repeated price alert suppressed."


def stale_note(last_checked: str, hours: int) -> str:
    """The footnote flagging an item whose last successful scrape is too old.

    Args:
        last_checked (str): The stored UTC timestamp of the last successful check.
        hours (int): The staleness threshold in hours (``OLD_ENTRY_HOURS``).

    Returns:
        str: The footnote wording.
    """
    return f"Stale: last scraped {last_checked} UTC (over {hours}h ago)."


def succeeded_on_attempt(attempt: int, max_retries: int) -> str:
    """The note recording that an item succeeded only after retries.

    Args:
        attempt (int): The 1-based attempt number that succeeded.
        max_retries (int): The configured maximum number of attempts.

    Returns:
        str: The note wording.
    """
    return f"Succeeded on attempt {attempt}/{max_retries}"


# --- Listing-type (multi-advert) success rows (application execution) ----------------

# The price-column wording for a listing check that completed fine but matched
# no advert (a normal steady state for a rare-item filter, not a failure).
ROW_NO_MATCH = "No matching advert"


def advert_matches_note(total: int, below: int) -> str:
    """The note summarizing a listing check's matches on the item row.

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


def advert_alerts_suppressed(count: int) -> str:
    """The note recording listing alerts suppressed by canonical offer URL."""
    return f"Repeated notification suppressed for {count} advert(s)."


# --- Skips, warnings, and failures (application execution) ----------------------------

NOTE_SKIP_FIELD = "Skipped by the config's `skip` setting."
WARN_STALE_NOTIFICATION_FAILED = "Failed to deliver the stale-products notification."
WARN_ERROR_NOTIFICATION_FAILED = "Failed to deliver the scraping-errors notification."
NOTE_RATE_LIMIT_ABORTED = "Rate limit reached; scraping aborted."
ERR_LOCK_HELD = "Another instance is currently running. Aborting..."


def plugin_lifecycle_failed(error_type: str) -> str:
    """Describe an unexpected target-scoped client lifecycle fault."""
    return f"Scraper target failed unexpectedly ({error_type})."


def skipping_warning(error_type: str) -> str:
    """The warning heading for a terminal, non-retryable skip error.

    Args:
        error_type (str): The exception type name (e.g. ``ProductNotFoundError``).

    Returns:
        str: The warning wording.
    """
    return f"Skipping ({error_type})"


def attempt_note(attempt: int, error_type: str) -> str:
    """The collapsed per-attempt footnote on a retried item's row.

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
    return f"See `logs/{target}/errors.txt` for details."


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
        f"Scraper '{name}' requires missing dependencies{missing_note}. "
        f"Install with `./install.sh --{name}`."
    )


def state_load_failed(target: str) -> str:
    """Describe a machine-state read failure without implying config mutation."""
    return "Scrape state could not be loaded."


def state_save_failed(target: str) -> str:
    """Describe a machine-state commit failure."""
    return "Latest scrape state was not saved."


# --- Configuration and persistence failures ------------------------------------------


def missing_config(path: str) -> str:
    return f"Create missing `{path}` from the plugin example."


def malformed_json(path: str, line: int, column: int) -> str:
    return f"Fix JSON in `{path}` at line {line}, column {column}."


def invalid_utf8(path: str) -> str:
    return f"`{path}` is not valid UTF-8."


def storage_read_permission(path: str) -> str:
    return f"Cannot read `{path}`; check its permissions."


def storage_read_failed(path: str) -> str:
    return f"Cannot read `{path}`; check the error log."


def json_object_required(path: str) -> str:
    return f"`{path}` must contain a JSON object."


def storage_save_permission(path: str) -> str:
    return f"Cannot save `{path}`; check its permissions."


def storage_save_failed(path: str) -> str:
    return f"Cannot save `{path}`; check the error log."


def invalid_state(path: str) -> str:
    return f"Fix invalid state in `{path}`; details are logged."


def unsupported_config_keys(path: str) -> str:
    return f"Remove unsupported keys from `{path}`."


def items_array_required(path: str) -> str:
    return f"`items` in `{path}` must be a JSON array."


def settings_object_required(path: str) -> str:
    return f"`settings` in `{path}` must be a JSON object."


def unsupported_settings(path: str) -> str:
    return f"Remove unsupported settings from `{path}`."


def required_settings_invalid(path: str) -> str:
    return f"Fix required settings in `{path}`."


def settings_invalid(path: str) -> str:
    return f"Fix settings in `{path}`."


def notifications_object_required(path: str) -> str:
    return f"`notifications` in `{path}` must be a JSON object."


def unsupported_notification_settings(path: str) -> str:
    return f"Remove unsupported notification settings from `{path}`."


def notification_urls_array_required(path: str) -> str:
    return f"`notifications.urls` in `{path}` must be a JSON array."


def notification_url_string_required(path: str) -> str:
    return f"A notification URL in `{path}` must be a string."


def notifications_invalid(path: str) -> str:
    return f"Fix notifications in `{path}`."


def misconfigured_items(path: str | None = None) -> str:
    if path:
        return f"Fix items in `{path}`; details are logged."
    return "Fix misconfigured items; details are logged."


DIAGNOSTIC_WRITE_FAILED = "Technical details could not be written to the error log."


# --- HTTP status details (shared HTTP helper) -----------------------------------------

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
