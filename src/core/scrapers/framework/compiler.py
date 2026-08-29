"""Validation and compilation of scraper plugin descriptors."""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path
from types import MappingProxyType
from typing import Any
from urllib.parse import urlsplit

from core.exceptions import PluginValidationError
from core.scrapers.api import ItemField, ScraperPlugin, UrlField
from core.scrapers.domain import (
    canonicalize_url,
    normalize_domain,
    parse_url,
    parsed_matches_domains,
)
from core.scrapers.framework.model import RegisteredPlugin
from core.scrapers.framework.naming import (
    FRAMEWORK_ITEM_KEYS,
    RESERVED_PLUGIN_NAMES,
    SNAKE_CASE_KEY,
)
from core.scrapers.framework.setting_specs import SUPPORTED_INTERVALS, framework_setting_specs
from core.settings import SettingSpec

RUNTIME_PLUGIN_FILES = ("__init__.py", "plugin.py", "client.py")
CONTROL_CHAR = re.compile(r"[\x00-\x1f\x7f]")


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
        isinstance(definition.config_schema_version, bool)
        or not isinstance(definition.config_schema_version, int)
        or definition.config_schema_version < 1
    ):
        raise PluginValidationError(
            f"Plugin '{target}' config_schema_version must be a positive integer."
        )

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
            or key in FRAMEWORK_ITEM_KEYS
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
        config_schema_version=definition.config_schema_version,
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


__all__ = ["compile_plugin"]
