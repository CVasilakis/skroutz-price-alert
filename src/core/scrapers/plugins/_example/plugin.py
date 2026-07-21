"""Minimal copyable import-light plugin descriptor."""

from urllib.parse import SplitResult

from core.scrapers.api import ScraperPlugin, UrlField


def accepts_url(url: SplitResult) -> bool:
    return url.path.startswith("/products/")


URL = UrlField(
    key="url",
    domains=["store.example"],
    accepts_url=accepts_url,
)


PLUGIN = ScraperPlugin(
    display_name="Example Store",
    item_fields=(URL,),
    reference_url=URL,
    default_interval="1h",
)
