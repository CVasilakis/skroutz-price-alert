"""Minimal example client; replace the deterministic price with a real fetch."""

from core.scrapers.api import PriceResult, ScraperClient, TrackedItem
from .plugin import REGION, SKU


class ExampleClient(ScraperClient):
    def scrape(self, item: TrackedItem) -> PriceResult:
        _sku = item[SKU]
        _region = self.settings[REGION]
        return PriceResult(price=1.0, currency="EUR")
