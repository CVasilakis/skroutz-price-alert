"""The complete, import-light contributor API for scraper plugins.

This module is the only import surface a plugin descriptor needs, and the only
``core`` module that a plugin's ``__init__.py``, ``plugin.py``, and optional
``migrations.py`` are allowed to import.  Everything exported here is either a
declaration a plugin writes, a value the framework hands back to a plugin, or a
modeled failure a plugin raises.  ``CONTRIBUTING.md`` in the repository root is
the authoritative contributor contract; this module restates the same rules
where an editor can show them.

A plugin package declares data and implements one client:

* ``plugin.py`` exports ``PLUGIN`` (a :class:`ScraperPlugin`) together with the
  :class:`ItemField`, :class:`UrlField`, and :class:`SettingSpec` objects it
  declares.  It must stay import-light -- standard library, this module, and
  safe package-local helpers only -- because the catalog imports it for every
  command (status, shell completion, timer rendering) without loading any
  transport, parser, persistence, or UI dependency.
* ``client.py`` exports ``Client``, a :class:`ScraperClient` subclass.  The
  framework derives that binding from the package name and imports the module
  only when its target actually runs, so private dependencies belong here.

The framework deliberately owns everything else: reading and validating
``config/<target>.json``, decoding items and settings, URL canonicalization,
request pacing, retry and abort policy, notifications, machine state in
``state/<target>.json``, and process exit statuses.  Plugins never read or write
state, never choose an exit status, and never receive an item they should not
check.

Plugin-authored setting warnings and modeled skip-exception messages are plain
text. Plugins cannot create Rich footnotes or references. Optional paired
backticks may mark commands, paths, or other code-like fragments; Rich tags are
displayed literally. Do not add wrapping or indentation: the TUI safely wraps
text of any length.
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
    PriceUnavailableError,
    RateLimitError,
    ResourceNotFoundError,
    ScraperError,
    ScraperParseError,
    ServerError,
)
from core.schema_migrations.contracts import ConfigMigration, JsonObject
from core.scrapers.domain import canonicalize_url
from core.settings.model import MISSING, ResolvedSettings, SettingSpec, _MissingDefault

# The canonical value type an ItemField decoder produces; ``item[FIELD]`` is
# typed with it, so a declaration written as ItemField[tuple[str, ...]](...)
# gives the client a precise type without any cast.
T = TypeVar("T")


@dataclass(frozen=True, eq=False)
class ItemField(Generic[T]):
    """One plugin-owned item field. Omitting ``default`` makes it required.

    Declare each field once at module scope in ``plugin.py`` and pass that same
    object to :attr:`ScraperPlugin.item_fields`. ``eq=False`` makes declarations
    compare and hash by identity, which is what turns ``item[FIELD]`` into an
    exact typed lookup: a second ``ItemField`` built with the same ``key`` is a
    different field and raises :class:`KeyError`.

    Fields are decoded from each row of ``items`` in ``config/<target>.json``. A
    row that omits a required field, or whose value the decoder rejects, is
    reported as misconfigured and skipped on its own; the target's remaining
    rows still run.

    Example:
        ```python
        TITLE_TERMS = ItemField[tuple[str, ...]](
            key="title_terms",
            decode=decode_string_tuple,
            default=(),
        )
        REGION = ItemField[str](key="region", decode=decode_region)  # required

        # in Client.scrape:
        terms = item[TITLE_TERMS]
        ```
    """

    key: str
    """The snake_case JSON key users write inside an item row.

    Must be unique within the plugin and must not collide with a framework item
    key (``id``, ``name``, ``target_price``, ``skip``). Unknown keys in a user's
    row are rejected, so this name is part of the plugin's public config
    contract: changing it needs a ``config_schema_version`` bump and a migration.
    """

    decode: Callable[[object], T]
    """Turn one raw JSON value into this field's canonical Python value.

    Receives whatever ``json`` produced (``str``, ``int``, ``float``, ``bool``,
    ``None``, ``list``, ``dict``) and must either return the canonical value or
    raise :class:`TypeError` or :class:`ValueError`; the raised message becomes
    the row's reported issue. Keep it pure and import-light -- it runs inside the
    descriptor module, is called during ordinary config loading, and must not
    perform I/O.
    """

    _: KW_ONLY

    default: T | _MissingDefault = MISSING
    """The value used when a row omits this key; omit it to make the field required.

    An optional default must already be canonical: compilation asserts
    ``decode(default) == default`` and rejects the plugin otherwise, so the
    framework never has to decide whether a default still needs decoding.
    """

    @property
    def required(self) -> bool:
        """Whether every item row must supply this field (no ``default`` was declared)."""
        return self.default is MISSING


@dataclass(frozen=True, eq=False)
class UrlField(ItemField[str]):
    """A URL input with its complete validation and canonicalization contract.

    A ``UrlField`` is an :class:`ItemField` whose value the *framework* decodes.
    For every configured row it, in order:

    1. rejects anything that is not one absolute, credential-free HTTP(S) URL;
    2. canonicalizes it -- lower-cased scheme, lower-cased and IDNA-encoded host,
       no trailing root dot, compressed IPv6, fragment removed. An explicit port,
       the path, and the query are preserved, because they can change which
       resource the URL addresses;
    3. checks the canonical host against :attr:`domains`;
    4. calls :attr:`accepts_url` with the parsed canonical URL.

    A row failing any step is reported as misconfigured and skipped, so a client
    only ever receives URLs that passed all four. ``item[URL]`` returns the
    canonical string, which may differ from what the user typed.

    Every field after the inherited ``KW_ONLY`` marker is keyword-only, so
    ``domains`` and ``accepts_url`` must be passed by name.

    Example:
        ```python
        def is_product_url(url: SplitResult) -> bool:
            return url.path.startswith("/items/")

        SOURCE_URL = UrlField(
            key="url",
            domains=("store.example",),
            accepts_url=is_product_url,
        )
        ```
    """

    decode: Callable[[object], str] = field(default=str, init=False, compare=False, repr=False)
    """The inherited decoder slot, deliberately kept out of ``__init__``.

    URL decoding is framework-owned (see the class docstring), so this
    placeholder is never called; it exists only to satisfy the inherited
    dataclass field. A plugin cannot supply its own URL decoder.
    """

    domains: Sequence[str]
    """The non-empty, duplicate-free hosts this field accepts. Keyword-only.

    Hosts only: no scheme, credentials, port, path, query, or fragment. A DNS
    name matches that exact host and its subdomains, so ``store.example`` also
    accepts ``www.store.example``; an IP literal matches only that address and
    IPv6 is declared without brackets. Values are normalized at compile time with
    the same rules used to canonicalize an item URL, which keeps the form a URL
    is stored under identical to the form it is matched against. Domains may
    overlap with another plugin's: several adapters can serve different page
    shapes on one site.
    """

    accepts_url: Callable[[SplitResult], bool]
    """Decide whether one already-canonical URL is a page this client understands.

    Receives the :func:`urllib.parse.urlsplit` result of the canonical URL, after
    its host already matched :attr:`domains`, so it should inspect only the page
    shape -- usually ``url.path``. It must return a real ``bool`` (a merely truthy
    value is a validation error), stay pure, and perform no I/O. Compilation
    probes it once per declared domain with ``https://<domain>/``; an exception
    raised there fails plugin validation.
    """


@dataclass(frozen=True)
class TrackedItem:
    """Immutable configuration data passed to a plugin client.

    One decoded row of ``items`` from ``config/<target>.json``, carrying
    configuration only: a client never receives -- and cannot reach -- the last
    price, the last check time, or alert history. The framework owns all of that
    in ``state/<target>.json``.

    Items are constructed by the framework alone. A row that fails to decode is
    reported and skipped, and an item whose :attr:`skip` is set never reaches
    :meth:`ScraperClient.scrape`, so every item a client sees is complete and
    meant to be checked.

    Plugin-declared values are kept out of ``repr`` so they cannot leak into
    tracebacks or logs, and are excluded from comparison for the same reason. Two
    items are therefore equal -- and hash alike -- when :attr:`id`, :attr:`name`,
    :attr:`target_price`, and :attr:`skip` match, whatever their declared fields
    hold. Nothing in the framework compares or hashes items, and one target's
    rows always carry distinct IDs, so no two live items can collide; state is
    keyed by :attr:`id` alone, never by equality.
    """

    id: str
    """The user's unique, stable key for this row and the only state key.

    Two rows may deliberately point at the same source input; their separate IDs
    keep separate price and alert history. Renaming an ID starts fresh history.
    """

    name: str
    """The friendly label shown in notifications and run output."""

    target_price: float
    """The alert threshold: a strictly lower price triggers a notification.

    Finite and non-negative. ``0`` is the documented way to monitor a row without
    any alert threshold, since no valid price is below it.
    """

    skip: bool = False
    """Whether the user paused this row. Skipped items never reach the client."""

    _custom: Mapping[ItemField[Any], Any] = field(default_factory=dict, repr=False, compare=False)
    """Framework-private storage for decoded plugin fields; read it via ``item[FIELD]``.

    Plugins must not construct or inspect this mapping directly. Tests build
    items through the ``tests/support.py`` seam instead.
    """

    def __post_init__(self) -> None:
        # Copy before wrapping: the proxy must not stay a live view of a caller's
        # dict, or a frozen item could still change underneath a running client.
        object.__setattr__(self, "_custom", MappingProxyType(dict(self._custom)))

    def __getitem__(self, spec: ItemField[T]) -> T:
        """Return a custom value by the exact field declaration object.

        Args:
            spec: The very :class:`ItemField` object this plugin declared, e.g.
                the module-level ``SOURCE_URL``. Lookup is by identity, not by
                key.

        Returns:
            The decoded, canonical value, typed as the field's ``T``.

        Raises:
            KeyError: The field was not declared by this plugin -- usually an
                equivalent-looking declaration built somewhere else, or a field
                missing from :attr:`ScraperPlugin.item_fields`.
        """
        try:
            return cast(T, self._custom[spec])
        except KeyError as exc:
            raise KeyError(f"Item field {spec.key!r} was not declared by this plugin") from exc


def _price(value: object, field_name: str) -> float:
    """Coerce one result price, rejecting booleans and non-finite or negative values."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InvalidScrapeResultError(f"{field_name} must be a number")
    try:
        result = float(value)
    except (OverflowError, ValueError) as exc:
        # An int too large for a float reaches here rather than becoming inf.
        raise InvalidScrapeResultError(f"{field_name} must be finite") from exc
    if not math.isfinite(result) or result < 0:
        raise InvalidScrapeResultError(f"{field_name} must be finite and non-negative")
    return result


