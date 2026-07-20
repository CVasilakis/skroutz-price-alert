"""Immutable scraper discovery/catalog and one-shot client loading."""

from __future__ import annotations

import importlib
import os
import pkgutil
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from core import messages
from core.exceptions import (
    PluginDependencyError,
    PluginDiscoveryError,
    PluginValidationError,
)
from core.scrapers.api import ItemField, ScraperClient, ScraperPlugin, TrackedItem, UrlField
from core.scrapers.settings import SUPPORTED_INTERVALS, framework_setting_specs
from core.scrapers.url import (
    canonicalize_url,
    normalize_domain,
    parse_url,
    parsed_matches_domains,
)
from core.settings import MISSING, ResolvedSettings, SettingSpec

RESERVED_PLUGIN_NAMES = frozenset({"general", "help", "quiet", "ping", "status", "update"})
RESERVED_ITEM_KEYS = frozenset({"id", "name", "target_price", "skip"})
RUNTIME_PLUGIN_FILES = ("__init__.py", "plugin.py", "client.py")
SNAKE_CASE_KEY = re.compile(r"[a-z][a-z0-9_]*\Z")
CONTROL_CHAR = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True)
class RegisteredPlugin:
    """One fully compiled descriptor record."""

    target: str
    display_name: str
    domains: tuple[str, ...]
    item_fields: tuple[ItemField[Any], ...]
    url_fields: tuple[UrlField, ...]
    reference_url: UrlField | None
    _url_domains: Mapping[UrlField, tuple[str, ...]]
    setting_specs: tuple[SettingSpec[Any], ...]
    settings_by_key: Mapping[str, SettingSpec[Any]]
    default_interval: str
    package: str
    source_dir: str
    example_config_path: str
    requirements_path: str | None

    @property
    def config_filename(self) -> str:
        return f"{self.target}.json"

    def canonicalize_url(self, field: UrlField | object, value: object = MISSING) -> str:
        """Canonicalize a value through one declared URL field.

        A one-argument call uses the declared reference URL. This is retained for
        framework diagnostics; plugin code should use its field declaration.
        """
        if value is MISSING:
            value = field
            if self.reference_url is None:
                raise ValueError("plugin has no reference URL field")
            field = self.reference_url
        if not isinstance(field, UrlField) or field not in self._url_domains:
            raise ValueError("URL field is not declared by this plugin")
        canonical = canonicalize_url(value)
        parsed = parse_url(canonical)
        domains = self._url_domains[field]
        if not parsed_matches_domains(parsed, domains):
            raise ValueError("URL host is not registered for this plugin")
        try:
            accepted = field.accepts_url(parsed)
        except Exception as exc:
            raise ValueError(f"plugin URL matcher failed: {exc}") from exc
        if not isinstance(accepted, bool):
            raise ValueError("plugin URL matcher must return bool")
        if not accepted:
            raise ValueError("URL path is not accepted by this plugin")
        return canonical

    def decode_field(self, field: ItemField[Any], value: object) -> Any:
        """Decode one declared item input through its compiled contract."""
        if field not in self.item_fields:
            raise ValueError("item field is not declared by this plugin")
        if isinstance(field, UrlField):
            return self.canonicalize_url(field, value)
        return field.decode(value)

    def item_reference_url(self, item: TrackedItem) -> str | None:
        """Return the plugin-selected diagnostic/notification URL, when any."""
        return item[self.reference_url] if self.reference_url is not None else None

    def setting(self, key: str) -> SettingSpec[Any]:
        """Return a compiled setting declaration by key."""
        try:
            return self.settings_by_key[key]
        except KeyError as exc:
            raise KeyError(f"Plugin {self.target!r} has no setting {key!r}") from exc


