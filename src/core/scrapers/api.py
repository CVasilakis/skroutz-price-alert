"""The complete, import-light contributor API for scraper plugins.

Plugin descriptor modules may import this module without loading any transport,
parser, persistence, or UI dependency.  The framework deliberately owns item
decoding and JSON persistence; plugins declare data and implement only a client.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import KW_ONLY, dataclass, field
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
from core.settings.model import MISSING, ResolvedSettings, SettingSpec, _MissingDefault

T = TypeVar("T")


@dataclass(frozen=True, eq=False)
class ItemField(Generic[T]):
    """One plugin-owned item field. Omitting ``default`` makes it required."""

    key: str
    decode: Callable[[object], T]
    _: KW_ONLY
    default: T | _MissingDefault = MISSING

    @property
    def required(self) -> bool:
        return self.default is MISSING


@dataclass(frozen=True, eq=False)
class UrlField(ItemField[str]):
    """A URL input with its complete validation and canonicalization contract."""

    decode: Callable[[object], str] = field(default=str, init=False, compare=False, repr=False)
    domains: Sequence[str]
    accepts_url: Callable[[SplitResult], bool]


@dataclass(frozen=True)
class TrackedItem:
    """Immutable configuration data passed to a plugin client."""

    id: str
    name: str
    target_price: float
    skip: bool = False
    _custom: Mapping[ItemField[Any], Any] = field(default_factory=dict, repr=False, compare=False)

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


def _absolute_result_url(value: object, field_name: str) -> str:
    from core.scrapers.url import canonicalize_url

    try:
        return canonicalize_url(value)
    except ValueError as exc:
        raise InvalidScrapeResultError(
            f"{field_name} must be an absolute credential-free HTTP(S) URL"
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
        object.__setattr__(self, "url", _absolute_result_url(self.url, "offer URL"))


@dataclass(frozen=True)
class PriceResult:
    """A successful product-page scrape."""

    price: float
    currency: str
    url: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", _price(self.price, "price"))
        object.__setattr__(self, "currency", _nonblank(self.currency, "currency"))
        if self.url is not None:
            object.__setattr__(self, "url", _absolute_result_url(self.url, "result URL"))


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

    def prepare_retry(self) -> None:
        """Prepare transport state before a retry, when needed."""

    def close(self) -> None:
        """Release transport resources, when supported."""

    def diagnostic_context(self) -> Mapping[str, str]:
        """Return non-secret context suitable for traceback logs."""
        return {}


@dataclass(frozen=True)
class ScraperPlugin:
    """A plugin's declarative, stdlib-only descriptor."""

    display_name: str
    item_fields: Sequence[ItemField[Any]] = ()
    settings: Sequence[SettingSpec[Any]] = ()
    default_interval: str = "1h"
    reference_url: UrlField | None = None


__all__ = [
    "ScraperPlugin",
    "ItemField",
    "UrlField",
    "SettingSpec",
    "TrackedItem",
    "ScraperClient",
    "PriceResult",
    "ListingResult",
    "Offer",
    "ScrapeResult",
    "InvalidScrapeResultError",
    "ScraperError",
    "RateLimitError",
    "ServerError",
    "ScraperParseError",
    "ProductNotFoundError",
    "ProductUnavailableError",
    "InvalidURLError",
]
