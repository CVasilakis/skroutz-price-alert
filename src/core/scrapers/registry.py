"""Discovery, validation, and lazy construction for scraper plugins."""

from __future__ import annotations

import importlib
import os
import pkgutil
import re
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from core import messages
from core.exceptions import (
    PluginDependencyError,
    PluginDiscoveryError,
    PluginValidationError,
)
from core.scrapers.base.plugin import ClassRef, PluginDefinition, RegisteredPlugin
from core.scrapers.base.settings import (
    BASE_SETTING_SPECS,
    KEY_INTERVAL,
    STATUS_OK,
    SUPPORTED_INTERVALS,
    ResolvedSetting,
    ResolvedSettings,
    SettingSpec,
    oncalendar_for,
    resolve_all,
    resolve_one,
)
from core.scrapers.base.url import normalize_domain

if TYPE_CHECKING:
    from core.scrapers.base.client import BaseScraperClient
    from core.scrapers.base.storage import BaseDataManager


# These names are already claimed by management flags or project-wide config.
RESERVED_PLUGIN_NAMES = frozenset({"general", "help", "quiet", "ping", "status", "update"})


@dataclass(frozen=True)
class ScheduleResolution:
    """One plugin's effective framework-owned timer schedule."""

    on_calendar: str
    status: str


