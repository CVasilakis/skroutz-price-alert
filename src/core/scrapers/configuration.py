"""Strict target configuration loading and item decoding."""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.exceptions import ConfigFileError
from core.scrapers.api import TrackedItem
from core.scrapers.state import JsonStateRepository
from core.scrapers.url import canonicalize_url, parsed_matches_domains, parse_url
from core.settings import ResolvedSettings, resolve_settings

TOP_LEVEL_KEYS = frozenset({"settings", "items", "metadata"})
COMMON_ITEM_KEYS = frozenset({
    "id", "name", "url", "target_price", "skip", "metadata",
})


@dataclass(frozen=True)
class RowIssue:
    index: int
    message: str


@dataclass(frozen=True)
class LoadedTargetConfig:
    settings: ResolvedSettings
    items: tuple[TrackedItem, ...]
    row_issues: tuple[RowIssue, ...]
    state: JsonStateRepository


def _nonblank(raw: object, field: str) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{field} must be a nonblank string")
    return raw.strip()


def _target_price(raw: object) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("target_price must be a number")
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        raise ValueError("target_price must be finite and non-negative")
    return value


class TargetConfigLoader:
    """Load a target's config once and join its separately owned state."""

    def __init__(self, plugin: Any, config_dir: str, state_dir: str | None = None) -> None:
        self.plugin = plugin
        self.config_path = Path(config_dir) / plugin.config_filename
        self.state_path = Path(state_dir) / f"{plugin.target}.json" if state_dir else (
            Path(config_dir).resolve().parent / "state" / f"{plugin.target}.json"
        )

    def read_document(self) -> dict[str, Any]:
        try:
            with self.config_path.open(encoding="utf-8") as file:
                document = json.load(file)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConfigFileError(
                f"Config file '{self.config_path}' is invalid or unreadable: {exc}"
            ) from exc
        if not isinstance(document, dict):
            raise ConfigFileError(f"Config file '{self.config_path}' must contain an object")
        unknown = set(document) - TOP_LEVEL_KEYS
        if unknown:
            raise ConfigFileError(
                f"Config file '{self.config_path}' has unknown top-level keys: "
                f"{', '.join(sorted(unknown))}"
            )
        metadata = document.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ConfigFileError("top-level metadata must be an object")
        if not isinstance(document.get("settings", {}), dict):
            raise ConfigFileError("settings must be an object")
        if not isinstance(document.get("items"), list):
            raise ConfigFileError("items must be an array")
        known_settings = {spec.key for spec in self.plugin.setting_specs}
        unknown_settings = set(document.get("settings", {})) - known_settings
        if unknown_settings:
            raise ConfigFileError(
                f"unknown settings: {', '.join(sorted(unknown_settings))}"
            )
        return document

    def load(self) -> LoadedTargetConfig:
        document = self.read_document()
        settings = resolve_settings(self.plugin.setting_specs, document.get("settings", {}))
        state = JsonStateRepository(self.state_path)
        state.load()
        items: list[TrackedItem] = []
        issues: list[RowIssue] = []
        seen_ids: set[str] = set()
        for index, row in enumerate(document["items"], 1):
            try:
                if isinstance(row, dict):
                    candidate_id = _nonblank(row.get("id"), "id")
                    if candidate_id in seen_ids:
                        raise ValueError(f"duplicate item id {candidate_id!r}")
                    # Reserve an explicit ID even if another field in this row is bad;
                    # a later duplicate must never silently become its state owner.
                    seen_ids.add(candidate_id)
                item = self._decode_item(row, state)
                items.append(item)
            except (TypeError, ValueError) as exc:
                issues.append(RowIssue(index, str(exc)))
        return LoadedTargetConfig(settings, tuple(items), tuple(issues), state)

    def load_settings(self) -> ResolvedSettings:
        """Resolve a strict config document without loading state or a client."""
        document = self.read_document()
        return resolve_settings(self.plugin.setting_specs, document.get("settings", {}))

    def _decode_item(self, row: object, state: JsonStateRepository) -> TrackedItem:
        if not isinstance(row, dict):
            raise ValueError("item must be an object")
        custom_by_key = {field.key: field for field in self.plugin.item_fields}
        unknown = set(row) - COMMON_ITEM_KEYS - set(custom_by_key)
        if unknown:
            raise ValueError(f"unknown item keys: {', '.join(sorted(unknown))}")
        metadata = row.get("metadata")
        if metadata is not None and not isinstance(metadata, dict):
            raise ValueError("metadata must be an object")
        item_id = _nonblank(row.get("id"), "id")
        name = _nonblank(row.get("name"), "name")
        url = canonicalize_url(row.get("url"))
        parsed = parse_url(url)
        if not parsed_matches_domains(parsed, self.plugin.domains):
            raise ValueError("URL host is not registered for this plugin")
        try:
            accepted = self.plugin.accepts_url(parsed)
        except Exception as exc:
            raise ValueError(f"plugin URL matcher failed: {exc}") from exc
        if accepted is not True:
            raise ValueError("URL path is not accepted by this plugin")
        target_price = _target_price(row.get("target_price"))
        skip = row.get("skip", False)
        if not isinstance(skip, bool):
            raise ValueError("skip must be a boolean")
        custom: dict[Any, Any] = {}
        for field in self.plugin.item_fields:
            raw = row[field.key] if field.key in row else field.default
            try:
                custom[field] = field.decode(raw)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{field.key}: {exc}") from exc
        last_price, last_checked = state.state_for(item_id)
        return TrackedItem(
            id=item_id, name=name, url=url, target_price=target_price, skip=skip,
            last_price=last_price, last_checked=last_checked, _custom=custom,
        )