def _nonblank(value: object, field_name: str) -> str:
    """Return one stripped, nonblank result string."""
    if not isinstance(value, str) or not value.strip():
        raise InvalidScrapeResultError(f"{field_name} must be a nonblank string")
    return value.strip()


def _absolute_result_url(value: object, field_name: str) -> str:
    """Canonicalize one result URL with the same rules applied to configured URLs."""
    try:
        return canonicalize_url(value)
    except ValueError as exc:
        raise InvalidScrapeResultError(
            f"{field_name} must be an absolute credential-free HTTP(S) URL"
        ) from exc


@dataclass(frozen=True)
class Offer:
    """One independently alertable offer returned by a listing scrape.

    Each offer below the item's ``target_price`` produces its own notification,
    so build one ``Offer`` per advert, not per page. Values are validated and
    normalized on construction; invalid ones raise
    :class:`InvalidScrapeResultError`, which the application treats as a parse
    failure and retries.
    """

    title: str
    """The advert's own title, stripped. Shown in the notification alongside the item name."""

    price: float
    """This advert's price, as a finite non-negative number in the listing's currency."""

    url: str
    """The advert's absolute HTTP(S) link, canonicalized on construction.

    The canonical form is also this offer's alert identity: with
    ``suppress_repeated_price_alerts`` enabled, the framework remembers the URLs
    it successfully alerted on and suppresses repeats until the offer leaves the
    below-target set. Two adverts of one result should therefore not share a URL:
    nothing rejects that, but they would be treated as the same advert.
    """

    def __post_init__(self) -> None:
        object.__setattr__(self, "title", _nonblank(self.title, "offer title"))
        object.__setattr__(self, "price", _price(self.price, "offer price"))
        object.__setattr__(self, "url", _absolute_result_url(self.url, "offer URL"))


