"""Copyable import-light example plugin descriptor."""

from urllib.parse import SplitResult

from core.scrapers.api import ItemField, ScraperPlugin, SettingSpec
from .helpers import decode_sku


SKU = ItemField(key="sku", decode=decode_sku, default="unknown")
REGION = SettingSpec(
    key="region", default="global", decode=decode_sku,
)


def accepts_url(url: SplitResult) -> bool:
    return url.path.startswith("/products/")


PLUGIN = ScraperPlugin(
    display_name="Example Store",
    domains=["store.example"],
    accepts_url=accepts_url,
    item_fields=[SKU],
    settings=[REGION],
    default_interval="1h",
)
