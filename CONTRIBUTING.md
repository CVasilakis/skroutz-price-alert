# Contributing a New Scraper

Thanks for wanting to extend **Scrooge Alert** with a new store! The project is
built around a **plugin architecture**, so adding a scraper is *purely additive*:
you drop a new self-contained package into `src/core/scrapers/<name>/` and the
framework discovers it automatically. **You never edit an existing Python or shell
file** — the installer, the management scripts, the CLI flags, and the systemd
units all enumerate scrapers through the registry.

This guide is written for both newcomers and experienced contributors:

- **In a hurry?** Jump to the [Quick Start](#quick-start) and the
  [copy-paste checklist](#final-checklist).
- **Want the full picture?** Read [How it works](#how-it-works) first, then follow
  [Step-by-step](#step-by-step-add-a-scraper).

> Throughout, the placeholder store is **Acme** (machine name `acme`, domain
> `acme.com`). Replace every `acme` / `Acme` / `acme.com` with your store's values.

---

## Table of Contents

1. [Quick Start](#quick-start)
2. [Development Setup](#development-setup)
3. [How it works](#how-it-works)
4. [The four contracts you implement](#the-four-contracts-you-implement)
5. [Step-by-step: add a scraper](#step-by-step-add-a-scraper)
6. [The scraping error contract (important)](#the-scraping-error-contract-important)
7. [Advanced & optional customizations](#advanced--optional-customizations)
8. [Test & validate your scraper](#test--validate-your-scraper)
9. [Project conventions & gotchas](#project-conventions--gotchas)
10. [Submitting your pull request](#submitting-your-pull-request)
11. [Final checklist](#final-checklist)

---

## Quick Start

A scraper is a Python package with up to six small files:

```
src/core/scrapers/acme/
├── __init__.py        # exposes a module-level `plugin` instance (required)
├── plugin.py          # the descriptor: metadata + class bindings (required)
├── client.py          # fetches a page/API and returns the price (required)
├── storage.py         # how the config JSON is read/validated (required)
├── model.py           # the tracked-item dataclass (required)
└── requirements.txt   # this scraper's private dependencies (optional)
```

Then create a `config/acme.json.example`, run `./install.sh --acme`, and test with
`./scripts/run.sh --acme`. That's it — no existing files change.

---

## Development Setup

```sh
git clone https://github.com/CVasilakis/scrooge-alert
cd scrooge-alert
./install.sh          # creates ./venv, installs deps, provisions systemd units
```

- **All code runs in the venv** (`./venv/bin/python3`). The wrapper scripts handle
  this for you; for direct runs use `./venv/bin/python3 src/core/main.py`.
- **Imports are absolute from the `core` package** — the import root is `src/`
  (the entry scripts put it on `sys.path`), so you write
  `from core.scrapers.acme.client import AcmeClient`, **not** `from scrapers...`
  or `from src.core...`.
- **Run the test suite** with `./venv/bin/python3 -m pytest` (pytest is installed
  via `pip install -r requirements-dev.txt`); CI also runs `shellcheck` over the
  shell scripts. Then validate your scraper by running it (see
  [Test & validate](#test--validate-your-scraper)) — discovery itself performs
  strict validation and fails loudly if a plugin is malformed.
- **Requires:** Linux with `systemd` (user services), Python 3.10+.

---

## How it works

```
A product URL  ──►  ScraperRegistry.plugin_for_url()  ──►  YOUR plugin
                    (matches get_supported_domains())          │
                                                               ▼
  orchestrator ── get_client_class() ─► YourClient.scrape_product(url) ─► ScrapeResult
       │                                                               (price, currency)
       │  compares price to the item's target_price, sends notifications,
       └─ get_storage_class() ─► YourDataManager.update_item(url, last_price=…, last_checked=…)
                                                               │
                                                               ▼
                                              atomic save back to config/<name>.json
```

- **The plugin descriptor (`plugin.py`) is the single source of truth** for your
  store: its domains, its config filename, and the client/storage classes it binds.
- **The registry auto-discovers** every package under `scrapers/` (via `pkgutil`),
  validates it, and routes product URLs to the right plugin by domain.
- **The config file doubles as state.** The scraper writes `last_price` /
  `last_checked` (UTC) back into `config/<name>.json` via the atomic save path.

### The import-light contract (load-bearing — read this)

`plugin.py` and `__init__.py` are imported for **every** plugin on **every**
command (to list flags, enumerate units, run `--status`, etc.). They must import
**only stdlib + the base contracts** — **never** a transport/parsing library
(`tls_client`, `selenium`, `lxml`, …).

Those heavy imports belong inside `get_client_class()` / `get_storage_class()`,
which run **only** when a scrape actually instantiates the class. This is what lets
your scraper's dependencies stay uninstalled without breaking every other command.
(Notice in the examples below that `plugin.py` imports your client/storage *inside*
the getter methods, not at module top.)

---

## The four contracts you implement

All base classes live in `src/core/scrapers/base/`.

| Contract | Base class | You provide | Required methods |
| :--- | :--- | :--- | :--- |
| **Descriptor** | `BasePlugin` (`plugin.py`) | store metadata + class bindings | `get_name`, `get_display_name`, `get_supported_domains`, `get_config_filename`, `get_client_class`, `get_storage_class` |
| **Client** | `BaseScraperClient` (`client.py`) — or `HttpScraperClient` for HTTP | the scrape logic | `scrape_product(url) -> ScrapeResult` |
| **Storage** | `JsonProductDataManager` (`storage.py`) | the store's URL-path rule | `_matches_product_path(url) -> bool` (+ `MODEL`, `ROOT_KEY`) |
| **Model** | `BaseTrackedItem` (`model.py`) | the tracked-item type | *(none — may be empty)* |

> **HTTP scrapers should extend `HttpScraperClient`**, not `BaseScraperClient`
> directly. It hands you a `tls_client` session with rotating browser fingerprints,
> a bounded `get()` hook with an explicit whole-request deadline, a header-profile
> pool, and the canonical HTTP-status → exception mapping
> (`raise_for_status`) for free.

---

## Step-by-step: add a scraper

### Step 0 — Pick your identifiers

| Thing | Rule | Acme example |
| :--- | :--- | :--- |
| **Machine name** (`get_name`) | `[A-Za-z0-9_]+` only — no hyphens/dots/spaces. Unique. Becomes the `--<name>` CLI flag and the `<name>-scraper` systemd unit. | `acme` |
| **Display name** | Any non-empty string (used in logs/notifications). | `Acme` |
| **Supported domains** | Non-empty list of hostnames/IPs only: no scheme, path, credentials, or port. DNS names are IDNA-normalized and URL ports are ignored while routing. **Must not overlap** any other plugin's domains. | `["acme.com"]` |
| **Config filename** | A safe JSON basename matching `[A-Za-z0-9][A-Za-z0-9._-]*\.json`; no paths, spaces or control characters, no case-insensitive collision, and never `general.json`. | `acme.json` |

### Step 1 — Create the package

```sh
mkdir src/core/scrapers/acme
```

### Step 2 — `model.py` (the tracked item)

Subclass `BaseTrackedItem`. It already carries `name`, `url`, `target_price`,
`last_price`, `skip`, and `last_checked`, so an empty subclass is fine — it exists
so you can add store-specific fields later without touching the base class.

```python
# src/core/scrapers/acme/model.py
from dataclasses import dataclass

from core.scrapers.base.model import BaseTrackedItem


@dataclass
class AcmeProduct(BaseTrackedItem):
    """An Acme tracked product. Inherits all base fields.

    Add store-specific fields here (e.g. `sku: str = ""`) and override `from_dict`
    to read them, composing the base parsing via `cls._base_field_kwargs(data)`
    (see "Store-specific model fields" below).
    """
    pass
```

### Step 3 — `client.py` (the scrape logic)

Extend `HttpScraperClient`, declare a `HEADERS_POOL` (at least one profile; add a
few so `refresh_identity` can rotate between retries), and implement
`scrape_product`. Return a `ScrapeResult`; signal every failure by **raising a
modeled exception** (see [the error contract](#the-scraping-error-contract-important)).

```python
# src/core/scrapers/acme/client.py
import json
from urllib.parse import urlparse

from core.scrapers.base.http_client import HttpScraperClient
from core.scrapers.base.model import ScrapeResult
from core.exceptions import InvalidURLError, ProductUnavailableError, ScraperParseError
from core.utils import parse_price  # the shared price normalizer — always use it

# A pool of header profiles. One is chosen at random per identity; refresh_identity
# (called between retries by the orchestrator) rotates to another.
_HEADERS_POOL = [
    {
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-US,en;q=0.9",
    },
    {
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "accept": "application/json, text/plain, */*",
        "accept-language": "en-GB,en;q=0.9",
    },
]


class AcmeClient(HttpScraperClient):
    """Scrapes the current price of an Acme product."""

    HEADERS_POOL = _HEADERS_POOL

    def scrape_product(self, product_url: str) -> ScrapeResult:
        # 1) Derive what to request from the product URL.
        path = urlparse(product_url).path
        if "/product/" not in path:
            raise InvalidURLError(f"Unrecognized Acme URL: {product_url}")

        # (Build your real request URL however the store requires.)
        request_url = product_url

        # 2) Fetch through the inherited bounded hook + current header profile.
        #    Never call self.session.get() directly: get() owns the explicit
        #    whole-request deadline shared by every HTTP plugin.
        response = self.get(request_url, headers=self.current_headers)

        # 3) Map the HTTP status to the modeled exception the orchestrator expects
        #    (404/410 -> not found, 401/403/429 -> rate limit, 5xx -> server, ...).
        #    This is inherited — you get correct retry/abort behavior for free.
        self.raise_for_status(response.status_code)

        # 4) Parse the response. Wrap any parse failure as ScraperParseError.
        try:
            data = response.json()
        except json.JSONDecodeError as e:
            raise ScraperParseError(f"Invalid JSON from Acme: {e}")

        raw_price = data.get("price")
        if raw_price is None:
            raise ProductUnavailableError("Acme product is listed but has no price")

        price = parse_price(raw_price)            # None => unparseable
        if price is None:
            raise ScraperParseError(f"Could not parse Acme price: {raw_price!r}")

        return ScrapeResult(price=price, currency="€")
```

**Scraping an HTML page instead of a JSON API?** Same pattern — fetch with
`self.session` / `self.current_headers`, call `self.raise_for_status(...)`, then
parse the markup with your parser of choice (add it to `requirements.txt`). You
still get status mapping and identity rotation for free.

**Not HTTP at all** (e.g. a vendor SDK)? Subclass `BaseScraperClient` directly and
implement `scrape_product`; optionally override `refresh_identity`, `close`, and
`get_current_headers`.

### Step 4 — `storage.py` (config read/validation)

Extend `JsonProductDataManager`. It implements the entire JSON lifecycle
(load/validate, dedup, atomic save, write-back). You only declare two class
attributes and one method — the store-specific URL-**path** rule. The domain check
is handled for you via the plugin's `get_supported_domains()`.

```python
# src/core/scrapers/acme/storage.py
from urllib.parse import urlparse

from core.scrapers.base.storage import JsonProductDataManager
from core.scrapers.acme.model import AcmeProduct


class AcmeDataManager(JsonProductDataManager):
    MODEL = AcmeProduct        # the dataclass parse_item() instantiates
    ROOT_KEY = "products"      # the top-level JSON key holding the item list

    def _matches_product_path(self, url: str) -> bool:
        # `url` is already confirmed to be on a supported domain, so inspect only
        # the path. Return True for paths that look like a product page.
        return "/product/" in urlparse(url).path
```

### Step 5 — `plugin.py` (the descriptor)

The single source of truth. Note the **deferred imports** inside
`get_client_class` / `get_storage_class` — that's the import-light contract.

```python
# src/core/scrapers/acme/plugin.py
from core.scrapers.base.plugin import BasePlugin
from core.scrapers.base.client import BaseScraperClient
from core.scrapers.base.storage import BaseDataManager


class AcmePlugin(BasePlugin):
    """Descriptor for the Acme store — the single source of truth for its domains,
    config filename, and class bindings."""

    _SUPPORTED_DOMAINS = ["acme.com"]

    @staticmethod
    def get_name() -> str:
        return "acme"

    @staticmethod
    def get_display_name() -> str:
        return "Acme"

    @staticmethod
    def get_supported_domains() -> list[str]:
        return AcmePlugin._SUPPORTED_DOMAINS

    @staticmethod
    def get_config_filename() -> str:
        return "acme.json"

    @staticmethod
    def get_client_class() -> type[BaseScraperClient]:
        from core.scrapers.acme.client import AcmeClient        # deferred (import-light)
        return AcmeClient

    @staticmethod
    def get_storage_class() -> type[BaseDataManager]:
        from core.scrapers.acme.storage import AcmeDataManager  # deferred (import-light)
        return AcmeDataManager
```

### Step 6 — `__init__.py` (expose the plugin)

Discovery looks for a **module-level `plugin`** attribute that is a `BasePlugin`
instance. This one line is mandatory:

```python
# src/core/scrapers/acme/__init__.py
from .plugin import AcmePlugin

plugin = AcmePlugin()
```

### Step 7 — `requirements.txt` (only if you need extra libraries)

Anything beyond the core framework goes here — it is installed **only** when your
plugin is provisioned, so users who skip your store never pull its dependencies.
Just dropping the file is enough; `install.sh` finds it automatically.

> **HTTP scrapers need `tls-client`** (because `HttpScraperClient` uses it). HTML
> scrapers should also add their parser (e.g. `lxml`, `beautifulsoup4`).

```text
# src/core/scrapers/acme/requirements.txt
# Dependencies only the Acme scraper needs. Keep their `import` statements behind
# the deferred get_client_class()/get_storage_class() calls (import-light contract).
tls-client
```

### Step 8 — `config/acme.json.example`

Provide a template so users can `cp config/acme.json.example config/acme.json`. The
filename **must** match `get_config_filename()`. This is enforced by the per-plugin
contract battery (`tests/integration/test_plugin_contract.py`), so a plugin without
its example config fails CI. Include the shared `settings` block and a sample product:

```json
{
  "settings": {
    "execution_interval": "1h",
    "log_retention_days": 7,
    "notify_scraping_errors": true
  },
  "products": [
    {
      "name": "Example Product",
      "url": "https://www.acme.com/product/12345",
      "target_price": 100
    }
  ]
}
```

> A product entry needs `name`, `url`, and `target_price`; `skip` is optional.
> `last_price` / `last_checked` are written automatically — don't add them by hand.

---

## The scraping error contract (important)

`scrape_product` communicates its outcome **purely through the exception it
raises** (or by returning a `ScrapeResult` on success). The orchestrator branches on
the exception **type** to decide retry / abort / notify behavior. **Every failure
must be a `ScraperError` subclass** (from `exceptions.py`); anything else is treated
as an unexpected fault.

| Raise this | When | Retried? | `refresh_identity` between tries? | Terminal outcome |
| :--- | :--- | :--- | :--- | :--- |
| `ProductNotFoundError` | product removed / 404·410 | No | — | Red product row; run continues; not aggregated |
| `ProductUnavailableError` | found, but no price | No | — | Red product row; run continues; not aggregated |
| `InvalidURLError` | URL can't be parsed | No | — | Red product row; included in the error summary; run continues |
| `ScraperParseError` | response can't be parsed | Yes | Yes | Counted and notified; final run status reports scrape failure |
| `RateLimitError` | blocked / 401·403·429 | Yes | Yes | **Aborts the whole run for this store**; traceback saved |
| `ServerError` | 5xx | Yes | **No** | Logged but **not** notified or counted (a real outage surfaces via stale-entry tracking) |
| other `ScraperError` | modeled remote/HTTP failure | Yes | Yes | Counted and notified; service status remains successful |
| any non-`ScraperError` exception | plugin/programming fault | Yes | Yes | Counted, traceback saved, and final run status reports scrape failure |

Practical rules:

- **Always `return ScrapeResult(price=…, currency=…)` on success.** (A listing-type
  scraper additionally fills `matches=[…]` and may return `price=None` for a
  checked-but-nothing-matched result — see
  [Listing-type stores](#listing-type-stores-classifieds-searches).)
- Successful results are validated before business logic. Currency and advert titles
  must be nonblank; metadata must be a dictionary; matches must be a list of
  `AdvertMatch`; every price must be numeric (not boolean), finite, and non-negative;
  advert links must be absolute HTTP(S) URLs. With matches, `price` must equal the
  cheapest match; `price=None` is valid only for an empty match list. Violations are
  retried as `InvalidScrapeResultError` and never update `last_price` or send a price alert.
- **Use `parse_price()`** (`from core.utils import parse_price`) for any price string — it
  handles currency symbols and EU/US digit grouping and returns `None` on failure
  (map that `None` to `ScraperParseError`).
- If you extend `HttpScraperClient`, call `self.raise_for_status(response.status_code)`
  and you get the 404/410, 401/403/429, 5xx mapping for free. If your store uses
  non-standard codes, override the `NOT_FOUND_CODES` / `RATE_LIMIT_CODES` class
  tuples (or `raise_for_status` entirely) — don't re-implement the mapping inline.

---

## Advanced & optional customizations

These are all opt-in; skip them unless you need them.

### Run on a non-hourly default schedule

Override `get_default_interval()` with a canonical interval key to set the default
cadence (users can still override it via `execution_interval`). The framework alone
renders `OnCalendar`, the matching timer `Unit`, `RandomizedDelaySec=180s`, and
`Persistent=true`; plugins cannot inject systemd directives.

```python
def get_default_interval(self) -> str:
    return "4h"
```

Migration is intentionally fail-fast: discovery rejects plugins that override the old
`get_timer_directives()` or `get_requirements_path()` hooks. Replace the former with
`get_default_interval()` and place private dependencies in the colocated
`scrapers/<plugin>/requirements.txt`, whose path the registry computes.

### Add a store-specific setting

Every scraper inherits the shared settings (`execution_interval`,
`log_retention_days`, `notify_scraping_errors`) for free. A setting is described by a
single `SettingSpec`: its JSON `key`, a `normalize` (raw value → effective value, or
`None` when unsupported), a `display` formatter, an invalid-value `warning`, and a
`default`. To add your own, declare a spec and return `BASE_SETTING_SPECS + [it]` from
`get_setting_specs()` — the exact shared `BASE_SETTING_SPECS` objects, in their declared
order, must remain the prefix; custom specs only extend it. There is no settings dataclass
to subclass and no `from_dict` to write:

```python
# in plugin.py (import-light — stdlib only, like the rest of the descriptor)
from core.scrapers.base.settings import BASE_SETTING_SPECS, SettingSpec

SPEC_REGION = SettingSpec(
    key="region",
    label="Region",
    normalize=lambda raw: raw if raw in {"eu", "us"} else None,
    display=str,
    warning="Invalid region. Using default.",
    default="eu",
)


class AcmePlugin(BasePlugin):
    ...
    def get_setting_specs(self):
        return BASE_SETTING_SPECS + [SPEC_REGION]
```

The new setting now resolves, validates, and renders in `--status` and the scraping
panel automatically — no base/framework file changes — and discovery rejects a blank or
duplicate `key` at startup.

Unknown keys in a well-formed user `settings` object are intentionally non-fatal: they
are ignored, sorted, and surfaced once in the relevant panel/background startup log.

For a real in-tree example see `SPEC_MIN_ADVERT_PRICE` in `scrapers/insomnia/plugin.py`
(a numeric floor with a bool-rejecting normalizer and a "disabled" display for 0).

To *consume* the effective value, read it through `self.settings`: the registry passes
the target's resolved settings to the **constructor** of both your client and your data
manager, so it is available from `__init__` onward — you can shape your session or
transport from a setting at construction time. A client that overrides `__init__` must
accept `settings` and forward it: `def __init__(self, settings=None):
super().__init__(settings)`.

```python
# in your client's scrape_product(), or in your data manager — read a custom setting
if self.settings.get("region") == "eu":
    ...
```

`self.settings.get(key, default)` returns the effective (already-validated) value,
falling back to the spec's `default` on an unset or invalid value, so scrape-time code
never re-validates.

Settings are **read-only**: they are authored by the user and the application never
writes them back, so there is no `update_setting`. A custom setting must be a plain
user input — never persist runtime state into `settings`. Machine-owned state (a
last price, a timestamp) belongs on item rows via `update_item(url, **fields)`
(see [Store-specific model fields](#store-specific-model-fields)).

### Store-specific model fields

Add fields to your `BaseTrackedItem` subclass and override `from_dict` to read them,
composing the base parsing through `_base_field_kwargs` (never re-implement it — it
owns the invalid-`target_price` sentinel rule, which must not drift between stores):

```python
@classmethod
def from_dict(cls, data):
    return cls(**cls._base_field_kwargs(data), sku=data.get("sku", ""))
```

For a real in-tree example see `scrapers/insomnia/model.py`, which adds the
`title_include`/`title_exclude` list fields (and validates their shape in the
storage's `is_valid_item`, so a malformed field surfaces in the `Config` row
instead of being silently read as empty).

Never write a `to_dict`/full reserialization — persist machine-owned fields by
passing them to `update_item(url, **fields)`, which surgically merges only those
keys and preserves everything the user authored.

### Listing-type stores (classifieds searches)

Most stores fit the classic shape: one row = one product URL = one price. A
*listing-type* store monitors a **listing page** of many independent adverts,
filtered per row — one row = one *search*, and several rows may share one
listing URL with different filters. The built-in `insomnia` plugin is the
reference implementation; the framework models this natively with four pieces:

1. **Return matches, not just a price.** Build one `AdvertMatch(title, price, url)`
   per advert that passes your filters and return
   `ScrapeResult(price=<cheapest match, or None>, currency=…, matches=[…])`.
   The orchestrator sends **one price-drop push per match below target**, each
   linking to that advert's own `url`. `price=None` means "checked fine, nothing
   matched": the row renders as *No matching advert*, `last_checked` refreshes
   (an empty search never goes stale), `last_price` is untouched, and no alert
   is sent. Reserve `ScraperParseError` for pages that don't look like a
   listing at all (markup change, block/interstitial page) — never report those
   as "no match", or a breakage would masquerade as silence forever.

2. **Transport row filters on the URL.** `scrape_product(url)` receives only a
   URL, so fold the row's filter fields into it as query parameters inside your
   model's `from_dict` (see `insomnia/model.py`: `build_search_url` /
   `split_search_url`). The client splits them back out and fetches the bare
   listing URL — the `scrape_product(url)` contract stays unchanged.

3. **Give rows a composite identity.** The base storage keys duplicate-removal
   and write-back merging by clean URL, which would collapse same-URL search
   rows. Override the two row-identity hooks with the same canonical key
   (clean listing URL + normalized terms), one computed from the stored dict
   and one from the item's virtual URL:

   ```python
   def _row_key(self, product): ...   # dedup grouping + save-merge (stored row dict)
   def _update_key(self, url): ...    # update-cache key (item.url)
   ```

   Derive both from one shared helper so they can never disagree —
   `tests/unit/test_insomnia_model.py` pins that invariant
   (`test_dict_side_and_url_side_agree`).

4. **Lead your example config with a filterless row.** The contract battery
   round-trips a save via `update_item(first_row["url"])` — the raw stored URL,
   whose identity carries no filter terms. Make the first row of
   `config/<name>.json.example` one without filters (which also documents that
   the filters are optional).

### Non-JSON storage backend

For a database or remote API, subclass `BaseDataManager` directly and implement its
abstract lifecycle (`load`, `save`, `update_item`, `get_items`, `parse_item`,
`is_valid_item`, `is_scrapable_item`, `clean_storage`). Note this is not a blank-slate
generic store: `BaseDataManager` still models a **product identified by a URL with a
`target_price`** (its `is_scrapable_item` / `has_valid_target_price` helpers assume
exactly that) — you're swapping the *backend*, not the domain. Most stores won't need
this — `JsonProductDataManager` covers file-backed scrapers.

---

## Test & validate your scraper

The suite under `tests/` (run with `./venv/bin/python3 -m pytest`) covers the core
framework; a new scraper needs no unit tests of its own. Validate yours by exercising
the real commands:

```sh
# 1) Provision just your scraper (installs core + your requirements.txt + units)
./install.sh --acme

# 2) Create a real config from your example and add a product with a target price
cp config/acme.json.example config/acme.json
nano config/acme.json

# 3) Run it interactively and watch the output
./scripts/run.sh --acme

# 4) Health check: config validates, units exist, no orphan/settings footnotes
./scripts/run.sh --status

# 5) Confirm notifications are wired (needs a configured .env)
./scripts/run.sh --ping
```

Background-run logs land in `logs/acme/output.log`; crash tracebacks in
`logs/acme/errors.txt`.

**What discovery validates** (a mistake here fails loudly at startup, naming your
package — so run any command, e.g. `./scripts/run.sh --status`, to surface it):

- the package exposes a module-level `plugin` that is a `BasePlugin` instance;
- `get_name()` matches `[A-Za-z0-9_]+`, is non-empty and **unique**;
- `get_display_name()` is non-empty and `get_config_filename()` is a safe `.json` basename;
- `get_supported_domains()` is a non-empty list of normalized, non-overlapping hostnames/IPs;
- `get_default_interval()` is one of the supported canonical interval keys;
- every settings spec satisfies its callable/default/display contract and the base prefix is exact;
- dependency metadata comes only from a colocated `requirements.txt`;
- **no two plugins claim overlapping domains**;
- `get_client_class()` / `get_storage_class()` return the right subclasses (checked
  at first use; a missing dependency surfaces as `PluginDependencyError` pointing at
  `./install.sh --acme`).

---

## Project conventions & gotchas

- **Import-light contract** — see [above](#the-import-light-contract-load-bearing--read-this).
  This is the most common mistake: importing your transport library at the top of
  `plugin.py`/`__init__.py` breaks discovery for *every* command.
- **Respect the rate limiting.** The orchestrator paces requests (base 20s delay +
  1–5s jitter, no concurrency) to avoid anti-bot blocks. Don't "optimize" this away,
  and don't add concurrency inside your client.
- **Never hand-write the config file.** All writes go through the atomic save path
  (`update_item` → `save`). The config is co-authored by the user *and* used as
  state; a full reserialization would clobber their keys.
- **Timestamps are UTC.** Use the shared `TIMESTAMP_FORMAT` (`constants.py`) if you
  write timestamps.
- **No existing files need editing.** If you find yourself modifying a shell script,
  `main.py`, the registry, or another plugin, step back — the framework is designed
  so a new store is *only* new files. (Docs are the exception: update `README.md`
  and `AGENTS.md` if your store is user-visible.)
- **One domain, one plugin.** Overlapping `get_supported_domains()` across plugins is
  rejected at discovery.

---

## Submitting your pull request

1. **Fork** the repo and branch off `main` (e.g. `feat/acme-scraper`).
2. **Keep it self-contained:** your `scrapers/acme/` package, its
   `requirements.txt` (if any), and `config/acme.json.example`. Update `README.md`
   (e.g. supported stores) and `AGENTS.md` if your addition is user-visible.
3. **Manually test** per [Test & validate](#test--validate-your-scraper) and note in
   the PR what you ran. CI runs the test suite (including the per-plugin contract
   battery) and shellcheck, but it cannot scrape your real store — that part is on you.
4. **Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/),**
   matching the existing history. For a new store use the `scrapers` scope, e.g.:

   ```
   feat(scrapers): add Acme store scraper
   ```

5. **Open the PR** with a clear description: which domains it covers, anything
   non-standard about the store's API/anti-bot behavior, and the dependencies you
   added. If you hit a bug or have a question first, please
   [open an issue](https://github.com/CVasilakis/scrooge-alert/issues).

---

## Final checklist

```
[ ] src/core/scrapers/<name>/ package created
[ ] model.py     — BaseTrackedItem subclass
[ ] client.py    — HttpScraperClient (or BaseScraperClient) subclass; scrape_product returns ScrapeResult
[ ]              — every failure raises a modeled ScraperError subclass
[ ]              — prices parsed via utils.parse_price
[ ] storage.py   — JsonProductDataManager subclass with MODEL, ROOT_KEY, _matches_product_path
[ ] plugin.py    — all 6 required methods; optional get_default_interval(); deferred class imports
[ ] __init__.py  — `plugin = <Name>Plugin()`
[ ] requirements.txt — added if you use non-core libs (tls-client for HTTP; your HTML parser)
[ ] config/<name>.json.example — matches get_config_filename(); has settings + a sample product
[ ] get_name() is [A-Za-z0-9_]+, unique; domains are host-only and don't overlap
[ ] custom settings extend the exact BASE_SETTING_SPECS prefix
[ ] ScrapeResult obeys the runtime currency/metadata/match/price/URL invariants
[ ] listing-type store? — ScrapeResult carries matches / price=None, the
    _row_key/_update_key hooks are overridden, and the first example row is filterless
[ ] ./install.sh --<name> succeeds
[ ] ./scripts/run.sh --<name> scrapes a real product
[ ] ./scripts/run.sh --status is clean
[ ] README.md / AGENTS.md updated if user-visible
[ ] Conventional-Commit message: feat(scrapers): add <Store> scraper
```

Welcome aboard, and thank you for contributing! 🎉
