"""The run's single target-configuration loading phase.

Every target's config is read exactly once per command, here, and the decoded
result is passed down. That is what lets ``status`` and ``run`` report identical
configuration health, and it removes any chance of a file changing between two
reads within one run.

A failure is data, not an exception: an unreadable config becomes a
:class:`TargetConfigLoad` carrying a failure, so the caller still has a record for
that target to report and count. Only the target that failed is affected.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from core import messages
from core.exceptions import ConfigFileError
from core.exit_status import ExitStatus
from core.general import GeneralConfigLoad
from core.infrastructure.logging import get_target_logger
from core.scrapers.api import TrackedItem
from core.scrapers.framework.configuration import RowIssue, TargetConfigLoader
from core.scrapers.framework.model import RegisteredPlugin
from core.settings import DEFAULT_LOG_RETENTION_DAYS, ResolvedSettings, resolve_settings


@dataclass(frozen=True)
class TargetConfigFailure:
    """Why one target's configuration could not be loaded at all.

    The two fields are separated by audience: :attr:`detail` is shown to the user
    in a panel, while :attr:`diagnostic` holds technical context for the error log
    and must never reach the terminal.
    """

    detail: str
    """Concise, presentation-safe reason, safe to show in a panel."""

    diagnostic: str | None = None
    """Full technical context for ``errors.txt``; never rendered to the user."""

    def __post_init__(self) -> None:
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise ValueError("load failure detail must be nonblank")
        if self.diagnostic is not None and (
            not isinstance(self.diagnostic, str) or not self.diagnostic.strip()
        ):
            raise ValueError("load failure diagnostic must be nonblank when provided")


@dataclass(frozen=True)
class TargetConfigLoad:
    """One target's configuration outcome, whether it loaded or failed.

    Enforces the invariant that a whole-file failure and decoded rows are mutually
    exclusive: a target either produced items or produced a failure. Individual bad
    *rows* are the separate, softer case — they are reported in
    :attr:`row_issues` while the remaining rows still run.
    """

    plugin: RegisteredPlugin
    """The compiled plugin this configuration belongs to."""

    settings: ResolvedSettings
    """Resolved settings; declaration defaults when the file could not be read, so
    reporting and logging still have usable values."""

    items: tuple[TrackedItem, ...] = ()
    """Successfully decoded rows, in file order. Empty when the file failed."""

    row_issues: tuple[RowIssue, ...] = ()
    """Rows that failed to decode, each with its position and reason."""

    row_diagnostic: str | None = None
    """Combined technical detail for the bad rows, destined for the error log."""

    failure: TargetConfigFailure | None = None
    """Set when the whole file was unusable, which excludes any decoded items."""

    def __post_init__(self) -> None:
        if self.failure is not None and (self.items or self.row_issues):
            raise ValueError("config failure cannot contain decoded items")

    @property
    def target(self) -> str:
        """The target name, for logs, locks, and state paths."""
        return self.plugin.target

    @property
    def count(self) -> int:
        """How many rows will actually be checked."""
        return len(self.items)

    @property
    def faulty_indices(self) -> list[int]:
        """1-based positions of the rows that were skipped."""
        return [issue.index for issue in self.row_issues]


def load_target_configs(
    plugins: Sequence[RegisteredPlugin],
    config_dir: str,
) -> list[TargetConfigLoad]:
    """Load validated plugin records without re-discovery or config re-reads."""
    results: list[TargetConfigLoad] = []
    for plugin in plugins:
        loader = TargetConfigLoader(plugin, config_dir)
        try:
            loaded = loader.load()
        except ConfigFileError as exc:
            settings = resolve_settings(plugin.setting_specs, None)
            results.append(
                TargetConfigLoad(
                    plugin,
                    settings,
                    failure=TargetConfigFailure(
                        str(exc),
                        exc.diagnostic_detail,
                    ),
                )
            )
            continue

        results.append(
            TargetConfigLoad(
                plugin=plugin,
                settings=loaded.settings,
                items=loaded.items,
                row_issues=loaded.row_issues,
                row_diagnostic=loaded.row_diagnostic,
            )
        )
    return results


def validate_notification_preflight(
    targets_to_run: Sequence[str],
    general: GeneralConfigLoad,
    retention_by_target: Mapping[str, int] | None = None,
) -> ExitStatus | None:
    """Validate notification configuration for a quiet/background run."""
    notifications = general.notifications
    if not notifications.usable:
        detail = notifications.error or "No valid notification URLs found in `config/general.json`"
        if general.diagnostic_saved is False:
            detail = f"{detail} {messages.DIAGNOSTIC_WRITE_FAILED}"
        for target in targets_to_run:
            retention = (retention_by_target or {}).get(target, DEFAULT_LOG_RETENTION_DAYS)
            get_target_logger(target, True, retention).error(
                f"❗ Notification configuration failed: {detail}"
            )
        return ExitStatus.NOTIFICATION_CONFIG_ERROR

    if notifications.invalid_urls:
        for target in targets_to_run:
            retention = (retention_by_target or {}).get(target, DEFAULT_LOG_RETENTION_DAYS)
            get_target_logger(target, True, retention).warning(
                f"❗ {len(notifications.invalid_urls)} invalid notification URL(s) "
                "detected in `config/general.json`."
            )

    return None


__all__ = [
    "TargetConfigFailure",
    "TargetConfigLoad",
    "load_target_configs",
    "validate_notification_preflight",
]
