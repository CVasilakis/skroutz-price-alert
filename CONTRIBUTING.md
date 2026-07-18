# Contributing a scraper plugin

A plugin needs only two implementation files: an import-light `plugin.py` descriptor
and a `client.py`. Start by copying `src/core/scrapers/_example/` to
`src/core/scrapers/<target>/`, then run:

```sh
./scripts/plugin-check.sh --<target>
```

The package directory is the target name and determines `config/<target>.json`,
`state/<target>.json`, CLI flags, logs, requirements, and systemd unit names. Adding a
store must not require edits to framework code or management scripts.

## Descriptor

`plugin.py` is imported during discovery, so it may import only the Python standard
library and `core.scrapers.api`. Never import an HTTP, browser, or parser dependency
there. The binding uses one relative `.module:Symbol` string and is resolved lazily.

```python
from urllib.parse import SplitResult
from core.scrapers.api import ScraperPlugin

def accepts_url(url: SplitResult) -> bool:
    return url.path.startswith("/products/")

PLUGIN = ScraperPlugin(
    display_name="Acme",
    domains=("acme.example",),
    client=".client:AcmeClient",
    accepts_url=accepts_url,
    default_interval="1h",
)
```

The framework validates the scheme, credentials, host, port, and registered domain
before calling `accepts_url`. Inspect only the parsed URL shape your client accepts.
Queries are preserved because they may define a search; fragments are removed.

## Client

Subclass `ScraperClient` and implement `scrape(item)`. Return `PriceResult` for a
single product page or `ListingResult` containing `Offer` values for a listing/search.
An empty listing result means the check succeeded with no match. Raise the modeled
exceptions exported by `core.scrapers.api` so retries and exit status follow the common
policy. Constructor settings are required and available as `self.settings`.

Result constructors reject blank text, booleans as prices, negative or non-finite
prices, invalid offer URLs, and non-`Offer` members. `ListingResult` snapshots any
iterable into an immutable tuple. The orchestrator retains a final return-type check.

## Optional item fields

Declare a field once with `ItemField[T]`; its decoder returns `T` or raises
`ValueError`/`TypeError`. The framework decodes and validates it, and client code reads
it through the declaration object:

```python
TITLE_INCLUDE = ItemField(
    key="title_include",
    decode=decode_string_tuple,
    default=(),
)

terms = item[TITLE_INCLUDE]
```

Do not add a model or storage class. Every runtime item is an immutable `TrackedItem`,
and its explicit `id` is the only state key. Duplicate IDs are configuration errors;
different IDs intentionally allow the same URL more than once.

## Optional settings

Declare custom settings with `SettingSpec[T]`. Its decoder follows the same
return-or-raise rule, and clients use typed lookup:

```python
MIN_PRICE = SettingSpec(
    key="min_price",
    label="Minimum Price",
    decode=decode_nonnegative_float,
    display=lambda value: f"{value:g} EUR",
    warning="min_price must be non-negative; using 0",
    default=0.0,
)

floor = self.settings[MIN_PRICE]
```

The framework adds `execution_interval`, `log_retention_days`, and
`notify_scraping_errors`, using the plugin's concrete default interval. Plugins never
declare systemd directives.

## Package files

Ship `config.example.json` and a useful `README.md`. Add `requirements.txt` only for
client-private dependencies; discovery must work without them. An example has
`settings` and `items`, and each item has a unique human-readable ID. Optional
`metadata` objects are ignored user notes. Unknown keys—including `schema_version`—are
rejected in user configuration.

Framework-owned, schema-versioned state lives separately in `state/<target>.json`;
plugins never read or write it. Both state prices and aware RFC 3339 UTC timestamps are
persisted atomically.

Add focused tests for your URL predicate, codecs, settings, and response parsing. The
generic plugin verifier covers discovery, import weight, metadata, example loading,
routing, binding, dependency guidance, README presence, and state round trips.