class ScraperRegistry:
    """Discover plugins and provide validated metadata and lazy instances."""

    _plugins: dict[str, RegisteredPlugin] = {}
    _discovered = False

    @classmethod
    def register(
        cls,
        definition: PluginDefinition,
        *,
        target: str,
        package: str | None = None,
        source_dir: str | os.PathLike[str] | None = None,
        where: str | None = None,
    ) -> None:
        """Validate and register a definition under its package-derived target."""
        package = package or f"core.scrapers.{target}"
        where = where or package
        if not isinstance(definition, PluginDefinition):
            raise PluginValidationError(
                f"Plugin '{where}' must export a PluginDefinition as PLUGIN, got "
                f"{type(definition).__name__}."
            )
        record = cls._validate_definition(
            target, package, definition, source_dir=source_dir, where=where
        )
        if record.target in cls._plugins:
            raise PluginValidationError(
                f"Duplicate plugin target '{record.target}' from '{where}'."
            )
        cls._check_domain_conflicts_for(record)
        cls._plugins[record.target] = record

    @classmethod
    def _reset(cls) -> None:
        """Clear registry state for isolated tests."""
        cls._plugins = {}
        cls._discovered = False

    @classmethod
    def discover(cls) -> None:
        """Discover every scraper package and import only its descriptor module."""
        if cls._discovered:
            return

        package_dir = Path(__file__).parent
        original = dict(cls._plugins)
        try:
            for _importer, target, ispkg in pkgutil.iter_modules([str(package_dir)]):
                if not ispkg or target == "base" or target.startswith("_"):
                    continue

                package = f"core.scrapers.{target}"
                module_name = f"{package}.plugin"
                try:
                    module = importlib.import_module(module_name)
                except Exception as exc:
                    raise PluginDiscoveryError(
                        f"Failed to import scraper descriptor '{module_name}': {exc}"
                    ) from exc

                if not hasattr(module, "PLUGIN"):
                    raise PluginDiscoveryError(
                        f"Scraper descriptor '{module_name}' does not export PLUGIN."
                    )
                cls.register(
                    getattr(module, "PLUGIN"),
                    target=target,
                    package=package,
                    source_dir=package_dir / target,
                    where=module_name,
                )
        except Exception:
            # Discovery is atomic: a broken plugin must not leave a partially populated
            # registry whose next lookup fails for a different (duplicate) reason.
            cls._plugins = original
            raise

        cls._discovered = True

    @classmethod
    def _validate_definition(
        cls,
        target: str,
        package: str,
        definition: PluginDefinition,
        *,
        source_dir: str | os.PathLike[str] | None,
        where: str,
    ) -> RegisteredPlugin:
        """Normalize a definition and reject unsafe or ambiguous metadata."""
        if not isinstance(target, str) or re.fullmatch(r"[a-z][a-z0-9_]*", target) is None:
            raise PluginValidationError(
                f"Plugin package '{target}' must be a lowercase Python-style identifier "
                "containing letters, digits, and underscores and starting with a letter."
            )
        if target in RESERVED_PLUGIN_NAMES:
            raise PluginValidationError(
                f"Plugin package name '{target}' is reserved by the framework."
            )
        if not isinstance(definition.display_name, str) or not definition.display_name.strip():
            raise PluginValidationError(
                f"Plugin '{target}' ({where}) must declare a nonblank display_name."
            )

        if not isinstance(definition.domains, tuple) or not definition.domains:
            raise PluginValidationError(
                f"Plugin '{target}' ({where}) must declare a non-empty domains tuple."
            )
        domains: list[str] = []
        for raw_domain in definition.domains:
            try:
                domain = normalize_domain(raw_domain)
            except ValueError as exc:
                raise PluginValidationError(
                    f"Plugin '{target}' ({where}) declares invalid domain {raw_domain!r}: {exc}."
                ) from exc
            if domain in domains:
                raise PluginValidationError(
                    f"Plugin '{target}' ({where}) declares duplicate normalized domain '{domain}'."
                )
            domains.append(domain)

        cls._validate_class_ref(target, "client", definition.client)
        cls._validate_class_ref(target, "storage", definition.storage)

        if definition.default_interval not in SUPPORTED_INTERVALS:
            raise PluginValidationError(
                f"Plugin '{target}' ({where}) default_interval must be one of "
                f"{sorted(SUPPORTED_INTERVALS)} (got {definition.default_interval!r})."
            )
        if not isinstance(definition.setting_specs, tuple):
            raise PluginValidationError(
                f"Plugin '{target}' ({where}) setting_specs must be a tuple of SettingSpec."
            )

        specs = (*BASE_SETTING_SPECS, *definition.setting_specs)
        cls._validate_setting_specs(target, where, specs)

        resolved_source = Path(source_dir).resolve() if source_dir is not None else None
        source_text = str(resolved_source) if resolved_source is not None else ""
        requirements = resolved_source / "requirements.txt" if resolved_source is not None else None
        example = resolved_source / "config.example.json" if resolved_source is not None else None
        record = RegisteredPlugin(
            target=target,
            display_name=definition.display_name.strip(),
            domains=tuple(domains),
            client=definition.client,
            storage=definition.storage,
            default_interval=definition.default_interval,
            setting_specs=tuple(specs),
            package=package,
            source_dir=source_text,
            example_config_path=str(example) if example is not None else "",
            requirements_path=(str(requirements) if requirements is not None and requirements.is_file() else None),
        )

        for spec in specs:
            try:
                displayed = spec.display(spec.default_for(record))
            except Exception as exc:
                raise PluginValidationError(
                    f"Plugin '{target}' setting '{spec.key}' default/display failed: {exc}"
                ) from exc
            if not isinstance(displayed, str):
                raise PluginValidationError(
                    f"Plugin '{target}' setting '{spec.key}' display(default) must return a string."
                )
        return record

    @staticmethod
    def _validate_class_ref(target: str, label: str, ref: object) -> None:
        if not isinstance(ref, ClassRef):
            raise PluginValidationError(
                f"Plugin '{target}' {label} must be a ClassRef."
            )
        module_pattern = r"\.?[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"
        if re.fullmatch(module_pattern, ref.module) is None:
            raise PluginValidationError(
                f"Plugin '{target}' {label} ClassRef has invalid module {ref.module!r}."
            )
        if not isinstance(ref.symbol, str) or not ref.symbol.isidentifier():
            raise PluginValidationError(
                f"Plugin '{target}' {label} ClassRef has invalid symbol {ref.symbol!r}."
            )

    @staticmethod
    def _validate_setting_specs(target: str, where: str, specs: tuple[SettingSpec, ...]) -> None:
        seen: set[str] = set()
        for spec in specs:
            if not isinstance(spec, SettingSpec):
                raise PluginValidationError(
                    f"Plugin '{target}' ({where}) setting_specs must contain only SettingSpec values."
                )
            if not isinstance(spec.key, str) or not spec.key.strip():
                raise PluginValidationError(
                    f"Plugin '{target}' ({where}) has a setting with a blank key."
                )
            if spec.key in seen:
                raise PluginValidationError(
                    f"Plugin '{target}' ({where}) declares duplicate setting key '{spec.key}'."
                )
            seen.add(spec.key)
            if not isinstance(spec.label, str) or not spec.label.strip():
                raise PluginValidationError(
                    f"Plugin '{target}' setting '{spec.key}' must have a nonblank label."
                )
            if not isinstance(spec.warning, str) or not spec.warning.strip():
                raise PluginValidationError(
                    f"Plugin '{target}' setting '{spec.key}' must have a nonblank warning."
                )
            for field in ("normalize", "display", "is_unset"):
                if not callable(getattr(spec, field)):
                    raise PluginValidationError(
                        f"Plugin '{target}' setting '{spec.key}' field '{field}' must be callable."
                    )
            if spec.default_factory is not None and not callable(spec.default_factory):
                raise PluginValidationError(
                    f"Plugin '{target}' setting '{spec.key}' default_factory must be callable or None."
                )

    @staticmethod
    def _domains_overlap(first: str, second: str) -> bool:
        return (
            first == second
            or first.endswith("." + second)
            or second.endswith("." + first)
        )

    @classmethod
    def _check_domain_conflicts_for(cls, plugin: RegisteredPlugin) -> None:
        for domain in plugin.domains:
            for owner, registered in cls._plugins.items():
                for existing in registered.domains:
                    if cls._domains_overlap(domain, existing):
                        raise PluginValidationError(
                            f"Domain conflict: plugin '{plugin.target}' claims '{domain}', which "
                            f"overlaps with '{existing}' claimed by plugin '{owner}'."
                        )

    @classmethod
    def registered_targets(cls) -> list[str]:
        cls.discover()
        return list(cls._plugins)

    @classmethod
    def get_plugin(cls, target: str) -> RegisteredPlugin:
        cls.discover()
        try:
            return cls._plugins[target]
        except KeyError as exc:
            raise ValueError(f"Unsupported plugin: {target}") from exc

    @staticmethod
    def _config_path(plugin: RegisteredPlugin, config_dir: str) -> str:
        return os.path.join(config_dir, plugin.config_filename)

    @staticmethod
    def _spec_for(plugin: RegisteredPlugin, key: str) -> SettingSpec:
        for spec in plugin.setting_specs:
            if spec.key == key:
                return spec
        raise KeyError(f"Plugin '{plugin.target}' exposes no setting '{key}'.")

    @classmethod
    def resolve_all_settings(cls, target: str, config_dir: str) -> ResolvedSettings:
        plugin = cls.get_plugin(target)
        return resolve_all(plugin.setting_specs, cls._config_path(plugin, config_dir), plugin)

    @classmethod
    def resolve_value(cls, target: str, key: str, config_dir: str) -> ResolvedSetting:
        plugin = cls.get_plugin(target)
        return resolve_one(cls._spec_for(plugin, key), cls._config_path(plugin, config_dir), plugin)

    @staticmethod
    def expected_on_calendar(plugin: RegisteredPlugin, interval: ResolvedSetting) -> str:
        canonical = interval.value if interval.status == STATUS_OK else plugin.default_interval
        return oncalendar_for(canonical)

    @classmethod
    def resolve_schedule(cls, target: str, config_dir: str) -> ScheduleResolution:
        plugin = cls.get_plugin(target)
        interval = cls.resolve_value(target, KEY_INTERVAL, config_dir)
        return ScheduleResolution(
            on_calendar=cls.expected_on_calendar(plugin, interval),
            status=interval.status,
        )

    @classmethod
    def plugin_for_url(cls, url: str) -> RegisteredPlugin | None:
        cls.discover()
        return next((plugin for plugin in cls._plugins.values() if plugin.matches_url(url)), None)

    @classmethod
    def _resolve_bound_class(cls, plugin: RegisteredPlugin, ref: ClassRef, base: type) -> type:
        try:
            module = importlib.import_module(ref.module, package=plugin.package)
        except ImportError as exc:
            missing = getattr(exc, "name", None)
            raise PluginDependencyError(
                messages.plugin_dependency_detail(plugin.target, missing)
            ) from exc
        try:
            bound_class = getattr(module, ref.symbol)
        except AttributeError as exc:
            raise PluginValidationError(
                f"Plugin '{plugin.target}' binding {ref.module}:{ref.symbol} does not exist."
            ) from exc
        if not isinstance(bound_class, type) or not issubclass(bound_class, base):
            raise PluginValidationError(
                f"Plugin '{plugin.target}' binding {ref.module}:{ref.symbol} must be a "
                f"{base.__name__} subclass, got {bound_class!r}."
            )
        return bound_class

    def __init__(self, config_dir: str):
        self._clients: dict[str, BaseScraperClient] = {}
        self._managers: dict[str, BaseDataManager] = {}
        self._settings: dict[str, ResolvedSettings] = {}
        self.config_dir = config_dir

    def settings_for(self, target: str) -> ResolvedSettings:
        if target not in self._settings:
            self._settings[target] = self.resolve_all_settings(target, self.config_dir)
        return self._settings[target]

    def get_client(self, target: str) -> BaseScraperClient:
        if target not in self._clients:
            from core.scrapers.base.client import BaseScraperClient

            plugin = self.get_plugin(target)
            client_class = self._resolve_bound_class(plugin, plugin.client, BaseScraperClient)
            self._clients[target] = client_class(settings=self.settings_for(target))
        return self._clients[target]

    def get_manager(self, target: str) -> BaseDataManager:
        if target not in self._managers:
            from core.scrapers.base.storage import BaseDataManager

            plugin = self.get_plugin(target)
            storage_class = self._resolve_bound_class(plugin, plugin.storage, BaseDataManager)
            path = self._config_path(plugin, self.config_dir)
            self._managers[target] = storage_class(path, plugin, self.settings_for(target))
        return self._managers[target]

    def close_all(self) -> None:
        for client in self._clients.values():
            client.close()
