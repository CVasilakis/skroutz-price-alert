"""Strict target configuration loading and item decoding."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.exceptions import ConfigFileError
from core.infrastructure.persistence import read_json_object
from core.scrapers.api import TrackedItem
from core.scrapers.framework.model import RegisteredPlugin
from core.settings import MISSING, ResolvedSettings, validate_settings_block

TOP_LEVEL_KEYS = frozenset({"settings", "items"})
COMMON_ITEM_KEYS = frozenset(
    {
        "id",
        "name",
        "target_price",
        "skip",
    }
)


@dataclass(frozen=True)
class RowIssue:
    index: int
    message: str


@dataclass(frozen=True)
class LoadedTargetConfig:
    settings: ResolvedSettings
    items: tuple[TrackedItem, ...]
    row_issues: tuple[RowIssue, ...]


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
    """Read and decode one required target config without touching state."""

    def __init__(self, plugin: RegisteredPlugin, config_dir: str) -> None:
        self.plugin = plugin
        self.config_path = Path(config_dir) / plugin.config_filename

    def read_document(self) -> dict[str, Any]:
        document = read_json_object(self.config_path)
        assert document is not None
        unknown = set(document) - TOP_LEVEL_KEYS
        if unknown:
            raise ConfigFileError(
                f"Config file '{self.config_path}' has unknown top-level keys: "
                f"{', '.join(sorted(unknown))}"
            )
        if not isinstance(document.get("items"), list):
            raise ConfigFileError("items must be an array")
        return document

    def _settings(self, document: dict[str, Any]) -> ResolvedSettings:
        try:
            return validate_settings_block(self.plugin.setting_specs, document.get("settings", {}))
        except ValueError as exc:
            raise ConfigFileError(str(exc)) from exc

    def load(self) -> LoadedTargetConfig:
        document = self.read_document()
        settings = self._settings(document)
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
                item = self._decode_item(row)
                items.append(item)
            except (TypeError, ValueError) as exc:
                issues.append(RowIssue(index, str(exc)))
        return LoadedTargetConfig(settings, tuple(items), tuple(issues))

    def load_settings(self) -> ResolvedSettings:
        """Resolve a strict config document without loading state or a client."""
        document = self.read_document()
        return self._settings(document)

    def _decode_item(self, row: object) -> TrackedItem:
        if not isinstance(row, dict):
            raise ValueError("item must be an object")
        custom_by_key = {field.key: field for field in self.plugin.item_fields}
        unknown = set(row) - COMMON_ITEM_KEYS - set(custom_by_key)
        if unknown:
            raise ValueError(f"unknown item keys: {', '.join(sorted(unknown))}")
        item_id = _nonblank(row.get("id"), "id")
        name = _nonblank(row.get("name"), "name")
        target_price = _target_price(row.get("target_price"))
        skip = row.get("skip", False)
        if not isinstance(skip, bool):
            raise ValueError("skip must be a boolean")
        custom: dict[Any, Any] = {}
        for field in self.plugin.item_fields:
            raw = row[field.key] if field.key in row else field.default
            if raw is MISSING:
                raise ValueError(f"{field.key} is required")
            try:
                custom[field] = self.plugin.decode_field(field, raw)
            except (TypeError, ValueError, OverflowError) as exc:
                raise ValueError(f"{field.key}: {exc}") from exc
        return TrackedItem(
            id=item_id,
            name=name,
            target_price=target_price,
            skip=skip,
            _custom=custom,
        )
