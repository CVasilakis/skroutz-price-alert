"""Framework-owned, atomic JSON state persistence for scraper targets."""

from __future__ import annotations

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.exceptions import StateFileError
from core.scrapers.api import TrackedItem
from core.utils import write_json_atomically

SCHEMA_VERSION = 1
STATE_TOP_KEYS = frozenset({"schema_version", "items"})
STATE_ITEM_KEYS = frozenset({"last_price", "last_checked"})


def format_utc(value: datetime) -> str:
    """Serialize an aware datetime as RFC 3339 UTC with a ``Z`` suffix."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be RFC 3339 UTC ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("timestamp must be valid RFC 3339 UTC") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _state_price(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("last_price must be a number")
    price = float(value)
    if not math.isfinite(price) or price < 0:
        raise ValueError("last_price must be finite and non-negative")
    return price


class JsonStateRepository:
    """Loaded state plus pending mutations; missing state is healthy and empty."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = str(path)
        self._items: dict[str, dict[str, Any]] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self.loaded = False

    def load(self) -> None:
        path = Path(self.path)
        if not path.exists():
            self._items = {}
            self.loaded = True
            return
        try:
            with path.open(encoding="utf-8") as file:
                document = json.load(file)
            self._items = self._validate_document(document)
        except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise StateFileError(f"State file '{path}' is invalid or unreadable: {exc}") from exc
        self.loaded = True

    @staticmethod
    def _validate_document(document: object) -> dict[str, dict[str, Any]]:
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
        result: dict[str, dict[str, Any]] = {}
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
            state: dict[str, Any] = {}
            if "last_price" in raw:
                state["last_price"] = _state_price(raw["last_price"])
            if "last_checked" in raw:
                state["last_checked"] = format_utc(parse_utc(raw["last_checked"]))
            result[item_id] = state
        return result

    def state_for(self, item_id: str) -> tuple[float | None, datetime | None]:
        state = self._items.get(item_id, {})
        price = state.get("last_price")
        checked = state.get("last_checked")
        return (
            float(price) if price is not None else None,
            parse_utc(checked) if checked is not None else None,
        )

    def update_item(self, item: TrackedItem, *, last_price: float | None = None,
                    last_checked: datetime | None = None) -> None:
        if last_price is None and last_checked is None:
            return
        update = dict(self._pending.get(item.id, self._items.get(item.id, {})))
        if last_price is not None:
            update["last_price"] = _state_price(last_price)
        if last_checked is not None:
            update["last_checked"] = format_utc(last_checked)
        self._pending[item.id] = update

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def save(self) -> None:
        if not self._pending:
            return
        merged = {**self._items, **self._pending}
        document = {"schema_version": SCHEMA_VERSION, "items": merged}
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            write_json_atomically(self.path, document)
        except (OSError, TypeError, ValueError) as exc:
            raise StateFileError(f"Could not save state file '{self.path}': {exc}") from exc
        self._items = merged
        self._pending.clear()


def general_state_path(config_dir: str) -> str:
    return str(Path(config_dir).resolve().parent / "state" / "general.json")