@dataclass(frozen=True)
class PriceResult:
    """A successful single-price scrape.

    Return this from :meth:`ScraperClient.scrape` when the item addresses one
    resource with one price. Values are validated and normalized on construction;
    invalid ones raise :class:`InvalidScrapeResultError`, which the application
    treats as a parse failure and retries, so a malformed result never reaches
    state or a notification.
    """

    price: float
    """The observed price as a finite non-negative number.

    ``0`` is accepted: reporting a genuine zero is the plugin's decision. Raise
    :class:`PriceUnavailableError` instead when the resource simply shows no
    price.
    """

    currency: str
    """The nonblank currency label shown to the user, e.g. ``"EUR"`` or ``"€"``.

    Free text: the framework never converts or compares currencies, it only
    displays this next to the price.
    """

    url: str | None = None
    """An optional link for the price notification, canonicalized on construction.

    Takes precedence over the item's :attr:`ScraperPlugin.reference_url`, which
    lets an identifier-only plugin (no ``UrlField`` at all) still deliver a
    useful link. When neither exists the alert is sent without one.
    """

    def __post_init__(self) -> None:
        object.__setattr__(self, "price", _price(self.price, "price"))
        object.__setattr__(self, "currency", _nonblank(self.currency, "currency"))
        if self.url is not None:
            object.__setattr__(self, "url", _absolute_result_url(self.url, "result URL"))


