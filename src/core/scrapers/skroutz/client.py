import json
import re
from urllib.parse import urlparse

from core.scrapers.api import (
    InvalidURLError,
    PriceResult,
    ProductUnavailableError,
    ScraperParseError,
    TrackedItem,
)
from core.scrapers.http import HttpScraperClient
from core.scrapers.pricing import parse_price
from core.scrapers.skroutz.plugin import URL

# Headers impersonating a real browser to avoid being blocked by anti-bot measures.
# The scraper rotates through these profiles randomly on retries.
_BASE_HEADERS = {
    "authority": "www.skroutz.gr",
    "accept": "application/json, text/plain, */*",
    "dnt": "1",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-mobile": "?0",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "x-requested-with": "XMLHttpRequest",
}

_HEADER_VARIANTS = [
    {
        "accept-language": "en-US,en;q=0.9",
        "referer": "https://www.skroutz.gr/search?keyphrase=home",
        "sec-ch-ua-platform": '"Windows"',
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    },
    {
        "accept-language": "el-GR,el;q=0.9,en-US;q=0.8,en;q=0.7",
        "referer": "https://www.skroutz.gr/search?keyphrase=camera",
        "sec-ch-ua-platform": '"Windows"',
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    },
    {
        "accept-language": "ro-RO,ro;q=0.9,en-US;q=0.8,en;q=0.7",
        "referer": "https://www.skroutz.gr/search?keyphrase=fantasy",
        "sec-ch-ua-platform": '"macOS"',
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    },
    {
        "accept-language": "bg-BG,bg;q=0.9,en-US;q=0.8,en;q=0.7",
        "referer": "https://www.skroutz.gr/search?keyphrase=harry+potter",
        "sec-ch-ua-platform": '"macOS"',
        "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    },
    {
        "accept-language": "de-DE,de;q=0.9,en-US;q=0.8,en;q=0.7",
        "referer": "https://www.skroutz.gr/c/11/home-garden.html",
        "sec-ch-ua-platform": '"Windows"',
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    },
]

_HEADERS_POOL: list[dict[str, str]] = [{**_BASE_HEADERS, **v} for v in _HEADER_VARIANTS]

# Per-TLD currency overrides; every other Skroutz domain prices in euro. Centralized
# here so a new country domain is a one-line entry rather than another inline ternary.
_CURRENCY_BY_TLD: dict[str, str] = {".ro": "Lei"}
_DEFAULT_CURRENCY = "€"


def _currency_for_domain(domain: str) -> str:
    """Returns the currency symbol for a Skroutz domain (euro unless overridden)."""
    for tld, currency in _CURRENCY_BY_TLD.items():
        if domain.endswith(tld):
            return currency
    return _DEFAULT_CURRENCY


class Client(HttpScraperClient):
    """Client for scraping product information from Skroutz.

    Inherits the TLS session, header-pool rotation, and HTTP-status-to-exception
    mapping from :class:`HttpScraperClient`; only the Skroutz-specific request
    shaping and JSON/price extraction live here.
    """

    HEADERS_POOL = _HEADERS_POOL

    def scrape(self, item: TrackedItem) -> PriceResult:
        """Scrapes the Skroutz API for the current price of a product.

        Args:
            item (TrackedItem): The decoded tracked product.

        Returns:
            PriceResult: The scraped price and currency.

        Raises:
            ProductNotFoundError: If the product is not found.
            ProductUnavailableError: If the product is found but price is unavailable.
            InvalidURLError: If the provided URL is invalid.
            ScraperError: For generic scraping errors (e.g. empty response, unexpected HTTP code).
            RateLimitError: If the server blocks the request or limits the rate.
            ServerError: For server-side HTTP errors (5xx).
            ScraperParseError: If the API response cannot be decoded as JSON.
        """
        product_url = item[URL]
        parsed_url = urlparse(product_url)
        domain = parsed_url.netloc
        match = re.search(r"/s/(\d+)", parsed_url.path)

        if not match:
            raise InvalidURLError(f"Failed to parse product ID from URL: {product_url}")

        product_id = match.group(1)
        api_link = f"https://{domain}/s/{product_id}/filter_products.json?"

        headers = self.current_headers.copy()
        headers["authority"] = domain

        parsed_referer = urlparse(headers.get("referer", "https://www.skroutz.gr/"))
        headers["referer"] = parsed_referer._replace(netloc=domain).geturl()

        response = self.get(api_link.strip(), headers=headers)

        # Maps the HTTP status to the modeled exception the orchestrator's retry/abort
        # policy is keyed on (404/410, 401/403/429, 5xx, ...). See HttpScraperClient.
        self.raise_for_status(response.status_code)

        try:
            response_data = response.json()
        except json.JSONDecodeError as e:
            raise ScraperParseError(f"No JSON response: {e}")

        if response_data.get("price_min") is None:
            raise ProductUnavailableError("Not available")

        # parse_price is the single shared price normalizer (handles currency symbols
        # and European/US grouping); None means the value was unparseable, which the
        # orchestrator treats as a modeled parse failure per the exception contract.
        price = parse_price(response_data["price_min"])
        if price is None:
            raise ScraperParseError(
                f"Could not parse price from value: {response_data['price_min']!r}"
            )

        return PriceResult(price=price, currency=_currency_for_domain(domain))
