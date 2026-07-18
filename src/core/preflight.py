"""The run's single target configuration and state loading phase."""

from dataclasses import dataclass

from core.exceptions import ConfigFileError, StateFileError
from core.scrapers.api import TrackedItem
from core.scrapers.configuration import RowIssue, TargetConfigLoader
from core.scrapers.registry import RegisteredPlugin, ScraperRegistry
from core.scrapers.state import JsonStateRepository
from core.settings import ResolvedSettings, SettingStatus, resolve_settings


@dataclass(frozen=True)
class TargetLoad:
    target: str
    plugin: RegisteredPlugin
    settings: ResolvedSettings
    items: tuple[TrackedItem, ...] = ()
    row_issues: tuple[RowIssue, ...] = ()
    state: JsonStateRepository | None = None
    error: str | None = None
    state_error: bool = False

    @property
    def count(self) -> int:
        return len(self.items)

    @property
    def faulty_indices(self) -> list[int]:
        return [issue.index for issue in self.row_issues]


def load_targets(registry: ScraperRegistry, targets: list[str]) -> list[TargetLoad]:
    results: list[TargetLoad] = []
    for target in targets:
        try:
            plugin = registry.get_plugin(target)
        except ValueError:
            continue
        loader = TargetConfigLoader(plugin, registry.config_dir, registry.state_dir)
        try:
            loaded = loader.load()
            result = TargetLoad(
                target, plugin, loaded.settings, loaded.items,
                loaded.row_issues, loaded.state,
            )
        except ConfigFileError as exc:
            settings = resolve_settings(plugin.setting_specs, None, SettingStatus.NO_CONFIG)
            result = TargetLoad(target, plugin, settings, error=str(exc))
        except StateFileError as exc:
            # The config itself is still useful for presentation and client settings.
            try:
                settings = loader.load_settings()
            except ConfigFileError:
                settings = resolve_settings(plugin.setting_specs, None, SettingStatus.NO_CONFIG)
            result = TargetLoad(target, plugin, settings, error=str(exc), state_error=True)
        registry.prime_settings(target, result.settings)
        results.append(result)
    return results