@dataclass(frozen=True)
class ListingResult:
    """A successful listing scrape, snapshotted as immutable offers.

    Return this from :meth:`ScraperClient.scrape` when the item addresses a
    search or classifieds page that can yield several independent adverts. The
    displayed price for the run is the cheapest offer, and every offer below
    ``target_price`` is alerted separately.

    An empty result is a successful no-match check, not a failure: it refreshes
    the item's last-checked timestamp, preserves its last price, clears active
    listing-alert history, and sends nothing.
    """

    currency: str
    """The nonblank currency label shared by every offer in this result."""

    offers: Iterable[Offer] = ()
    """The matching adverts, consumed once and stored as an immutable tuple.

    Any iterable is accepted, including a generator, so a client can filter
    lazily; it is snapshotted during construction, and the attribute is a
    ``tuple`` afterwards. Do the store-specific filtering (title terms, minimum
    price, ...) before constructing the result -- the framework only compares
    against ``target_price``.
    """

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
"""The two intentional success variants a client may return."""


def validate_scrape_result(value: object) -> ScrapeResult:
    """Retain a defensive boundary check around third-party ``scrape`` methods.

    Framework-internal: the application calls this on whatever a client returned,
    before any evaluation, so an untyped or ``None`` return becomes a modeled
    parse failure instead of an attribute error deeper in the run. Plugins never
    need to call it, which is why it is not re-exported in ``__all__``.

    Args:
        value: The raw return value of :meth:`ScraperClient.scrape`.

    Returns:
        The same object, narrowed to :data:`ScrapeResult`.

    Raises:
        InvalidScrapeResultError: The value is neither a :class:`PriceResult` nor
            a :class:`ListingResult`.
    """
    if not isinstance(value, (PriceResult, ListingResult)):
        raise InvalidScrapeResultError("scrape() must return PriceResult or ListingResult")
    return value


