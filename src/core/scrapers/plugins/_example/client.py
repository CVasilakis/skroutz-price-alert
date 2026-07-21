"""Minimal example client; replace the deterministic price with a real fetch."""

from core.scrapers.api import PriceResult, ScraperClient, TrackedItem


class Client(ScraperClient):
    def scrape(self, item: TrackedItem) -> PriceResult:
        return PriceResult(price=1.0, currency="EUR")
