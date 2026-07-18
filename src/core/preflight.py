"""The run's single target configuration and state loading phase."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from core.exceptions import ConfigFileError, StateFileError
from core.scrapers.api import TrackedItem
from core.scrapers.configuration import RowIssue, TargetConfigLoader
from core.scrapers.registry import RegisteredPlugin
from core.scrapers.state import JsonStateRepository
from core.settings import ResolvedSettings, SettingStatus, resolve_settings


@dataclass(frozen=True)
class TargetLoad:
    plugin: RegisteredPlugin
    settings: ResolvedSettings
    items: tuple[TrackedItem, ...] = ()
    row_issues: tuple[RowIssue, ...] = ()
    state: JsonStateRepository | None = None
    error: str | None = None
    state_error: bool = False

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
            settings = resolve_settings(plugin.setting_specs, None, SettingStatus.NO_CONFIG)
            results.append(TargetLoad(plugin, settings, error=str(exc)))
            continue

        state = JsonStateRepository(resolved_state_dir / f"{plugin.target}.json")
        try:
            state.load()
        except StateFileError as exc:
            results.append(TargetLoad(
                plugin,
                loaded.settings,
                loaded.items,
                loaded.row_issues,
                error=str(exc),
                state_error=True,
            ))
            continue
        results.append(TargetLoad(
            plugin,
            loaded.settings,
            loaded.items,
            loaded.row_issues,
            state,
        ))
    return results
