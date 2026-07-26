"""Framework-owned schema-v1 JSON state for scraper targets."""

from __future__ import annotations

import json
import math
import os
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from core import messages
from core.exceptions import StateFileError
from core.infrastructure.persistence import (
    format_utc,
    parse_utc,
    read_failure_message,
    save_failure_message,
    storage_diagnostic,
    write_json_atomically,
)
from core.scrapers.framework.url import canonicalize_url

SCHEMA_VERSION = 1
STATE_TOP_KEYS = frozenset({"schema_version", "items"})
STATE_ITEM_KEYS = frozenset(
    {"last_price", "last_checked", "price_alert_delivered", "notified_offer_urls"}
)


def _state_price(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("last_price must be a number")
    price = float(value)
    if not math.isfinite(price) or price < 0:
        raise ValueError("last_price must be finite and non-negative")
    return price


def _state_offer_urls(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("notified_offer_urls must be an array")
    result: list[str] = []
    for raw in value:
        try:
            canonical = canonicalize_url(raw)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "notified_offer_urls must contain absolute credential-free HTTP(S) URLs"
            ) from exc
        if canonical != raw:
            raise ValueError("notified_offer_urls must contain canonical URLs")
        if canonical in result:
            raise ValueError("notified_offer_urls must not contain duplicates")
        result.append(canonical)
    return tuple(result)


@dataclass(frozen=True)
class StateEntry:
    """Historical state for one explicit item ID."""

    last_price: float | None = None
    last_checked: datetime | None = None
    price_alert_delivered: bool = False
    notified_offer_urls: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.price_alert_delivered, bool):
            raise TypeError("price_alert_delivered must be a boolean")
        if not isinstance(self.notified_offer_urls, tuple):
            raise TypeError("notified_offer_urls must be a tuple")
        if self.price_alert_delivered and self.notified_offer_urls:
            raise ValueError("single-price and listing alert state cannot both be active")


class JsonStateRepository:
    """Loaded state plus pending ID-based mutations; missing state is empty."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        *,
        display_path: str | None = None,
    ) -> None:
        self.path = str(path)
        self.display_path = display_path or self.path
        self._items: dict[str, StateEntry] = {}
        self._pending: dict[str, StateEntry] = {}

    def load(self) -> None:
        path = Path(self.path)
        try:
            with path.open(encoding="utf-8") as file:
                document = json.load(file)
        except FileNotFoundError:
            self._items = {}
            return
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise StateFileError(
                read_failure_message(self.display_path, exc),
                storage_diagnostic(path, exc, operation="read scraper state"),
            ) from exc
        try:
            self._items = self.validate_document(document)
        except (TypeError, ValueError) as exc:
            raise StateFileError(
                messages.invalid_state(self.display_path),
                storage_diagnostic(path, exc, operation="validate scraper state"),
            ) from exc

    @staticmethod
    def validate_document(document: object) -> dict[str, StateEntry]:
        if not isinstance(document, dict):
            raise ValueError("top level must be an object")
        unknown = set(document) - STATE_TOP_KEYS
        if unknown:
            raise ValueError(f"unknown top-level keys: {', '.join(sorted(unknown))}")
        version = document.get("schema_version")
        if isinstance(version, bool) or version != SCHEMA_VERSION:
            raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
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
                    f"state for {item_id!r} has unknown keys: {', '.join(sorted(unknown_fields))}"
                )
            price = _state_price(raw["last_price"]) if "last_price" in raw else None
            checked = parse_utc(raw["last_checked"]) if "last_checked" in raw else None
            delivered = raw.get("price_alert_delivered", False)
            if not isinstance(delivered, bool):
                raise ValueError("price_alert_delivered must be a boolean")
            offer_urls = (
                _state_offer_urls(raw["notified_offer_urls"])
                if "notified_offer_urls" in raw
                else ()
            )
            result[item_id] = StateEntry(price, checked, delivered, offer_urls)
        return result

    def get(self, item_id: str) -> StateEntry:
        return self._pending.get(item_id, self._items.get(item_id, StateEntry()))

    def record_priced_check(
        self,
        item_id: str,
        price: float,
        checked_at: datetime,
        *,
        price_alert_delivered: bool = False,
        notified_offer_urls: Iterable[str] = (),
    ) -> None:
        if not isinstance(price_alert_delivered, bool):
            raise TypeError("price_alert_delivered must be a boolean")
        offer_urls = _state_offer_urls(list(notified_offer_urls))
        self._pending[item_id] = StateEntry(
            _state_price(price),
            parse_utc(format_utc(checked_at)),
            price_alert_delivered,
            offer_urls,
        )

    def record_no_price_check(self, item_id: str, checked_at: datetime) -> None:
        current = self._pending.get(item_id, self._items.get(item_id, StateEntry()))
        self._pending[item_id] = StateEntry(current.last_price, parse_utc(format_utc(checked_at)))

    @property
    def has_pending(self) -> bool:
        return bool(self._pending)

    def save(self) -> None:
        if not self._pending:
            return
        merged = {**self._items, **self._pending}
        serialized = {
            item_id: {
                **({"last_price": entry.last_price} if entry.last_price is not None else {}),
                **(
                    {"last_checked": format_utc(entry.last_checked)}
                    if entry.last_checked is not None
                    else {}
                ),
                **({"price_alert_delivered": True} if entry.price_alert_delivered else {}),
                **(
                    {"notified_offer_urls": list(entry.notified_offer_urls)}
                    if entry.notified_offer_urls
                    else {}
                ),
            }
            for item_id, entry in merged.items()
        }
        document = {"schema_version": SCHEMA_VERSION, "items": serialized}
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            write_json_atomically(self.path, document)
        except (OSError, TypeError, ValueError) as exc:
            raise StateFileError(
                save_failure_message(self.display_path, exc),
                storage_diagnostic(self.path, exc, operation="save scraper state"),
            ) from exc
        self._items = merged
        self._pending.clear()


__all__ = ["JsonStateRepository", "StateEntry"]
