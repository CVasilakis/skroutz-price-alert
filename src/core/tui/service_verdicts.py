from dataclasses import dataclass, replace

from core.constants import (
    EXIT_CODE_INTERRUPT,
    EXIT_CODE_NOTIFICATION_CONFIG_ERROR,
    EXIT_CODE_NOTIFICATION_ERROR,
    EXIT_CODE_PLUGIN_DEPENDENCY_ERROR,
    EXIT_CODE_PRODUCTS_ERROR,
    EXIT_CODE_RATE_LIMIT_ERROR,
    EXIT_CODE_SCRAPE_ERROR,
    EXIT_CODE_SKIPPED,
    EXIT_CODE_STORAGE_ERROR,
    EXIT_CODE_SUCCESS,
)


@dataclass(frozen=True)
class ServiceVerdict:
    """How a finished service run is presented in the status report.

    Attributes:
        icon: The status icon (e.g. ``"✅"``, ``"🟡"``, ``"❗"``).
        label: The short status word shown in the cell (e.g. ``"OK"``, ``"Failed"``).
        color: The Rich color applied to the label.
        note: An optional footnote; fully resolved (no placeholder) by the time a
            verdict is returned from :func:`classify_service_state`.
    """

    icon: str
    label: str
    color: str
    note: str | None = None


# Process exit code -> how the status report renders it. This is the single source of
# truth for exit-code presentation, so a new exit code is one entry here instead of
# another branch in status.py. A ``{detail}`` placeholder in a note is filled in by
# classify_service_state (e.g. with the offending config filename).
_VERDICTS: dict[int, ServiceVerdict] = {
    EXIT_CODE_SUCCESS: ServiceVerdict("✅", "OK", "green"),
    EXIT_CODE_SKIPPED: ServiceVerdict(
        "🟡", "Skipped", "yellow", "Another instance of the scraper was running."
    ),
    EXIT_CODE_PRODUCTS_ERROR: ServiceVerdict(
        "❗", "Failed", "red", "Issue with the `config/{detail}` file."
    ),
    EXIT_CODE_NOTIFICATION_CONFIG_ERROR: ServiceVerdict(
        "❗",
        "Failed",
        "red",
        "Issue with notification configuration in `config/general.json`.",
    ),
    EXIT_CODE_RATE_LIMIT_ERROR: ServiceVerdict(
        "❗", "Failed", "red", "Blocked by server due to rate limits."
    ),
    EXIT_CODE_SCRAPE_ERROR: ServiceVerdict(
        "❗",
        "Scraping Failed",
        "red",
        "A parser or unexpected scraper failure exhausted all retries. Check `logs/{target}/output.log`.",
    ),
    EXIT_CODE_STORAGE_ERROR: ServiceVerdict(
        "❗",
        "Storage Failed",
        "red",
        "Could not update `state/{target}.json` with the latest scrape state.",
    ),
    EXIT_CODE_NOTIFICATION_ERROR: ServiceVerdict(
        "🟡",
        "Notification Warning",
        "yellow",
        "At least one configured notification could not be delivered. Run `./scripts/run.sh --ping`.",
    ),
    EXIT_CODE_PLUGIN_DEPENDENCY_ERROR: ServiceVerdict(
        "❗",
        "Dependencies Missing",
        "red",
        "Install this scraper's dependencies with `./install.sh --{target}`.",
    ),
    EXIT_CODE_INTERRUPT: ServiceVerdict(
        "🟡", "Interrupted", "yellow", "Process was terminated by the user or system."
    ),
}

# Fallback for an exit code not in the table; the note carries the raw reason/code.
_UNKNOWN_VERDICT = ServiceVerdict("❗", "Failed", "red", "{detail}")


def classify_service_state(
    result: str, exec_status: str, target: str, config_filename: str
) -> ServiceVerdict:
    """Maps a finished service's systemd outcome to a presentation verdict.

    A run counts as fully successful only when systemd reports ``Result=success``
    together with a zero exit code; any other exit code is looked up in the verdict
    table, and an unrecognized code falls back to a generic failure carrying the raw
    reason and code.

    Args:
        result (str): The systemd ``Result`` property (e.g. ``"success"``).
        exec_status (str): The process exit code as a string (``ExecMainStatus``).
        target (str): The scraper target, used in actionable paths and commands.
        config_filename (str): The target's config filename, used to fill the
            products-error note's ``{detail}`` placeholder.

    Returns:
        ServiceVerdict: The icon/label/color and a fully-resolved note (or None).
    """
    if result == "success" and exec_status == str(EXIT_CODE_SUCCESS):
        return _VERDICTS[EXIT_CODE_SUCCESS]

    try:
        code = int(exec_status)
    except (TypeError, ValueError):
        code = None

    verdict = _VERDICTS.get(code) if code is not None and code != EXIT_CODE_SUCCESS else None
    if verdict is None:
        detail = f"Reason: {result or 'Unknown'}, Exit Code: {exec_status or 'Unknown'}"
        return replace(_UNKNOWN_VERDICT, note=detail)

    if verdict.note:
        return replace(
            verdict,
            note=verdict.note.format(
                detail=config_filename,
                target=target,
            ),
        )
    return verdict
