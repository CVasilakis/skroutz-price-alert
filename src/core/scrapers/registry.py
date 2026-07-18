"""Atomic plugin discovery, strict validation, routing, and lazy clients."""

from __future__ import annotations

import importlib
import os
import pkgutil
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from core import messages
from core.exceptions import PluginDependencyError, PluginDiscoveryError, PluginValidationError, StorageFileError
from core.scrapers.api import ItemField, ScraperClient, ScraperPlugin, SettingSpec
from core.scrapers.configuration import TargetConfigLoader
from core.scrapers.settings import (
    KEY_INTERVAL, SUPPORTED_INTERVALS, framework_setting_specs, oncalendar_for,
)
from core.scrapers.url import normalize_domain, parsed_matches_domains, parse_url
from core.settings import ResolvedSetting, ResolvedSettings, SettingStatus, resolve_settings

RESERVED_PLUGIN_NAMES = frozenset({"general", "help", "quiet", "ping", "status", "update"})
RESERVED_ITEM_KEYS = frozenset({"id", "name", "url", "target_price", "skip", "metadata"})


@dataclass(frozen=True)
class RegisteredPlugin:
    target: str
    display_name: str
    domains: tuple[str, ...]
    client: str
    accepts_url: Any
    item_fields: tuple[ItemField[Any], ...]
    setting_specs: tuple[SettingSpec[Any], ...]
    custom_setting_specs: tuple[SettingSpec[Any], ...]
    default_interval: str
    package: str
    source_dir: str
    example_config_path: str
    requirements_path: str | None

    @property
    def config_filename(self) -> str:
        return f"{self.target}.json"

    def matches_url(self, value: object) -> bool:
        try:
            parsed = parse_url(value)
            return parsed_matches_domains(parsed, self.domains) and self.accepts_url(parsed) is True
        except Exception:
            return False

    def setting(self, key: str) -> SettingSpec[Any]:
        """Return one framework-validated declaration for internal boundaries."""
        return next(spec for spec in self.setting_specs if spec.key == key)


@dataclass(frozen=True)
class ScheduleResolution:
    on_calendar: str
    status: SettingStatus


