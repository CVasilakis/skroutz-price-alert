"""Strict target configuration loading and item decoding."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core import messages
from core.exceptions import ConfigFileError
from core.infrastructure.persistence import read_json_object, storage_diagnostic
from core.scrapers.api import TrackedItem
from core.scrapers.framework.model import RegisteredPlugin
from core.settings import (
    MISSING,
    ResolvedSettings,
    SettingsValidationError,
    SettingsValidationProblem,
    validate_settings_block,
)

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
    row_diagnostic: str | None = None


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
        self.display_path = f"config/{plugin.config_filename}"

    def _validation_error(self, message: str, detail: str) -> ConfigFileError:
        error = ValueError(detail)
        return ConfigFileError(
            message,
            storage_diagnostic(
                self.config_path,
                error,
                operation="validate target configuration",
            ),
        )

    def read_document(self) -> dict[str, Any]:
        document = read_json_object(self.config_path, display_path=self.display_path)
        assert document is not None
        unknown = set(document) - TOP_LEVEL_KEYS
        if unknown:
            raise self._validation_error(
                messages.unsupported_config_keys(self.display_path),
                f"unknown top-level keys: {', '.join(sorted(unknown))}",
            )
        if not isinstance(document.get("items"), list):
            raise self._validation_error(
                messages.items_array_required(self.display_path),
                f"items is {type(document.get('items')).__name__}, expected list",
            )
        return document

    def _settings(self, document: dict[str, Any]) -> ResolvedSettings:
        try:
            return validate_settings_block(self.plugin.setting_specs, document.get("settings", {}))
        except SettingsValidationError as exc:
            if exc.problem is SettingsValidationProblem.NOT_OBJECT:
                message = messages.settings_object_required(self.display_path)
            elif exc.problem is SettingsValidationProblem.UNKNOWN:
                message = messages.unsupported_settings(self.display_path)
            elif exc.problem is SettingsValidationProblem.REQUIRED:
                message = messages.required_settings_invalid(self.display_path)
            else:
                message = messages.settings_invalid(self.display_path)
            raise ConfigFileError(
                message,
                storage_diagnostic(
                    self.config_path,
                    exc,
                    operation="validate target settings",
                ),
            ) from exc

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
        row_diagnostic = None
        if issues:
            lines = [
                "Target configuration contains invalid item rows.",
                f"Path: {self.config_path.resolve()}",
            ]
            lines.extend(f"JSON item {issue.index}: {issue.message}" for issue in issues)
            row_diagnostic = "\n".join(lines)
        return LoadedTargetConfig(settings, tuple(items), tuple(issues), row_diagnostic)

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
