# Contributing a scraper plugin

Scrapers are checked-in price adapters under `src/core/scrapers/`. Adding one is
additive: copy `src/core/scrapers/_example/` to
`src/core/scrapers/<target>/`; no framework, shell, registry, or UI edit is needed.
The package name becomes the CLI flag and the stem for config, state, logs, and
systemd units.

## Package layout

Every production plugin must contain:

- `__init__.py` — empty and import-light;
- `plugin.py` — the import-light descriptor;
- `client.py` — exports the conventional `Client` class;
- `README.md` — store-specific behavior and configuration;
- `config.example.json` — a strict, runnable example.

Add `requirements.txt` only when the client needs private dependencies. Those
dependencies must never be imported by `plugin.py` or `__init__.py`.

## Descriptor contract

`plugin.py` may import only the Python standard library and
`core.scrapers.api`. The descriptor declares metadata; the framework derives
`<package>.client:Client` and imports it only when that target runs.

```python
from urllib.parse import SplitResult

from core.scrapers.api import ScraperPlugin


def accepts_url(url: SplitResult) -> bool:
    return url.path.startswith("/products/")


PLUGIN = ScraperPlugin(
    display_name="Acme",
    domains=["acme.example"],
    accepts_url=accepts_url,
    default_interval="1h",
)
```

Domains are hostnames or IP addresses only—no scheme, credentials, port, path,
query, or fragment. Multiple adapters may support different page shapes on the
same domain. The framework validates and canonicalizes an item's absolute
credential-free HTTP(S) URL, verifies its host against this plugin's domains,
then calls `accepts_url`. Queries are preserved; fragments are removed. The URL
predicate must return a real `bool` and should inspect only the parsed page shape
the client understands.

`default_interval` defaults to `1h` and must use a supported canonical interval.
`domains`, `item_fields`, and `settings` accept ordinary sequences and are
compiled into immutable tuples.

## Client and result contract

`client.py` exports `Client`, a `ScraperClient` subclass:

```python
from core.scrapers.api import PriceResult, ScraperClient, TrackedItem


class Client(ScraperClient):
    def scrape(self, item: TrackedItem) -> PriceResult:
        price = fetch_price(item.url)
        return PriceResult(price=price, currency="EUR")
```

`TrackedItem` contains immutable configuration only: `id`, `name`, `url`,
`target_price`, `skip`, and declared custom fields. Plugins do not receive or
write historical state.

Return one of two intentional variants:

- `PriceResult(price, currency)` for one product price;
- `ListingResult(currency, offers)` for a listing/search, with one `Offer(title,
  price, url)` per independently alertable advert.

An empty `ListingResult` is a successful no-match check. It refreshes
`last_checked`, preserves `last_price`, and sends no alert. Every listing offer
below the target triggers its own alert on every run. Single prices below target
also alert on every run; historical price does not suppress them.

Result values reject blank currency/title strings, boolean, negative, or
non-finite prices, non-`Offer` members, and non-absolute offer URLs. Listing
iterables are snapshotted to immutable tuples.

Raise modeled exceptions from `core.scrapers.api`: `ProductNotFoundError`,
`ProductUnavailableError`, `InvalidURLError`, `RateLimitError`, `ServerError`,
`ScraperParseError`, or the base `ScraperError`. Their retry, identity refresh,
abort, traceback, notification, and exit-status policies are framework-owned.

## Custom item fields

Declare a typed field once. Its decoder returns a canonical value or raises
`TypeError`/`ValueError`; its default must pass that same decoder.

```python
from core.scrapers.api import ItemField

TITLE_TERMS = ItemField(
    key="title_terms",
    default=(),
    decode=decode_string_tuple,
)

# In Client.scrape:
terms = item[TITLE_TERMS]
```

Lookup uses the exact declaration object. Keys must be unique and cannot collide
with framework item keys. Do not add plugin-specific models or storage classes.

## Custom settings

The normal declaration needs only `key`, `default`, and `decode`; its label,
string display, and invalid-value warning are derived. Override presentation only
when the setting needs specialized vocabulary.

```python
from core.scrapers.api import SettingSpec

MIN_PRICE = SettingSpec(
    key="min_price",
    default=0.0,
    decode=decode_nonnegative_float,
    display=lambda value: f"{value:g} EUR" if value else "disabled",
)

# In Client.scrape:
floor = self.settings[MIN_PRICE]
```

Invalid known values fall back to the compiled default and surface a warning.
Unknown setting keys and malformed settings blocks are fatal configuration
errors. The framework adds `execution_interval`, `log_retention_days`, and
`notify_scraping_errors`; plugins cannot declare systemd directives.

## Optional client helpers

HTTP clients may subclass the documented `core.scrapers.http.HttpScraperClient`
for bounded requests, TLS identity rotation, clean shutdown, and standard HTTP
status mapping. Use `core.scrapers.pricing.parse_price` for finite price parsing
with European or US separators. These modules may load private dependencies and
therefore belong in `client.py`, never the descriptor.

## Config, dependencies, and tests

The example config is a strict JSON object containing `settings` and `items`.
Every item needs a unique, stable `id`, `name`, accepted `url`, and non-negative
`target_price`; `skip` and `metadata` are optional. Unknown keys are rejected.
User config is read-only. Schema-v1 machine state is owned by the framework in
`state/<target>.json`.

Put client-only dependencies in the colocated `requirements.txt`. A missing
dependency must remain discoverable and produce the install hint
`./install.sh --<target>` only when the client is constructed.

Add focused parser tests for representative success payloads, malformed markup,
no-price/unavailable cases, relevant status codes, URL shapes, field codecs, and
custom setting codecs. The generic verifier already checks descriptor imports,
package import weight, metadata, canonical defaults, conventional client typing,
strict example loading, URL acceptance, dependency guidance, schema-v1 state
round trips, and clean client shutdown.

Run the focused verifier and full acceptance suite:

```sh
./scripts/plugin-check.sh --<target>
./venv/bin/python3 -m pytest
./venv/bin/basedpyright src
find . -type f -name '*.sh' -print0 | xargs -0 shellcheck
```

To prove the additive workflow itself, copy `_example`, rename its package, run
its plugin check, and verify that no framework or management-script edit is
required.