def _sequence(value: object, *, target: str, field: str) -> tuple[Any, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise PluginValidationError(f"Plugin '{target}' {field} must be a sequence.")
    return tuple(value)


def _safe_text(value: object, *, context: str, nonblank: bool = True) -> str:
    if not isinstance(value, str) or nonblank and not value.strip():
        raise PluginValidationError(f"{context} must be a nonblank string.")
    if CONTROL_CHAR.search(value):
        raise PluginValidationError(f"{context} must not contain control characters.")
    return value


def _validate_canonical_default(target: str, kind: str, key: str, declaration: Any) -> None:
    try:
        canonical = declaration.decode(declaration.default)
    except Exception as exc:
        raise PluginValidationError(
            f"Plugin '{target}' {kind} '{key}' default failed: {exc}"
        ) from exc
    if canonical != declaration.default:
        raise PluginValidationError(
            f"Plugin '{target}' {kind} '{key}' default is not canonical; "
            f"declare {canonical!r} instead of {declaration.default!r}."
        )


def compile_plugin(
    definition: object,
    *,
    target: str,
    package: str,
    source_dir: str | os.PathLike[str] | None = None,
    where: str | None = None,
) -> RegisteredPlugin:
    """Validate and normalize one descriptor without changing catalog state."""
    context = where or package
    if not isinstance(definition, ScraperPlugin):
        raise PluginValidationError(f"Plugin '{context}' must export a ScraperPlugin as PLUGIN.")
    if (
        not isinstance(target, str)
        or SNAKE_CASE_KEY.fullmatch(target) is None
        or target in RESERVED_PLUGIN_NAMES
    ):
        raise PluginValidationError(f"Plugin package name '{target}' is invalid or reserved.")
    display_name = _safe_text(
        definition.display_name, context=f"Plugin '{target}' display_name"
    ).strip()

    if (
        not isinstance(definition.default_interval, str)
        or definition.default_interval not in SUPPORTED_INTERVALS
    ):
        raise PluginValidationError(
            f"Plugin '{target}' default_interval must be one of {sorted(SUPPORTED_INTERVALS)}."
        )

    fields = _sequence(definition.item_fields, target=target, field="item_fields")
    seen_fields: set[str] = set()
    url_fields: list[UrlField] = []
    all_domains: list[str] = []
    url_domains: dict[UrlField, tuple[str, ...]] = {}
    for declaration in fields:
        if not isinstance(declaration, ItemField):
            raise PluginValidationError(f"Plugin '{target}' item_fields contains a non-ItemField.")
        key = declaration.key
        if (
            not isinstance(key, str)
            or SNAKE_CASE_KEY.fullmatch(key) is None
            or key in RESERVED_ITEM_KEYS
        ):
            raise PluginValidationError(
                f"Plugin '{target}' item field key {key!r} is invalid or reserved."
            )
        if key in seen_fields:
            raise PluginValidationError(f"Plugin '{target}' duplicates item field '{key}'.")
        seen_fields.add(key)
        if not callable(declaration.decode):
            raise PluginValidationError(
                f"Plugin '{target}' item field '{key}' decoder is not callable."
            )
        if isinstance(declaration, UrlField):
            raw_domains = _sequence(
                declaration.domains, target=target, field=f"URL field '{key}' domains"
            )
            if not raw_domains:
                raise PluginValidationError(
                    f"Plugin '{target}' URL field '{key}' domains must be non-empty."
                )
            domains: list[str] = []
            for raw in raw_domains:
                try:
                    domain = normalize_domain(raw)
                except (TypeError, ValueError) as exc:
                    raise PluginValidationError(
                        f"Plugin '{target}' URL field '{key}' domain {raw!r}: {exc}."
                    ) from exc
                if domain in domains:
                    raise PluginValidationError(
                        f"Plugin '{target}' URL field '{key}' repeats domain '{domain}'."
                    )
                domains.append(domain)
                if domain not in all_domains:
                    all_domains.append(domain)
            if not callable(declaration.accepts_url):
                raise PluginValidationError(
                    f"Plugin '{target}' URL field '{key}' accepts_url must be callable."
                )
            for domain in domains:
                try:
                    probe = declaration.accepts_url(urlsplit(f"https://{domain}/"))
                except Exception as exc:
                    raise PluginValidationError(
                        f"Plugin '{target}' URL field '{key}' accepts_url probe failed: {exc}"
                    ) from exc
                if not isinstance(probe, bool):
                    raise PluginValidationError(
                        f"Plugin '{target}' URL field '{key}' accepts_url must return bool."
                    )
            url_fields.append(declaration)
            url_domains[declaration] = tuple(domains)
            if not declaration.required:
                try:
                    canonical = canonicalize_url(declaration.default)
                    parsed = parse_url(canonical)
                    accepted = parsed_matches_domains(parsed, domains) and declaration.accepts_url(
                        parsed
                    )
                except Exception as exc:
                    raise PluginValidationError(
                        f"Plugin '{target}' item field '{key}' default failed: {exc}"
                    ) from exc
                if canonical != declaration.default or accepted is not True:
                    raise PluginValidationError(
                        f"Plugin '{target}' item field '{key}' default is not canonical."
                    )
        elif not declaration.required:
            _validate_canonical_default(target, "item field", key, declaration)

    reference_url = definition.reference_url
    if reference_url is not None and (
        not isinstance(reference_url, UrlField)
        or not any(reference_url is field for field in url_fields)
    ):
        raise PluginValidationError(
            f"Plugin '{target}' reference_url must be one declared UrlField."
        )

    custom_settings = _sequence(definition.settings, target=target, field="settings")
    framework_settings = framework_setting_specs(definition.default_interval)
    settings = (*framework_settings, *custom_settings)
    seen_settings: set[str] = set()
    for declaration in settings:
        if not isinstance(declaration, SettingSpec):
            raise PluginValidationError(f"Plugin '{target}' settings contains a non-SettingSpec.")
        key = declaration.key
        if (
            not isinstance(key, str)
            or SNAKE_CASE_KEY.fullmatch(key) is None
            or key in seen_settings
        ):
            raise PluginValidationError(
                f"Plugin '{target}' setting key {key!r} is blank or duplicated."
            )
        seen_settings.add(key)
        if (
            not callable(declaration.decode)
            or not callable(declaration.display)
            or not callable(declaration.is_unset)
        ):
            raise PluginValidationError(
                f"Plugin '{target}' setting '{key}' has a non-callable codec."
            )
        if not isinstance(declaration.sensitive, bool):
            raise PluginValidationError(
                f"Plugin '{target}' setting '{key}' sensitive must be a boolean."
            )
        if not declaration.required:
            _validate_canonical_default(target, "setting", key, declaration)
            if not declaration.sensitive:
                try:
                    displayed = declaration.display(declaration.default)
                except Exception as exc:
                    raise PluginValidationError(
                        f"Plugin '{target}' setting '{key}' display failed: {exc}"
                    ) from exc
                if not isinstance(displayed, str):
                    raise PluginValidationError(
                        f"Plugin '{target}' setting '{key}' display must return str."
                    )
                _safe_text(
                    displayed,
                    context=f"Plugin '{target}' setting '{key}' display output",
                    nonblank=False,
                )
        try:
            label = declaration.display_label
            warning = declaration.warning
        except Exception as exc:
            raise PluginValidationError(
                f"Plugin '{target}' setting '{key}' display override failed: {exc}"
            ) from exc
        if not isinstance(label, str) or not label.strip() or CONTROL_CHAR.search(label):
            raise PluginValidationError(
                f"Plugin '{target}' setting '{key}' has an invalid display override."
            )
        if warning is not None and (
            not isinstance(warning, str) or not warning.strip() or CONTROL_CHAR.search(warning)
        ):
            raise PluginValidationError(
                f"Plugin '{target}' setting '{key}' has an invalid display override."
            )

    source = Path(source_dir).resolve() if source_dir is not None else None
    if source is not None:
        missing = [name for name in RUNTIME_PLUGIN_FILES if not (source / name).is_file()]
        if missing:
            raise PluginValidationError(
                f"Plugin '{target}' is missing required file(s): {', '.join(missing)}."
            )
        _safe_text(str(source), context=f"Plugin '{target}' source path")

    requirements = source / "requirements.txt" if source else None
    settings_by_key = MappingProxyType({spec.key: spec for spec in settings})
    return RegisteredPlugin(
        target=target,
        display_name=display_name,
        domains=tuple(all_domains),
        item_fields=tuple(fields),
        url_fields=tuple(url_fields),
        reference_url=reference_url,
        _url_domains=MappingProxyType(url_domains),
        setting_specs=tuple(settings),
        settings_by_key=settings_by_key,
        default_interval=definition.default_interval,
        package=package,
        source_dir=str(source) if source else "",
        example_config_path=str(source / "config.example.json") if source else "",
        requirements_path=str(requirements) if requirements and requirements.is_file() else None,
    )


class PluginCatalog:
    """An immutable, atomically constructed collection of checked-in plugins."""

    def __init__(self, plugins: Iterable[RegisteredPlugin]) -> None:
        records = tuple(plugins)
        by_target: dict[str, RegisteredPlugin] = {}
        for plugin in records:
            if plugin.target in by_target:
                raise PluginValidationError(f"Duplicate plugin target '{plugin.target}'.")
            by_target[plugin.target] = plugin
        self._plugins = records
        self._by_target = MappingProxyType(by_target)

    @classmethod
    def discover(
        cls,
        package_dir: str | os.PathLike[str] | None = None,
        *,
        package: str = "core.scrapers",
    ) -> "PluginCatalog":
        root = Path(package_dir) if package_dir is not None else Path(__file__).parent
        records: list[RegisteredPlugin] = []
        try:
            candidates = sorted(pkgutil.iter_modules([str(root)]), key=lambda item: item.name)
            for candidate in candidates:
                target = candidate.name
                if not candidate.ispkg or target.startswith("_"):
                    continue
                module_name = f"{package}.{target}.plugin"
                try:
                    module = importlib.import_module(module_name)
                except Exception as exc:
                    raise PluginDiscoveryError(
                        f"Failed to import scraper descriptor '{module_name}': {exc}"
                    ) from exc
                try:
                    definition = module.PLUGIN
                except AttributeError as exc:
                    raise PluginDiscoveryError(
                        f"Scraper descriptor '{module_name}' does not export PLUGIN."
                    ) from exc
                records.append(
                    compile_plugin(
                        definition,
                        target=target,
                        package=f"{package}.{target}",
                        source_dir=root / target,
                        where=module_name,
                    )
                )
        except (PluginDiscoveryError, PluginValidationError):
            raise
        except Exception as exc:
            raise PluginDiscoveryError(
                f"Failed to discover scraper plugins in '{root}': {exc}"
            ) from exc
        return cls(records)

    @property
    def plugins(self) -> tuple[RegisteredPlugin, ...]:
        return self._plugins

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(self._by_target)

    def get(self, target: str) -> RegisteredPlugin:
        try:
            return self._by_target[target]
        except KeyError as exc:
            raise ValueError(f"Unsupported plugin: {target}") from exc


class ClientLoader:
    """Load one conventional client without retaining lifecycle state."""

    @staticmethod
    def _import_failure(plugin: RegisteredPlugin, exc: ImportError) -> Exception:
        missing = getattr(exc, "name", None)
        internal = (
            not missing
            or missing == plugin.package
            or plugin.package.startswith(f"{missing}.")
            or missing.startswith(f"{plugin.package}.")
            or missing == "core"
            or missing.startswith("core.")
        )
        if internal:
            return PluginValidationError(f"Plugin '{plugin.target}' client import failed: {exc}")
        return PluginDependencyError(messages.plugin_dependency_detail(plugin.target, missing))

    def load(self, plugin: RegisteredPlugin, settings: ResolvedSettings) -> ScraperClient:
        module_name = f"{plugin.package}.client"
        try:
            module = importlib.import_module(module_name)
        except ImportError as exc:
            raise self._import_failure(plugin, exc) from exc
        except Exception as exc:
            raise PluginValidationError(
                f"Plugin '{plugin.target}' client import failed: {exc}"
            ) from exc
        try:
            client_type = module.Client
        except AttributeError as exc:
            raise PluginValidationError(
                f"Plugin '{plugin.target}' must export Client from client.py."
            ) from exc
        if not isinstance(client_type, type) or not issubclass(client_type, ScraperClient):
            raise PluginValidationError(
                f"Plugin '{plugin.target}' Client must be a ScraperClient subclass."
            )
        try:
            return client_type(settings)
        except ImportError as exc:
            raise self._import_failure(plugin, exc) from exc


__all__ = [
    "ClientLoader",
    "PluginCatalog",
    "RegisteredPlugin",
    "compile_plugin",
]
