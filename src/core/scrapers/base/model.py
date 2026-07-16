from dataclasses import dataclass, field
from typing import Any, TypeVar

from core.utils import parse_price

T = TypeVar('T', bound='BaseTrackedItem')


@dataclass
class AdvertMatch:
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


@dataclass
class ScrapeResult:
    """Represents the result of a successful price scrape.

    Attributes:
        price: The scraped price as a float, or ``None`` for a listing-type
            scrape that completed fine but matched no advert ("checked, nothing
            to report"): the orchestrator refreshes ``last_checked`` without
            touching ``last_price`` and sends no alert. Classic single-product
            scrapers always set a float.
        currency: The currency symbol (e.g. ``"€"``, ``"Lei"``).
        matches: The independent offers found by a listing-type scrape, each a
            candidate for its own price-drop alert. Empty for single-product
            scrapers. When non-empty, ``price`` is the cheapest match's price.
        metadata: Optional extra data returned by the scraper (e.g.
            ``{"stock": "in_stock", "seller": "StoreName"}``).  Consumers
            that only need ``price`` and ``currency`` can ignore this.
    """
    price: float | None
    currency: str
    matches: list[AdvertMatch] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BaseTrackedItem:
    """Base class for any item tracked by the scraper.

    Every tracked item — regardless of which store it comes from — has a
    human-readable name, a URL, a target price for alerting, the most
    recently scraped price, a skip flag, and a last-checked timestamp.

    Read-side projection:
        A tracked item is built from a stored row via ``from_dict`` purely for
        reading (price comparison, notifications, stale checks). It is never
        reserialized back to storage. Writes go through
        ``BaseDataManager.update_item(url, **fields)``, which surgically merges
        only the named fields into the stored row. This is deliberate: the config
        file is co-authored by the user, so a full reserialization would clobber
        unknown keys, coerce the user's original input, and persist the invalid
        ``target_price`` sentinel (see ``from_dict``). Subclasses may add
        store-specific fields and override ``from_dict`` to read them, composing the
        base parsing via ``_base_field_kwargs`` instead of re-implementing it::

            @classmethod
            def from_dict(cls, data):
                return cls(**cls._base_field_kwargs(data), sku=data.get('sku', ''))

        To persist a machine-owned field, pass it to ``update_item`` — no ``to_dict``
        is needed.

        Item rows are the *only* place the application writes machine-owned state.
        The config's top-level ``settings`` block is read-only user input — never
        written back — so runtime state belongs here (on the item, via
        ``update_item``), not in ``settings`` (see :mod:`core.scrapers.base.settings`).
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

        The compositional half of ``from_dict``: a subclass adding store-specific
        fields overrides ``from_dict`` and reuses this for the base fields (see the
        class docstring) instead of re-implementing the parsing — in particular the
        ``target_price`` sentinel rule, which must never drift between stores.

        Args:
            data (dict[str, Any]): The item data dictionary.

        Returns:
            dict[str, Any]: One kwarg per base field, ready to pass to ``cls(...)``.
        """
        target_price = parse_price(data.get('target_price', 0.0))
        if target_price is None:
            target_price = -1.0  # sentinel: invalid value

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
    def from_dict(cls: type[T], data: dict[str, Any]) -> T:
        """Creates an instance from a dictionary.

        Error-handling contract:
            * Missing keys fall back to the field defaults declared above.
            * Invalid ``target_price`` values (non-numeric strings, None)
              are stored as ``-1.0`` to signal invalidity.  The caller
              (typically the orchestrator) is responsible for detecting the
              sentinel and deciding how to proceed.
            * Other malformed base fields are normalized to their safe typed
              defaults so presentation and timestamp-repair code never receives
              arbitrary JSON containers.

        Args:
            data (dict[str, Any]): The item data dictionary.

        Returns:
            BaseTrackedItem: A new instance populated with data from the
            dictionary.
        """
        return cls(**cls._base_field_kwargs(data))
