"""The run's single target configuration and state loading phase."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core import messages
from core.constants import EXIT_CODE_NOTIFICATION_CONFIG_ERROR
from core.exceptions import ConfigFileError, StateFileError
from core.general.configuration import GeneralConfigLoad
from core.infrastructure.logging import get_target_logger
from core.scrapers.api import TrackedItem
from core.scrapers.framework.configuration import RowIssue, TargetConfigLoader
from core.scrapers.framework.model import RegisteredPlugin
from core.scrapers.framework.state import JsonStateRepository
from core.settings import DEFAULT_LOG_RETENTION_DAYS, ResolvedSettings, resolve_settings


class LoadFailureKind(str, Enum):
    CONFIG = "config"
    STATE = "state"


@dataclass(frozen=True)
class LoadFailure:
    kind: LoadFailureKind
    detail: str
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LoadFailureKind):
            raise TypeError("load failure kind must be CONFIG or STATE")
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise ValueError("load failure detail must be nonblank")
        if self.diagnostic is not None and (
            not isinstance(self.diagnostic, str) or not self.diagnostic.strip()
        ):
            raise ValueError("load failure diagnostic must be nonblank when provided")


@dataclass(frozen=True)
class TargetLoad:
    plugin: RegisteredPlugin
    settings: ResolvedSettings
    items: tuple[TrackedItem, ...] = ()
    row_issues: tuple[RowIssue, ...] = ()
    row_diagnostic: str | None = None
    state: JsonStateRepository | None = None
    failure: LoadFailure | None = None

    def __post_init__(self) -> None:
        if self.failure is None and self.state is None:
            raise ValueError("successful target load requires state")
        if self.failure is not None and self.state is not None:
            raise ValueError("failed target load cannot contain state")
        if self.failure is not None and self.failure.kind is LoadFailureKind.CONFIG:
            if self.items or self.row_issues:
                raise ValueError("config failure cannot contain decoded items")

    @property
    def target(self) -> str:
        return self.plugin.target

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def faulty_indices(self) -> list[int]:
        return [issue.index for issue in self.row_issues]


def load_targets(
    plugins: Sequence[RegisteredPlugin],
    config_dir: str,
    state_dir: str | None = None,
) -> list[TargetLoad]:
    """Load validated plugin records without re-discovery or config re-reads."""
    resolved_state_dir = (
        Path(state_dir) if state_dir is not None else Path(config_dir).resolve().parent / "state"
    )
    results: list[TargetLoad] = []
    for plugin in plugins:
        loader = TargetConfigLoader(plugin, config_dir)
        try:
            loaded = loader.load()
        except ConfigFileError as exc:
            settings = resolve_settings(plugin.setting_specs, None)
            results.append(
                TargetLoad(
                    plugin,
                    settings,
                    failure=LoadFailure(
                        LoadFailureKind.CONFIG,
                        str(exc),
                        exc.diagnostic_detail,
                    ),
                )
            )
            continue

        state = JsonStateRepository(
            resolved_state_dir / f"{plugin.target}.json",
            display_path=f"state/{plugin.target}.json",
        )
        try:
            state.load()
        except StateFileError as exc:
            results.append(
                TargetLoad(
                    plugin=plugin,
                    settings=loaded.settings,
                    items=loaded.items,
                    row_issues=loaded.row_issues,
                    row_diagnostic=loaded.row_diagnostic,
                    failure=LoadFailure(
                        LoadFailureKind.STATE,
                        str(exc),
                        exc.diagnostic_detail,
                    ),
                )
            )
            continue
        results.append(
            TargetLoad(
                plugin=plugin,
                settings=loaded.settings,
                items=loaded.items,
                row_issues=loaded.row_issues,
                row_diagnostic=loaded.row_diagnostic,
                state=state,
            )
        )
    return results


def validate_notification_preflight(
    targets_to_run: Sequence[str],
    general: GeneralConfigLoad,
    retention_by_target: Mapping[str, int] | None = None,
) -> int | None:
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
        logging.critical(f"Notification configuration failed: {detail}")
        return EXIT_CODE_NOTIFICATION_CONFIG_ERROR

    if notifications.invalid_urls:
        for target in targets_to_run:
            retention = (retention_by_target or {}).get(target, DEFAULT_LOG_RETENTION_DAYS)
            get_target_logger(target, True, retention).warning(
                f"❗ {len(notifications.invalid_urls)} invalid notification URL(s) "
                "detected in `config/general.json`."
            )

    return None


__all__ = [
    "LoadFailure",
    "LoadFailureKind",
    "TargetLoad",
    "load_targets",
    "validate_notification_preflight",
]
