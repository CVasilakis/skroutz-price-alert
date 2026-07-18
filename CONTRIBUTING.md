# Contributing a scraper plugin

Adding a store is an additive change: create one package under
`src/core/scrapers/<target>/`. Do not edit the registry, orchestrator, shell scripts,
status UI, or the shared contract test. Discovery, CLI flags, configuration paths,
dependencies, schedules, and the generic test battery are derived from the package.

The target directory name is the machine identifier. It must be a lowercase Python identifier,
must not be a reserved CLI name, and determines all host-owned names:

- config: `config/<target>.json`
- units: `<target>-scraper.service` and `<target>-scraper.timer`
- CLI: `--<target>`

## Package layout

```text
src/core/scrapers/acme/
├── __init__.py
├── plugin.py
├── client.py
├── model.py                 # optional when BaseTrackedItem is sufficient
├── storage.py
├── config.example.json
├── requirements.txt         # optional private dependencies
└── README.md
```

`plugin.py` and `__init__.py` are imported for every plugin during discovery. Keep
them import-light: stdlib and base contracts only. Transport and parser imports belong
in the lazily referenced client or storage modules.

## 1. Declare the plugin

`plugin.py` exports exactly one `PLUGIN` value. It is data, not a class hierarchy:

```python
from core.scrapers.base.plugin import ClassRef, PluginDefinition

PLUGIN = PluginDefinition(
    display_name="Acme",
    domains=("acme.example",),
    client=ClassRef(".client", "AcmeClient"),
    storage=ClassRef(".storage", "AcmeDataManager"),
    default_interval="1h",
)
```

Declare bare normalized hosts only—no scheme, path, credentials, or port. Parent and
subdomain claims cannot overlap across plugins. `default_interval` must be one of the
framework's supported canonical keys; plugins never provide raw systemd directives.

The registry derives the package, target, config filename, example path, and optional
requirements path. `ClassRef` delays importing heavy modules until that target is first
instantiated.

## 2. Model tracked rows and identity

Use `BaseTrackedItem` when a row needs no custom fields. Otherwise add a dataclass and
parse only the extra fields:

```python
from dataclasses import dataclass
from typing import Any

from core.scrapers.base.model import BaseTrackedItem

@dataclass
class AcmeItem(BaseTrackedItem):
    region: str = "eu"

    @classmethod
    def parse_extra_fields(cls, data: dict[str, Any]) -> dict[str, Any]:
        region = data.get("region")
        return {"region": region if isinstance(region, str) else "eu"}
```

The base class owns common normalization. Do not override `from_dict` or duplicate its
field parsing.

By default, the cleaned URL is the row identity. If several logical rows may share a
URL, override the model's single `identity_key()` method. That key drives deduplication,
the update cache, and save merging, so there are no paired storage hooks to keep aligned:

```python
def identity_key(self) -> str:
    return f"{super().identity_key()}|{self.region.casefold()}"
```

The client receives the full parsed item. Never encode row fields into a virtual URL.

## 3. Implement the client

Implement `scrape(item)`. A product page returns `PriceResult`; a listing/search page
returns `ListingResult` with zero or more independent `OfferMatch` values:

```python
from core.scrapers.base.model import BaseTrackedItem, PriceResult
from core.scrapers.base.http_client import HttpScraperClient

class AcmeClient(HttpScraperClient):
    def scrape(self, item: BaseTrackedItem) -> PriceResult:
        response = self.get(item.url, headers=self.current_headers)
        self.raise_for_status(response.status_code)
        price = parse_acme_price(response)
        return PriceResult(price=price, currency="€")
```

For listing stores:

```python
return ListingResult(
    currency="€",
    offers=tuple(OfferMatch(title=o.title, price=o.price, url=o.url) for o in offers),
)
```

An empty `offers` tuple means “checked successfully, no match.” The orchestrator updates
`last_checked` without changing `last_price`. It derives the aggregate cheapest price
when offers exist and sends each qualifying offer as its own notification.

Runtime validation rejects blank currencies, non-dict metadata, non-tuple offer
containers, invalid offer values, booleans/non-finite/negative prices, and non-absolute
HTTP(S) offer URLs. Invalid results enter the normal parse-retry policy and are never
persisted or notified.

Use the modeled exceptions from `core.exceptions`:

- `ProductNotFoundError`, `ProductUnavailableError`, `InvalidURLError`: terminal row error
- `ScraperParseError`: retryable response/parsing error
- `RateLimitError`: retryable blocking/rate limit
- `ServerError`: retryable remote 5xx
- another `ScraperError`: modeled remote failure

Unexpected exceptions are treated as plugin defects, retried, logged with a traceback,
and make the run unhealthy. Wrap parser/library errors in `ScraperParseError` where the
failure is an expected response problem.

Do not remove the shared pacing delay or introduce concurrent requests.

## 4. Implement storage

Most JSON-backed stores need only a model and a path rule:

```python
from core.scrapers.base.storage import JsonProductDataManager
from .model import AcmeItem

class AcmeDataManager(JsonProductDataManager):
    MODEL = AcmeItem

    def _matches_product_path(self, url: str) -> bool:
        return url.startswith("https://acme.example/products/")
```

The base manager validates rows, preserves malformed and unknown user-authored data,
deduplicates by `identity_key()`, caches item-based updates, and atomically merges
machine-owned fields on save. Always call `update_item(item, **fields)` with the parsed
item. Never write config JSON directly or add a settings write path.

## 5. Add custom settings

Declare only plugin-specific `SettingSpec` values and place them in `setting_specs`:

```python
SPEC_REGION = SettingSpec(
    key="region",
    label="Region",
    normalize=normalize_region,
    display=str,
    warning="Unsupported region. Using the default.",
    default="eu",
)

PLUGIN = PluginDefinition(
    # ...
    setting_specs=(SPEC_REGION,),
)
```

The registry prepends the shared execution interval, retention, and error-notification
specs. Redeclaring them or duplicating a key is an error. Resolved settings are injected
into the client and manager constructors and are available as `self.settings`.

Settings are read-only user input. Runtime state belongs on item rows.

## 6. Dependencies, example, and local documentation

Put transport/parser dependencies in the package's optional `requirements.txt`. Keep
the root requirements limited to framework dependencies. Installation selects only the
requirements files of requested plugins.

Ship a valid `config.example.json` beside the plugin. Its top-level item collection must
match the storage manager, contain at least one illustrative row, route every URL back to
the plugin, load without faulty rows, and survive a save round-trip.

Add a package `README.md` documenting URL shape, row fields, settings, dependencies, and
store-specific behavior. General contributor mechanics stay in this file.

## 7. Verify

The shared integration battery automatically includes every discovered target. Add
plugin-specific unit tests only for parsing, URL rules, custom identity, and custom
settings.

```sh
./venv/bin/python3 -m pytest
shellcheck -x --exclude=SC2086,SC2046 install.sh update.sh scripts/*.sh scripts/lib/common.sh
basedpyright src
```

Then exercise the additive path:

```sh
./install.sh --acme
./scripts/run.sh --acme
./scripts/run.sh --status
```

## Review checklist

- `PLUGIN` is declarative and import-light.
- No existing source file needs a store-specific branch or name.
- The target, domains, bindings, interval, and custom settings pass registration.
- The client consumes a parsed item and returns the correct typed result.
- Model identity covers every field that distinguishes logical rows.
- Storage updates use the item and atomic save path.
- Dependencies and the example are package-local.
- Expected failures use modeled exceptions.
- Tests cover only store-specific behavior; the generic contract battery passes.
