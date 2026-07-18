from urllib.parse import urljoin

from bs4 import BeautifulSoup
from bs4.element import Tag

from core.scrapers.http import HttpScraperClient
from core.scrapers.api import ListingResult, Offer, ScraperParseError, TrackedItem
from core.scrapers.pricing import parse_price
from core.scrapers.insomnia.plugin import MIN_ADVERT_PRICE, TITLE_EXCLUDE, TITLE_INCLUDE

# Headers impersonating a real browser fetching an HTML page. The scraper
# rotates through these profiles randomly on retries.
_BASE_HEADERS = {
    'accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
    'accept-language': 'el-GR,el;q=0.9,en-US;q=0.8,en;q=0.7',
    'dnt': '1',
    'sec-fetch-dest': 'document',
    'sec-fetch-mode': 'navigate',
    'sec-fetch-site': 'none',
    'upgrade-insecure-requests': '1',
}

_HEADER_VARIANTS = [
    {
        'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"Windows"',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    },
    {
        'sec-ch-ua': '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
        'sec-ch-ua-mobile': '?0',
        'sec-ch-ua-platform': '"macOS"',
        'user-agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    },
    {
        'accept-language': 'en-US,en;q=0.9,el;q=0.8',
        'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    },
]

_HEADERS_POOL: list[dict[str, str]] = [{**_BASE_HEADERS, **v} for v in _HEADER_VARIANTS]

# The insomnia.gr classifieds listing markup anchors (one home per selector).
_ADVERT_SELECTOR = ("li", "insAdvertsList")   # one advert card on the listing
_REQUEST_MARKER = "insRequest"                # marks a "Ζήτηση" (want-to-buy) advert
_PRICE_SELECTOR = ("p", "cFilePrice")         # the advert's price element
_NO_PRICE_TEXT = "Επικοινωνία"                # price placeholder: "contact the seller"

_CURRENCY = "€"


class Client(HttpScraperClient):
    """Client scraping insomnia.gr classifieds listing pages.

    One scrape fetches a listing page, walks its advert cards, and returns every
    advert that passes the tracked row's title filters and the
    ``min_advert_price`` floor — each one an :class:`Offer` candidate for
    its own price-drop alert. Inherits the
    TLS session, header-pool rotation, and HTTP-status-to-exception mapping
    from :class:`HttpScraperClient`.
    """

    HEADERS_POOL = _HEADERS_POOL

    def scrape(self, item: TrackedItem) -> ListingResult:
        """Scrapes an insomnia classifieds listing for adverts matching the search.

        Args:
            item (TrackedItem): The decoded listing-search item, including filters.

        Returns:
            ListingResult: Every matching offer, cheapest first; an empty tuple
            means the listing was checked successfully but no offer matched.

        Raises:
            ScraperError: For generic scraping errors (e.g. empty response, unexpected HTTP code).
            RateLimitError: If the server blocks the request or limits the rate.
            ServerError: For server-side HTTP errors (5xx).
            ScraperParseError: If the page does not look like a classifieds listing.
        """
        listing_url = item.url
        include = item[TITLE_INCLUDE]
        exclude = item[TITLE_EXCLUDE]

        response = self.get(listing_url, headers=self.current_headers)

        # Maps the HTTP status to the modeled exception the orchestrator's retry/abort
        # policy is keyed on (404/410, 401/403/429, 5xx, ...). See HttpScraperClient.
        self.raise_for_status(response.status_code)

        matches = self._parse_adverts(response.text or "", listing_url, include, exclude)
        matches.sort(key=lambda match: match.price)
        return ListingResult(currency=_CURRENCY, offers=tuple(matches))

    def _parse_adverts(self, html: str, listing_url: str, include: tuple[str, ...], exclude: tuple[str, ...]) -> list[Offer]:
        """Extracts the adverts matching the search from a listing page.

        Args:
            html (str): The listing page markup.
            listing_url (str): The bare listing URL (for resolving relative links).
            include (list[str]): Title terms that must all be present.
            exclude (list[str]): Title terms that must all be absent.

        Returns:
            list[Offer]: The matching offers (unordered).

        Raises:
            ScraperParseError: If no advert cards are present at all — a markup
                change or a blocked/interstitial page must surface as a loud,
                retried failure rather than masquerade as "no matching advert".
        """
        soup = BeautifulSoup(html, "html.parser")
        adverts = soup.find_all(_ADVERT_SELECTOR[0], class_=_ADVERT_SELECTOR[1])
        if not adverts:
            raise ScraperParseError("No adverts found on the listing page (markup change or blocked?)")

        # The minimum-price floor filtering out bait adverts (an iPhone for 1€).
        # Resolved/validated by the settings engine; 0 disables the floor.
        floor = self.settings[MIN_ADVERT_PRICE]

        matches = []
        for advert in adverts:
            if not isinstance(advert, Tag):
                continue

            # Want-to-buy ("Ζήτηση") adverts are requests, not offers.
            if advert.find(class_=_REQUEST_MARKER):
                continue

            heading = advert.find("h4")
            title_tag = heading.find("a") if isinstance(heading, Tag) else None
            price_tag = advert.find(_PRICE_SELECTOR[0], class_=_PRICE_SELECTOR[1])
            # Check None explicitly before the runtime type guard: this keeps the
            # optional-member contract visible to static analyzers even in a
            # core-only environment where the private bs4 dependency is absent.
            if title_tag is None or price_tag is None:
                raise ScraperParseError("Advert card without a title link or price element (markup change?)")
            if not isinstance(title_tag, Tag) or not isinstance(price_tag, Tag):
                raise ScraperParseError("Advert card without a title link or price element (markup change?)")

            price_text = price_tag.get_text(strip=True)
            if price_text == _NO_PRICE_TEXT:
                continue

            # parse_price is the single shared price normalizer (handles currency
            # symbols and European/US grouping); None means unparseable.
            price = parse_price(price_text)
            if price is None:
                raise ScraperParseError(f"Could not parse advert price from value: {price_text!r}")
            if price < floor:
                continue

            title = title_tag.get_text(strip=True)
            folded = title.casefold()
            if not all(term.casefold() in folded for term in include):
                continue
            if any(term.casefold() in folded for term in exclude):
                continue

            matches.append(Offer(title=title, price=price,
                                 url=urljoin(listing_url, str(title_tag.get("href", "")))))
        return matches