class ScraperRegistry:
    _plugins: dict[str, RegisteredPlugin] = {}
    _discovered = False

    @classmethod
    def _reset(cls) -> None:
        cls._plugins = {}
        cls._discovered = False

    @classmethod
    def discover(cls) -> None:
        if cls._discovered:
            return
        package_dir = Path(__file__).parent
        original = dict(cls._plugins)
        try:
            for _importer, target, ispkg in pkgutil.iter_modules([str(package_dir)]):
                if not ispkg or target in {"base"} or target.startswith("_"):
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
                    module.PLUGIN, target=target, package=package,
                    source_dir=package_dir / target, where=module_name,
                )
        except Exception:
            cls._plugins = original
            raise
        cls._discovered = True

    @classmethod
    def register(cls, definition: ScraperPlugin, *, target: str,
                 package: str | None = None,
                 source_dir: str | os.PathLike[str] | None = None,
                 where: str | None = None) -> None:
        package = package or f"core.scrapers.{target}"
        where = where or package
        if not isinstance(definition, ScraperPlugin):
            raise PluginValidationError(
                f"Plugin '{where}' must export a ScraperPlugin as PLUGIN."
            )
        record = cls._validate(target, package, definition, source_dir, where)
        if target in cls._plugins:
            raise PluginValidationError(f"Duplicate plugin target '{target}'.")
        cls._check_domain_conflicts(record)
        cls._plugins[target] = record

    @classmethod
    def _validate(cls, target: str, package: str, definition: ScraperPlugin,
                  source_dir: str | os.PathLike[str] | None,
                  where: str) -> RegisteredPlugin:
        if re.fullmatch(r"[a-z][a-z0-9_]*", target) is None or target in RESERVED_PLUGIN_NAMES:
            raise PluginValidationError(f"Plugin package name '{target}' is invalid or reserved.")
        if not isinstance(definition.display_name, str) or not definition.display_name.strip():
            raise PluginValidationError(f"Plugin '{target}' must declare a nonblank display_name.")
        if not isinstance(definition.domains, tuple) or not definition.domains:
            raise PluginValidationError(f"Plugin '{target}' domains must be a non-empty tuple.")
        domains: list[str] = []
        for raw in definition.domains:
            try:
                domain = normalize_domain(raw)
            except ValueError as exc:
                raise PluginValidationError(f"Plugin '{target}' domain {raw!r}: {exc}.") from exc
            if domain in domains:
                raise PluginValidationError(f"Plugin '{target}' repeats domain '{domain}'.")
            domains.append(domain)
        if not callable(definition.accepts_url):
            raise PluginValidationError(f"Plugin '{target}' accepts_url must be callable.")
        for domain in domains:
            try:
                probe = definition.accepts_url(urlsplit(f"https://{domain}/"))
            except Exception as exc:
                raise PluginValidationError(f"Plugin '{target}' accepts_url probe failed: {exc}") from exc
            if not isinstance(probe, bool):
                raise PluginValidationError(f"Plugin '{target}' accepts_url must return bool.")
        if not isinstance(definition.client, str) or re.fullmatch(
            r"\.[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*:[A-Za-z_]\w*", definition.client
        ) is None:
            raise PluginValidationError(
                f"Plugin '{target}' client must be a relative '.module:Symbol' binding."
            )
        if definition.default_interval not in SUPPORTED_INTERVALS:
            raise PluginValidationError(
                f"Plugin '{target}' default_interval must be one of {sorted(SUPPORTED_INTERVALS)}."
            )
        if not isinstance(definition.item_fields, tuple):
            raise PluginValidationError(f"Plugin '{target}' item_fields must be a tuple.")
        if not isinstance(definition.settings, tuple):
            raise PluginValidationError(f"Plugin '{target}' settings must be a tuple.")
        cls._validate_fields(target, definition.item_fields)
        framework = framework_setting_specs(definition.default_interval)
        specs = (*framework, *definition.settings)
        cls._validate_settings(target, specs)
        source = Path(source_dir).resolve() if source_dir is not None else None
        requirements = source / "requirements.txt" if source else None
        return RegisteredPlugin(
            target=target, display_name=definition.display_name.strip(), domains=tuple(domains),
            client=definition.client, accepts_url=definition.accepts_url,
            item_fields=definition.item_fields, setting_specs=tuple(specs),
            custom_setting_specs=definition.settings,
            default_interval=definition.default_interval, package=package,
            source_dir=str(source) if source else "",
            example_config_path=str(source / "config.example.json") if source else "",
            requirements_path=str(requirements) if requirements and requirements.is_file() else None,
        )

    @staticmethod
    def _validate_fields(target: str, fields: tuple[ItemField[Any], ...]) -> None:
        seen: set[str] = set()
        for field in fields:
            if not isinstance(field, ItemField):
                raise PluginValidationError(f"Plugin '{target}' item_fields contains a non-ItemField.")
            if not isinstance(field.key, str) or not field.key.strip() or field.key in RESERVED_ITEM_KEYS:
                raise PluginValidationError(f"Plugin '{target}' item field key {field.key!r} is invalid or reserved.")
            if field.key in seen:
                raise PluginValidationError(f"Plugin '{target}' duplicates item field '{field.key}'.")
            seen.add(field.key)
            if not callable(field.decode):
                raise PluginValidationError(f"Plugin '{target}' item field '{field.key}' decoder is not callable.")
            try:
                field.decode(field.default)
            except Exception as exc:
                raise PluginValidationError(f"Plugin '{target}' item field '{field.key}' default failed: {exc}") from exc

    @staticmethod
    def _validate_settings(target: str, specs: tuple[SettingSpec[Any], ...]) -> None:
        seen: set[str] = set()
        for spec in specs:
            if not isinstance(spec, SettingSpec):
                raise PluginValidationError(f"Plugin '{target}' settings contains a non-SettingSpec.")
            if not spec.key.strip() or spec.key in seen:
                raise PluginValidationError(f"Plugin '{target}' setting key {spec.key!r} is blank or duplicated.")
            seen.add(spec.key)
            if not spec.label.strip() or not spec.warning.strip():
                raise PluginValidationError(f"Plugin '{target}' setting '{spec.key}' needs label and warning.")
            if not callable(spec.decode) or not callable(spec.display) or not callable(spec.is_unset):
                raise PluginValidationError(f"Plugin '{target}' setting '{spec.key}' has a non-callable codec.")
            try:
                decoded = spec.decode(spec.default)
                displayed = spec.display(spec.default)
            except Exception as exc:
                raise PluginValidationError(f"Plugin '{target}' setting '{spec.key}' default failed: {exc}") from exc
            if not isinstance(displayed, str):
                raise PluginValidationError(f"Plugin '{target}' setting '{spec.key}' display must return str.")

    @staticmethod
    def _domains_overlap(first: str, second: str) -> bool:
        return first == second or first.endswith("." + second) or second.endswith("." + first)

    @classmethod
    def _check_domain_conflicts(cls, plugin: RegisteredPlugin) -> None:
        for domain in plugin.domains:
            for owner, existing_plugin in cls._plugins.items():
                for existing in existing_plugin.domains:
                    if cls._domains_overlap(domain, existing):
                        raise PluginValidationError(
                            f"Domain conflict: '{domain}' for '{plugin.target}' overlaps "
                            f"'{existing}' for '{owner}'."
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

    @classmethod
    def plugin_for_url(cls, value: object) -> RegisteredPlugin | None:
        cls.discover()
        return next((plugin for plugin in cls._plugins.values() if plugin.matches_url(value)), None)

    @classmethod
    def resolve_all_settings(cls, target: str, config_dir: str) -> ResolvedSettings:
        plugin = cls.get_plugin(target)
        path = Path(config_dir) / plugin.config_filename
        if not path.exists():
            return resolve_settings(plugin.setting_specs, None, SettingStatus.NO_CONFIG)
        return TargetConfigLoader(plugin, config_dir).load_settings()

    @staticmethod
    def expected_on_calendar(plugin: RegisteredPlugin,
                             interval: ResolvedSetting[Any]) -> str:
        canonical = interval.value if interval.status in (
            SettingStatus.OK, SettingStatus.DEFAULT,
        ) else plugin.default_interval
        return oncalendar_for(canonical)

    @classmethod
    def resolve_schedule(cls, target: str, config_dir: str) -> ScheduleResolution:
        plugin = cls.get_plugin(target)
        settings = cls.resolve_all_settings(target, config_dir)
        interval_spec = next(spec for spec in plugin.setting_specs if spec.key == KEY_INTERVAL)
        interval = settings.resolved(interval_spec)
        return ScheduleResolution(cls.expected_on_calendar(plugin, interval), interval.status)

    def __init__(self, config_dir: str, state_dir: str | None = None):
        self.config_dir = config_dir
        self.state_dir = state_dir
        self._clients: dict[str, ScraperClient] = {}
        self._settings: dict[str, ResolvedSettings] = {}

    def prime_settings(self, target: str, settings: ResolvedSettings) -> None:
        self._settings[target] = settings

    def settings_for(self, target: str) -> ResolvedSettings:
        if target not in self._settings:
            self._settings[target] = self.resolve_all_settings(target, self.config_dir)
        return self._settings[target]

    def get_client(self, target: str) -> ScraperClient:
        if target in self._clients:
            return self._clients[target]
        plugin = self.get_plugin(target)
        module_name, symbol = plugin.client.split(":", 1)
        try:
            module = importlib.import_module(module_name, package=plugin.package)
        except ImportError as exc:
            raise PluginDependencyError(
                messages.plugin_dependency_detail(plugin.target, getattr(exc, "name", None))
            ) from exc
        try:
            client_type = getattr(module, symbol)
        except AttributeError as exc:
            raise PluginValidationError(
                f"Plugin '{target}' client binding '{plugin.client}' does not exist."
            ) from exc
        if not isinstance(client_type, type) or not issubclass(client_type, ScraperClient):
            raise PluginValidationError(
                f"Plugin '{target}' client binding must be a ScraperClient subclass."
            )
        self._clients[target] = client_type(self.settings_for(target))
        return self._clients[target]

    def close_all(self) -> None:
        for client in self._clients.values():
            client.close()
