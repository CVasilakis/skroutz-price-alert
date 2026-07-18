"""The complete, import-light contributor API for scraper plugins.

Plugin descriptor modules may import this module without loading any transport,
parser, persistence, or UI dependency.  The framework deliberately owns item
decoding and JSON persistence; plugins declare data and implement only a client.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any, Generic, TypeVar, cast
from urllib.parse import SplitResult

from core.exceptions import (
    InvalidScrapeResultError,
    InvalidURLError,
    ProductNotFoundError,
    ProductUnavailableError,
    RateLimitError,
    ScraperError,
    ScraperParseError,
    ServerError,
)

T = TypeVar("T")


@dataclass(frozen=True, eq=False)
class ItemField(Generic[T]):
    """One plugin-owned item field, including its only decoder and default."""

    key: str
    decode: Callable[[object], T]
    default: T


@dataclass(frozen=True, eq=False)
class SettingSpec(Generic[T]):
    """One typed setting declaration.

    ``decode`` returns a valid value or raises ``ValueError``/``TypeError``.
    Invalid user values fall back to ``default`` and are surfaced by the settings
    presentation layer; ``None`` is never an invalid-value sentinel.
    """

    key: str
    label: str
    decode: Callable[[object], T]
    display: Callable[[T], str]
    warning: str
    default: T
    is_unset: Callable[[object], bool] = field(
        default=lambda value: value is None, compare=False, repr=False
    )


@dataclass(frozen=True)
class TrackedItem:
    """A validated runtime item with state joined by its explicit ID."""

    id: str
    name: str
    url: str
    target_price: float
    skip: bool = False
    last_price: float | None = None
    last_checked: datetime | None = None
    _custom: Mapping[ItemField[Any], Any] = field(
        default_factory=dict, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "_custom", MappingProxyType(dict(self._custom)))

    def __getitem__(self, spec: ItemField[T]) -> T:
        """Return a custom value by the exact field declaration object."""
        try:
            return cast(T, self._custom[spec])
        except KeyError as exc:
            raise KeyError(f"Item field {spec.key!r} was not declared by this plugin") from exc


def _price(value: object, field_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidScrapeResultError(f"{field_name} must be a number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        raise InvalidScrapeResultError(f"{field_name} must be finite") from exc
    if not math.isfinite(result) or result < 0:
        raise InvalidScrapeResultError(f"{field_name} must be finite and non-negative")
    return result


def _nonblank(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidScrapeResultError(f"{field_name} must be a nonblank string")
    return value.strip()


def _absolute_offer_url(value: object) -> str:
    from core.scrapers.url import canonicalize_url

    try:
        return canonicalize_url(value)
    except ValueError as exc:
        raise InvalidScrapeResultError(
            "offer URL must be an absolute credential-free HTTP(S) URL"
        ) from exc


@dataclass(frozen=True)
class Offer:
    """One independently alertable offer returned by a listing scrape."""

    title: str
    price: float
    url: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _nonblank(self.title, "offer title"))
        object.__setattr__(self, "price", _price(self.price, "offer price"))
        object.__setattr__(self, "url", _absolute_offer_url(self.url))


@dataclass(frozen=True)
class PriceResult:
    """A successful product-page scrape."""

    price: float
    currency: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", _price(self.price, "price"))
        object.__setattr__(self, "currency", _nonblank(self.currency, "currency"))


@dataclass(frozen=True)
class ListingResult:
    """A successful listing scrape, snapshotted as immutable offers."""

    currency: str
    offers: Iterable[Offer] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "currency", _nonblank(self.currency, "currency"))
        try:
            offers = tuple(self.offers)
        except TypeError as exc:
            raise InvalidScrapeResultError("offers must be iterable") from exc
        for index, offer in enumerate(offers, 1):
            if not isinstance(offer, Offer):
                raise InvalidScrapeResultError(f"offers[{index}] must be an Offer")
        object.__setattr__(self, "offers", offers)


ScrapeResult = PriceResult | ListingResult


def validate_scrape_result(value: object) -> ScrapeResult:
    """Retain a defensive boundary check around third-party ``scrape`` methods."""
    if not isinstance(value, (PriceResult, ListingResult)):
        raise InvalidScrapeResultError("scrape() must return PriceResult or ListingResult")
    return value


class ScraperClient(ABC):
    """The only runtime class implemented by a scraper plugin."""

    def __init__(self, settings: "ResolvedSettings") -> None:
        self.settings = settings

    @abstractmethod
    def scrape(self, item: TrackedItem) -> ScrapeResult:
        """Return a result or raise one of the modeled scraper exceptions."""
        raise NotImplementedError

    def refresh_identity(self) -> None:
        """Rotate transport identity before a retry, when supported."""

    def close(self) -> None:
        """Release transport resources, when supported."""

    def get_current_headers(self) -> dict[str, str]:
        """Return active request headers for diagnostics."""
        return {}


@dataclass(frozen=True)
class ScraperPlugin:
    """A plugin's declarative, stdlib-only descriptor."""

    display_name: str
    domains: tuple[str, ...]
    client: str
    accepts_url: Callable[[SplitResult], bool]
    item_fields: tuple[ItemField[Any], ...] = ()
    settings: tuple[SettingSpec[Any], ...] = ()
    default_interval: str = "1h"


# Imported only for annotations at runtime through this late import, after all public
# contributor contracts exist.  core.settings itself is stdlib-only.
from core.settings.model import ResolvedSettings  # noqa: E402


__all__ = [
    "ScraperPlugin", "ItemField", "SettingSpec", "TrackedItem", "ScraperClient",
    "PriceResult", "ListingResult", "Offer", "ScrapeResult",
    "InvalidScrapeResultError", "ScraperError", "RateLimitError", "ServerError",
    "ScraperParseError", "ProductNotFoundError", "ProductUnavailableError",
    "InvalidURLError",
]
