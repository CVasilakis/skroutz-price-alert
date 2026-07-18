"""Framework-owned schema-v1 JSON state for scraper targets."""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core.exceptions import StateFileError
from core.persistence import format_utc, parse_utc, write_json_atomically

SCHEMA_VERSION = 1
STATE_TOP_KEYS = frozenset({"schema_version", "items"})
STATE_ITEM_KEYS = frozenset({"last_price", "last_checked"})


def _state_price(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("last_price must be a number")
    price = float(value)
    if not math.isfinite(price) or price < 0:
        raise ValueError("last_price must be finite and non-negative")
    return price


@dataclass(frozen=True)
class StateEntry:
    """Historical state for one explicit item ID."""

    last_price: float | None = None
    last_checked: datetime | None = None


class JsonStateRepository:
    """Loaded state plus pending ID-based mutations; missing state is empty."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = str(path)
        self._items: dict[str, StateEntry] = {}
        self._pending: dict[str, StateEntry] = {}

    def load(self) -> None:
        path = Path(self.path)
        if not path.exists():
            self._items = {}
            return
        try:
            with path.open(encoding="utf-8") as file:
                document = json.load(file)
            self._items = self._validate_document(document)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise StateFileError(f"State file '{path}' is invalid or unreadable: {exc}") from exc

    @staticmethod
    def _validate_document(document: object) -> dict[str, StateEntry]:
        if not isinstance(document, dict):
            raise ValueError("top level must be an object")
        unknown = set(document) - STATE_TOP_KEYS
        if unknown:
            raise ValueError(f"unknown top-level keys: {', '.join(sorted(unknown))}")
        if document.get("schema_version") != SCHEMA_VERSION:
            raise ValueError("schema_version must be 1")
        raw_items = document.get("items")
        if not isinstance(raw_items, dict):
            raise ValueError("items must be an object keyed by item ID")
        result: dict[str, StateEntry] = {}
        for item_id, raw in raw_items.items():
            if not isinstance(item_id, str) or not item_id.strip():
                raise ValueError("state item IDs must be nonblank strings")
            if not isinstance(raw, dict):
                raise ValueError(f"state for {item_id!r} must be an object")
            unknown_fields = set(raw) - STATE_ITEM_KEYS
            if unknown_fields:
                raise ValueError(
                    f"state for {item_id!r} has unknown keys: "
                    f"{', '.join(sorted(unknown_fields))}"
                )
            price = _state_price(raw["last_price"]) if "last_price" in raw else None
            checked = parse_utc(raw["last_checked"]) if "last_checked" in raw else None
            result[item_id] = StateEntry(price, checked)
        return result

    def get(self, item_id: str) -> StateEntry:
        return self._pending.get(item_id, self._items.get(item_id, StateEntry()))

    def record_priced_check(self, item_id: str, price: float, checked_at: datetime) -> None:
        self._pending[item_id] = StateEntry(
            _state_price(price), parse_utc(format_utc(checked_at))
        )

    def record_no_price_check(self, item_id: str, checked_at: datetime) -> None:
        current = self._pending.get(item_id, self._items.get(item_id, StateEntry()))
        self._pending[item_id] = StateEntry(
            current.last_price, parse_utc(format_utc(checked_at))
        )

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def save(self) -> None:
        if not self._pending:
            return
        merged = {**self._items, **self._pending}
        serialized = {
            item_id: {
                **(
                    {"last_price": entry.last_price}
                    if entry.last_price is not None else {}
                ),
                **(
                    {"last_checked": format_utc(entry.last_checked)}
                    if entry.last_checked is not None else {}
                ),
            }
            for item_id, entry in merged.items()
        }
        document = {"schema_version": SCHEMA_VERSION, "items": serialized}
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            write_json_atomically(self.path, document)
        except (OSError, TypeError, ValueError) as exc:
            raise StateFileError(f"Could not save state file '{self.path}': {exc}") from exc
        self._items = merged
        self._pending.clear()


__all__ = ["JsonStateRepository", "StateEntry"]
