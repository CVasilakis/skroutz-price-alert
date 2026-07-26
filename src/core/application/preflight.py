"""The run's single target-configuration loading phase."""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from core import messages
from core.constants import EXIT_CODE_NOTIFICATION_CONFIG_ERROR
from core.exceptions import ConfigFileError
from core.general.configuration import GeneralConfigLoad
from core.infrastructure.logging import get_target_logger
from core.scrapers.api import TrackedItem
from core.scrapers.framework.configuration import RowIssue, TargetConfigLoader
from core.scrapers.framework.model import RegisteredPlugin
from core.settings import DEFAULT_LOG_RETENTION_DAYS, ResolvedSettings, resolve_settings


@dataclass(frozen=True)
class TargetConfigFailure:
    detail: str
    diagnostic: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise ValueError("load failure detail must be nonblank")
        if self.diagnostic is not None and (
            not isinstance(self.diagnostic, str) or not self.diagnostic.strip()
        ):
            raise ValueError("load failure diagnostic must be nonblank when provided")


@dataclass(frozen=True)
class TargetConfigLoad:
    plugin: RegisteredPlugin
    settings: ResolvedSettings
    items: tuple[TrackedItem, ...] = ()
    row_issues: tuple[RowIssue, ...] = ()
    row_diagnostic: str | None = None
    failure: TargetConfigFailure | None = None

    def __post_init__(self) -> None:
        if self.failure is not None and (self.items or self.row_issues):
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
    "TargetConfigFailure",
    "TargetConfigLoad",
    "load_target_configs",
    "validate_notification_preflight",
]
