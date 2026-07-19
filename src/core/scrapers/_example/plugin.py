"""Minimal copyable import-light plugin descriptor."""

from urllib.parse import SplitResult

from core.scrapers.api import ScraperPlugin


def accepts_url(url: SplitResult) -> bool:
    return url.path.startswith("/products/")


PLUGIN = ScraperPlugin(
    display_name="Example Store",
    domains=["store.example"],
    accepts_url=accepts_url,
    default_interval="1h",
)
