# Contributing a scraper plugin

Scrapers are checked-in price adapters. A contribution is strictly additive: it
creates `src/core/scrapers/<target>/` and `tests/plugins/<target>/` and does not edit
the framework, registry, shell tools, UI, root documentation, workflows, or snapshots.
The package name becomes the CLI flag and the stem for config, state, logs, and
systemd units.

## Quick start

Generate both plugin-owned directories without installing or touching systemd:

```sh
./scripts/plugin-create.sh acme_store \
  --display-name "Acme Store" \
  --domain store.example \
  --url-prefix /products/
./scripts/dev-setup.sh --acme_store
```

The scaffold refuses existing destinations and leaves a failing behavior-test
placeholder. Implement the client with mocked response fixtures, replace that test,
complete the package-local guide, and run the one-target acceptance command:

```sh
./scripts/plugin-check.sh --acme_store
```

That command checks the descriptor and example, runs only the target-owned tests,
and statically checks the plugin package. Run the full suite before submitting:

```sh
./venv/bin/python3 -m pytest
./venv/bin/basedpyright src
git ls-files -z -- '*.sh' | xargs -0 ./venv/bin/shellcheck -x --exclude=SC2086,SC2046
```

## Package layout

Application discovery requires the Python execution files:

- `__init__.py` — empty and import-light;
- `plugin.py` — the import-light descriptor;
- `client.py` — exports the conventional `Client` class;

The contributor verifier additionally requires:

- `README.md` — store-specific behavior and configuration;
- `config.example.json` — a strict, runnable example with at least one item;
- `tests/plugins/<target>/test_*.py` — mocked target-owned behavior tests.

Add `requirements.txt` only when the client needs private dependencies. Those
dependencies must never be imported by `plugin.py` or `__init__.py`.
Production code must not import a sibling plugin; reusable behavior belongs in the
framework only when it is genuinely store-independent.

## Descriptor contract

`plugin.py` and `__init__.py` must remain import-light. They may use the Python
standard library, `core.scrapers.api`, and safe plugin-local helpers. The isolated
contributor probe verifies actual import effects and rejects third-party or heavy
framework imports instead of relying on a source-code allowlist. The framework
derives `<package>.client:Client` and imports it only when that target runs.

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
query, or fragment. A declared DNS domain accepts that exact host and its
subdomains; an IP declaration matches only that IP. Multiple adapters may support
different page shapes on the same domain. The framework validates and canonicalizes
an item's absolute credential-free HTTP(S) URL, verifies its host against this plugin's domains,
then calls `accepts_url`. Queries are preserved; fragments are removed. The URL
predicate must return a real `bool` and should inspect only the parsed page shape
the client understands.

`default_interval` defaults to `1h` and must use a supported canonical interval.
`domains`, `item_fields`, and `settings` accept ordinary sequences and are
compiled into immutable tuples and lookup maps. Target, field, and setting keys
must be snake_case. Contributor text must not contain control characters because
the same catalog feeds terminal panels and a TSV shell bridge.

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
`ScraperParseError`, or the base `ScraperError`. Their retry preparation,
abort, traceback, notification, and exit-status policies are framework-owned.

## Custom item fields

Declare a typed field once. Its decoder returns a canonical value or raises
`TypeError`/`ValueError`; its default must already be canonical
(`decode(default) == default`). Compilation never rewrites declaration objects.

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

A basic client implements only `scrape()`. Override `prepare_retry()` to rotate
or reset transport state before selected retries, `diagnostic_context()` to return
a non-secret string mapping for traceback logs, and `close()` to release resources.
The orchestrator creates one client per target and closes it in that target's
`finally` block.

HTTP clients may subclass the documented `core.scrapers.http.HttpScraperClient`
for bounded requests, TLS identity rotation, clean shutdown, and standard HTTP
status mapping. Use `core.scrapers.pricing.parse_price` for finite price parsing
with European or US separators. A plugin using this optional helper must declare
`tls-client` in its own `requirements.txt`. Transport/parser modules may load private
dependencies and therefore belong in `client.py`, never the descriptor.

## Config, dependencies, and tests

The example config is a strict JSON object containing `settings` and at least one
valid item. It must demonstrate every custom setting and item field so users do not
need to infer store-specific configuration from Python code.
Every item needs a unique, stable `id`, `name`, accepted `url`, and non-negative
`target_price`; `skip` is optional. Unknown keys, including `metadata`, are rejected.
User config is read-only. Schema-v1 machine state is owned by the framework in
`state/<target>.json`.

Put client-only dependencies in the colocated `requirements.txt`. A missing
dependency must remain discoverable and produce the install hint
`./install.sh --<target>` only when the client is constructed. Missing `client.py`,
a missing or invalid `Client`, and plugin-internal import defects are validation
errors, not dependency errors.

Add target-owned tests under `tests/plugins/<target>/` with focused parser tests for
representative success payloads, malformed markup, no-price/unavailable cases,
relevant status codes, accepted and rejected URL shapes, field codecs, and custom
setting codecs. Never call the live store. The generic verifier checks descriptor
imports, actual isolated import effects, contributor files, custom-schema examples,
sibling-plugin isolation, canonical defaults, conventional client typing, URL
acceptance, dependency guidance, schema-v1 state round trips, and clean shutdown.

CI additionally creates a clean environment for every plugin and installs only core
plus that plugin's own `requirements.txt`. This prevents an undeclared dependency
from being hidden by another plugin. Keep dependencies package-local and verify their
combined constraints with `pip check`.

Run the focused verifier and full acceptance suite:

```sh
./scripts/plugin-check.sh --<target>
./venv/bin/python3 -m pytest
./venv/bin/basedpyright src
git ls-files -z -- '*.sh' | xargs -0 ./venv/bin/shellcheck -x --exclude=SC2086,SC2046
```

Coverage is collected to show untested production lines, but its percentage is
informational only. Do not add `--cov-fail-under`, `fail_under`, or another
coverage threshold: an otherwise successful local or CI test run must never fail
because of its coverage percentage.

The test suite itself scaffolds and discovers a temporary plugin to prove that the
two new plugin-owned directories are sufficient. A plugin pull request should not
contain changes outside those directories.