class ScraperClient(ABC):
    """The only runtime class implemented by a scraper plugin.

    ``client.py`` must export a subclass named exactly ``Client``; the framework
    derives that binding from the package name and imports the module only when
    its target actually runs. Transport and parser dependencies therefore belong
    in this module, never in the descriptor.

    Lifecycle, per target and per run:

    1. the framework constructs exactly one instance with the target's resolved
       settings, once that target's configuration has been validated;
    2. :meth:`scrape` is called once per non-skipped item, strictly sequentially
       and paced -- a base delay plus jitter separates consecutive requests;
    3. a failed attempt is retried until the run's attempt limit is reached,
       except for the three skip errors, which return at once;
       :meth:`prepare_retry` runs between attempts for every failure but a
       :class:`ServerError`;
    4. :meth:`close` runs in that target's ``finally`` block, including after an
       interruption or an aborting failure.

    Because instances are never shared or reused across targets, a client may
    keep transport state on itself. Subclasses that define ``__init__`` must call
    ``super().__init__(settings)``.

    Example:
        ```python
        class Client(ScraperClient):
            def scrape(self, item: TrackedItem) -> PriceResult:
                price = fetch_price(item[SOURCE_URL])
                return PriceResult(price=price, currency="EUR")
        ```
    """

    def __init__(self, settings: "ResolvedSettings") -> None:
        """Store the target's resolved settings.

        Args:
            settings: The framework and plugin settings for this target,
                already validated. Read a value with ``self.settings[SPEC]``,
                passing the exact :class:`SettingSpec` object the descriptor
                declared.
        """
        self.settings = settings

    @abstractmethod
    def scrape(self, item: TrackedItem) -> ScrapeResult:
        """Return a result or raise one of the modeled scraper exceptions.

        Called once per non-skipped item; implementations should perform one
        logical check and return, leaving pacing, retries, notification, and
        persistence to the framework.

        Args:
            item: One decoded configuration row. Read declared inputs with
                ``item[FIELD]``, and note that ``item.target_price`` is the
                framework's alert threshold, not a filter the client applies.

        Returns:
            A :class:`PriceResult` for one resource price, or a
            :class:`ListingResult` for a search or classifieds page. An empty
            ``ListingResult`` is a successful no-match check.

        Raises:
            ResourceNotFoundError: The resource is gone or was removed. The item
                is skipped for this run without retrying or alerting.
            PriceUnavailableError: The resource exists but shows no price. Also
                skipped without retrying.
            InvalidURLError: The input cannot address a real resource. Skipped
                without retrying, and included in the scraping-errors
                notification.
            RateLimitError: The host blocked or throttled the request. Retried,
                then aborts this target's remaining items.
            ServerError: A remote 5xx. Retried without :meth:`prepare_retry` and
                not counted as a plugin failure.
            ScraperParseError: The response could not be parsed. Retried.
            ScraperError: Any other modeled scraping failure. Retried.

        Any other exception is retried and then reported as an unexpected fault
        with a saved traceback. Returning something that is not a
        :class:`PriceResult` or :class:`ListingResult` is rejected at the
        boundary and treated as a parse failure.
        """
        raise NotImplementedError

    def prepare_retry(self) -> None:
        """Prepare transport state before a retry, when needed.

        Called between failed attempts (never before the first, and never after
        the last), which makes it the place to rotate a request identity, reset a
        session, or drop a poisoned connection. The default does nothing, so a
        stateless client can ignore it.

        An exception raised here is contained rather than propagated: it is
        recorded as a note on the attempt that failed, a traceback is written to
        the target's error log, and the next attempt proceeds with whatever
        transport state remains. It costs the item neither its remaining retries
        nor the run's unsaved results.
        """

    def close(self) -> None:
        """Release transport resources, when supported.

        Called exactly once per target, in a ``finally`` block, so it also runs
        after an interruption or an aborting failure. The default does nothing.

        An exception raised here is chained behind the run's own failure rather
        than replacing it, so it can never hide the real cause -- but it does
        fail the target, so release resources defensively.
        """

    def diagnostic_context(self) -> Mapping[str, str]:
        """Return non-secret context suitable for traceback logs.

        Merged into ``logs/<target>/errors.txt`` when a failure saves a
        traceback, to make an intermittent fault reproducible -- the request
        identity in use, a resolved endpoint, a response shape. Never include
        credentials, tokens, or the value of a sensitive setting: this text is
        written to disk verbatim.
        """
        return {}


