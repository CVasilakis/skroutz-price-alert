from dataclasses import dataclass, field
import math
from typing import Any, TypeVar

from core.exceptions import InvalidScrapeResultError
from core.scrapers.base.url import clean_url, is_absolute_http_url
from core.utils import parse_price

T = TypeVar('T', bound='BaseTrackedItem')


@dataclass(frozen=True)
class OfferMatch:
    """One concrete offer found by a listing-type scrape.

    A classic product scrape resolves one URL to one price, but a listing-type
    scraper (a classifieds search) can surface several independent offers in a
    single check. Each one carries its own title and direct link so the
    orchestrator can alert on every offer below the target, linking straight
    to the advert rather than the listing page.

    Attributes:
        title: The advert's own title as published on the listing.
        price: The advert's price as a float.
        url: The direct link to the advert.
    """
    title: str
    price: float
    url: str


@dataclass(frozen=True)
class PriceResult:
    """A successful scrape that resolves one tracked product to one price."""

    price: float
    currency: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ListingResult:
    """A successful listing search containing zero or more independent offers."""

    currency: str
    offers: tuple[OfferMatch, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


ScrapeResult = PriceResult | ListingResult


def _valid_price(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidScrapeResultError(f"{field} must be a number")
    try:
        normalized = float(value)
    except (OverflowError, ValueError) as exc:
        raise InvalidScrapeResultError(f"{field} must be a finite number") from exc
    if not math.isfinite(normalized) or normalized < 0:
        raise InvalidScrapeResultError(f"{field} must be finite and non-negative")
    return normalized


def validate_scrape_result(value: object) -> ScrapeResult:
    """Validate and normalize one scraper's successful return value."""
    if not isinstance(value, (PriceResult, ListingResult)):
        raise InvalidScrapeResultError("scrape() must return a PriceResult or ListingResult")
    if not isinstance(value.currency, str) or not value.currency.strip():
        raise InvalidScrapeResultError("currency must be a nonblank string")
    if not isinstance(value.metadata, dict):
        raise InvalidScrapeResultError("metadata must be a dictionary")

    if isinstance(value, PriceResult):
        return PriceResult(_valid_price(value.price, "price"), value.currency, dict(value.metadata))

    if not isinstance(value.offers, tuple):
        raise InvalidScrapeResultError("offers must be a tuple")
    normalized_offers: list[OfferMatch] = []
    for index, match in enumerate(value.offers, start=1):
        if not isinstance(match, OfferMatch):
            raise InvalidScrapeResultError(f"offers[{index}] must be an OfferMatch")
        if not isinstance(match.title, str) or not match.title.strip():
            raise InvalidScrapeResultError(f"offers[{index}].title must be nonblank")
        price = _valid_price(match.price, f"offers[{index}].price")
        if not is_absolute_http_url(match.url):
            raise InvalidScrapeResultError(f"offers[{index}].url must be an absolute HTTP(S) URL")
        normalized_offers.append(OfferMatch(match.title, price, match.url))
    return ListingResult(value.currency, tuple(normalized_offers), dict(value.metadata))


@dataclass
class BaseTrackedItem:
    """Base class for any item tracked by the scraper.

    Every tracked item — regardless of which store it comes from — has a
    human-readable name, a URL, a target price for alerting, the most
    recently scraped price, a skip flag, and a last-checked timestamp.

    ``from_dict`` owns shared normalization and composes subclass fields through
    ``parse_extra_fields``. ``identity_key`` is the one stable identity used for
    deduplication and state write-back; a plugin with multiple logical rows per URL
    overrides that method on its item model.

    Storage merges only explicitly updated machine fields into the original row,
    preserving unknown user-authored keys. The top-level ``settings`` block remains
    read-only; runtime state belongs on item rows.
    """
    name: str = "Unknown"
    url: str = ""
    target_price: float = 0.0
    last_price: float = 0.0
    skip: bool = False
    last_checked: str = ""

    @classmethod
    def _base_field_kwargs(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Parses the shared base fields out of a stored row, as constructor kwargs.

        Subclasses do not override this method; they implement ``parse_extra_fields``
        while this shared normalization remains authoritative.

        Args:
            data (dict[str, Any]): The item data dictionary.

        Returns:
            dict[str, Any]: One kwarg per base field, ready to pass to ``cls(...)``.
        """
        target_price = parse_price(data.get('target_price', 0.0))
        if target_price is None or target_price < 0:
            target_price = 0.0

        raw_name = data.get('name', 'Unknown')
        name = raw_name.strip() if isinstance(raw_name, str) and raw_name.strip() else 'Unknown'
        raw_url = data.get('url', '')
        url = raw_url if isinstance(raw_url, str) else ''
        last_price = parse_price(data.get('last_price', 0.0))
        if last_price is None or last_price < 0:
            last_price = 0.0
        raw_skip = data.get('skip', False)
        skip = raw_skip if isinstance(raw_skip, bool) else False
        raw_last_checked = data.get('last_checked', '')
        if isinstance(raw_last_checked, str):
            last_checked = raw_last_checked
        elif raw_last_checked is None:
            last_checked = ''
        else:
            # Keep malformed state visible to the timestamp-repair path while
            # preserving the model's string contract for every downstream consumer.
            last_checked = str(raw_last_checked)

        return {
            'name': name,
            'url': url,
            'target_price': target_price,
            'last_price': last_price,
            'skip': skip,
            'last_checked': last_checked,
        }

    @classmethod
    def parse_extra_fields(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Parse subclass-owned fields while base normalization remains centralized."""
        return {}

    @classmethod
    def from_dict(cls: type[T], data: dict[str, Any]) -> T:
        """Creates an instance from a dictionary.

        Error-handling contract:
            * Missing keys fall back to the field defaults declared above.
            * Invalid ``target_price`` values become the safe non-alerting value ``0.0``;
              the orchestrator reads the raw row separately when presenting warnings.
            * Other malformed base fields are normalized to their safe typed
              defaults so presentation and timestamp-repair code never receives
              arbitrary JSON containers.

        Args:
            data (dict[str, Any]): The item data dictionary.

        Returns:
            BaseTrackedItem: A new instance populated with data from the
            dictionary.
        """
        return cls(**cls._base_field_kwargs(data), **cls.parse_extra_fields(data))

    def identity_key(self) -> str:
        """Return the stable key used for deduplication and state write-back."""
        return clean_url(self.url)
