"""Immutable compiled scraper plugin model."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from core.scrapers.api import ItemField, TrackedItem, UrlField
from core.scrapers.framework.url import canonicalize_url, parse_url, parsed_matches_domains
from core.settings import MISSING, SettingSpec


@dataclass(frozen=True)
class RegisteredPlugin:
    """One fully compiled descriptor record."""

    target: str
    display_name: str
    config_schema_version: int
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
        """Canonicalize a value through one declared URL field."""
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


__all__ = ["RegisteredPlugin"]