@dataclass(frozen=True)
class ScraperPlugin:
    """A plugin's declarative, stdlib-only descriptor.

    Export one instance as ``PLUGIN`` from the package's ``plugin.py``. The
    package directory name is the target: it becomes the CLI flag, the config,
    state and log stem, and the systemd unit prefix. Compilation validates every
    declaration below before the plugin is ever used, and never rewrites the
    objects a plugin declared.

    Example:
        ```python
        PLUGIN = ScraperPlugin(
            display_name="Acme",
            item_fields=(SOURCE_URL,),
            reference_url=SOURCE_URL,
            default_interval="1h",
        )
        ```
    """

    display_name: str
    """The store's human-readable name, used in panels and notifications.

    Nonblank and free of control characters: the same catalog feeds Rich panels
    and a TSV bridge consumed by the shell scripts.
    """

    item_fields: Sequence[ItemField[Any]] = ()
    """The plugin's own item declarations, in the order they should be presented.

    Any sequence is accepted and compiled into an immutable tuple plus lookup
    maps. Keys must be unique and must not shadow a framework item key. A plugin
    may declare no fields at all if its rows need nothing beyond ``id``,
    ``name``, ``target_price``, and ``skip``.
    """

    settings: Sequence[SettingSpec[Any]] = ()
    """The plugin's own target settings, added after the framework's own.

    The framework always contributes ``execution_interval``,
    ``log_retention_days``, ``notify_scraping_errors``, and
    ``suppress_repeated_price_alerts``; a plugin cannot redeclare those or inject
    systemd directives. Read a resolved value in the client with
    ``self.settings[SPEC]``.
    """

    default_interval: str = "1h"
    """This plugin's canonical timer cadence when the user configures none.

    Must be one of the supported canonical intervals (``15m``, ``30m``, ``1h``,
    ``2h``, ``4h``, ``8h``, ``12h``, ``24h``). It is also the default of the
    framework's ``execution_interval`` setting, and the framework alone renders
    it into the systemd timer.
    """

    reference_url: UrlField | None = None
    """One declared :class:`UrlField` to use for diagnostics and price alerts.

    Must be an object also present in :attr:`item_fields`. It supplies the link
    in a single-price notification when the result carries none, and the URL
    recorded next to a saved traceback. Leave it ``None`` for a plugin with
    several URL inputs or none at all.
    """

    config_schema_version: int = 1
    """The private schema version of this plugin's own config fields and settings.

    Positive integer, independent of the framework's ``schema_version``. User
    configs must declare a matching ``plugin_schema_version``. Bump it whenever a
    private field or setting changes shape, and add a ``migrations.py`` exporting
    ``CONFIG_MIGRATIONS`` with exactly one :data:`ConfigMigration` per source
    version; new plugins stay at ``1`` and ship no ``migrations.py``.
    """


__all__ = [
    "ConfigMigration",
    "JsonObject",
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
    "ResourceNotFoundError",
    "PriceUnavailableError",
    "InvalidURLError",
]
