"""The run's single target configuration and state loading phase."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from core.exceptions import ConfigFileError, StateFileError
from core.scrapers.api import TrackedItem
from core.scrapers.configuration import RowIssue, TargetConfigLoader
from core.scrapers.registry import RegisteredPlugin
from core.scrapers.state import JsonStateRepository
from core.settings import ResolvedSettings, resolve_settings


class LoadFailureKind(str, Enum):
    CONFIG = "config"
    STATE = "state"


@dataclass(frozen=True)
class LoadFailure:
    kind: LoadFailureKind
    detail: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, LoadFailureKind):
            raise TypeError("load failure kind must be CONFIG or STATE")
        if not isinstance(self.detail, str) or not self.detail.strip():
            raise ValueError("load failure detail must be nonblank")


@dataclass(frozen=True)
class TargetLoad:
    plugin: RegisteredPlugin
    settings: ResolvedSettings
    items: tuple[TrackedItem, ...] = ()
    row_issues: tuple[RowIssue, ...] = ()
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
                    failure=LoadFailure(LoadFailureKind.CONFIG, str(exc)),
                )
            )
            continue

        state = JsonStateRepository(resolved_state_dir / f"{plugin.target}.json")
        try:
            state.load()
        except StateFileError as exc:
            results.append(
                TargetLoad(
                    plugin=plugin,
                    settings=loaded.settings,
                    items=loaded.items,
                    row_issues=loaded.row_issues,
                    failure=LoadFailure(LoadFailureKind.STATE, str(exc)),
                )
            )
            continue
        results.append(
            TargetLoad(
                plugin=plugin,
                settings=loaded.settings,
                items=loaded.items,
                row_issues=loaded.row_issues,
                state=state,
            )
        )
    return results


__all__ = ["LoadFailure", "LoadFailureKind", "TargetLoad", "load_targets"]
