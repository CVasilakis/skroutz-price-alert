from dataclasses import dataclass, replace

from core.exit_status import ExitStatus


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
_VERDICTS: dict[ExitStatus, ServiceVerdict] = {
    ExitStatus.SUCCESS: ServiceVerdict("✅", "OK", "green"),
    ExitStatus.APPLICATION_ERROR: ServiceVerdict(
        "❗",
        "Application Failed",
        "red",
        "Unexpected application failure; check `logs/errors.txt`.",
    ),
    ExitStatus.ALREADY_RUNNING: ServiceVerdict(
        "🟡", "Skipped", "yellow", "Another instance of the scraper was running."
    ),
    ExitStatus.TARGET_CONFIG_ERROR: ServiceVerdict(
        "❗", "Failed", "red", "Issue with the `config/{detail}` file."
    ),
    ExitStatus.NOTIFICATION_CONFIG_ERROR: ServiceVerdict(
        "❗",
        "Failed",
        "red",
        "Issue with notification configuration in `config/general.json`.",
    ),
    ExitStatus.RATE_LIMIT_ERROR: ServiceVerdict(
        "❗", "Failed", "red", "Blocked by server due to rate limits."
    ),
    ExitStatus.SCRAPE_ERROR: ServiceVerdict(
        "❗",
        "Scraping Failed",
        "red",
        "Retries exhausted; check `logs/{target}/output.log`.",
    ),
    ExitStatus.STORAGE_ERROR: ServiceVerdict(
        "❗",
        "Storage Failed",
        "red",
        "Machine state or its lock could not be used in `state/{target}.json` or "
        "`state/locks/{target}.lock`.",
    ),
    ExitStatus.NOTIFICATION_ERROR: ServiceVerdict(
        "🟡",
        "Notification Warning",
        "yellow",
        "A configured notification failed. Run `./scripts/run.sh --ping`.",
    ),
    ExitStatus.PLUGIN_DEPENDENCY_ERROR: ServiceVerdict(
        "❗",
        "Dependencies Missing",
        "red",
        "Install this scraper's dependencies with `./install.sh --{target}`.",
    ),
    ExitStatus.INTERRUPTED: ServiceVerdict(
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
            target-configuration-error note's ``{detail}`` placeholder.

    Returns:
        ServiceVerdict: The icon/label/color and a fully-resolved note (or None).
    """
    try:
        status = ExitStatus(int(exec_status))
    except (TypeError, ValueError):
        status = None

    if result == "success" and status is ExitStatus.SUCCESS:
        return _VERDICTS[ExitStatus.SUCCESS]

    verdict = (
        _VERDICTS.get(status) if status is not None and status is not ExitStatus.SUCCESS else None
    )
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
