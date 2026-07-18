"""Copyable import-light example plugin descriptor."""

from urllib.parse import SplitResult

from core.scrapers.api import ItemField, ScraperPlugin, SettingSpec


def decode_sku(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("must be a nonblank string")
    return raw.strip()


SKU = ItemField(key="sku", decode=decode_sku, default="unknown")
REGION = SettingSpec(
    key="region", label="Region", decode=decode_sku, display=str,
    warning="region must be a nonblank string; using global", default="global",
)


def accepts_url(url: SplitResult) -> bool:
    return url.path.startswith("/products/")


PLUGIN = ScraperPlugin(
    display_name="Example Store",
    domains=("store.example",),
    client=".client:ExampleClient",
    accepts_url=accepts_url,
    item_fields=(SKU,),
    settings=(REGION,),
    default_interval="1h",
)
