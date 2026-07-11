"""Tests for the insomnia client's listing parsing and filter semantics.

Exercises ``scrape_product`` against fixture HTML mirroring the insomnia.gr
classifieds markup (advert cards, the "Ζήτηση" want-to-buy marker, the
"Επικοινωνία" no-price placeholder, EU-formatted prices) with the network
mocked out. Pins the filter contract — ALL includes / ANY exclude,
case-insensitive — the ``min_advert_price`` floor, the cheapest-first result,
the ``price=None`` no-match outcome, and the loud parse failure on foreign markup.
"""

import unittest
from unittest import mock

import pytest

pytest.importorskip("bs4", reason="insomnia dependencies not installed")
pytest.importorskip("tls_client", reason="insomnia dependencies not installed")

from core.exceptions import InvalidURLError, RateLimitError, ScraperParseError
from core.settings import ResolvedSettings, resolve_spec
from core.scrapers.insomnia.client import InsomniaClient
from core.scrapers.insomnia.model import build_search_url
from core.scrapers.insomnia.plugin import SPEC_MIN_ADVERT_PRICE

LISTING = "https://www.insomnia.gr/classifieds/category/174-google/"

LISTING_HTML = """
<html><body><ul>
  <li class="insAdvertsList">
    <h4><a href="/classifieds/ad/101-pixel-9/">Google Pixel 9 128GB σφραγισμένο</a></h4>
    <p class="cFilePrice">450,00 €</p>
  </li>
  <li class="insAdvertsList">
    <h4><a href="/classifieds/ad/102-pixel-9a/">Google PIXEL 9a 128GB</a></h4>
    <p class="cFilePrice">350,00 €</p>
  </li>
  <li class="insAdvertsList">
    <span class="insRequest">Ζήτηση</span>
    <h4><a href="/classifieds/ad/103-wanted/">Ζητείται Pixel 9 128GB</a></h4>
    <p class="cFilePrice">100,00 €</p>
  </li>
  <li class="insAdvertsList">
    <h4><a href="/classifieds/ad/104-swap/">Google Pixel 9 128GB μόνο ανταλλαγή</a></h4>
    <p class="cFilePrice">Επικοινωνία</p>
  </li>
  <li class="insAdvertsList">
    <h4><a href="/classifieds/ad/105-bait/">Google Pixel 9 128GB ευκαιρία!!!</a></h4>
    <p class="cFilePrice">1,00 €</p>
  </li>
  <li class="insAdvertsList">
    <h4><a href="/classifieds/ad/106-pro/">Google Pixel 9 Pro XL 512GB</a></h4>
    <p class="cFilePrice">1.234,56 €</p>
  </li>
</ul></body></html>
"""


def _settings(min_advert_price):
    """Real resolved settings carrying the min_advert_price floor."""
    resolved = resolve_spec(SPEC_MIN_ADVERT_PRICE,
                            {"min_advert_price": min_advert_price}, None)
    return ResolvedSettings([(SPEC_MIN_ADVERT_PRICE, resolved)])


class _FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code


def _client(html=LISTING_HTML, status_code=200, min_advert_price=30):
    """An InsomniaClient with the HTTP session replaced by a canned response."""
    client = InsomniaClient(_settings(min_advert_price))
    client.session = mock.Mock()
    client.session.get.return_value = _FakeResponse(html, status_code)
    return client


class TestScrapeProduct(unittest.TestCase):
    def test_filters_and_returns_the_matching_adverts_cheapest_first(self):
        client = _client()
        result = client.scrape_product(build_search_url(LISTING, ["pixel 9"], ["9a"]))

        # 102 is excluded ("9a", case-insensitively), 103 is a Ζήτηση request,
        # 104 has no price, 105 is under the 30€ floor — 101 and 106 survive.
        self.assertEqual([m.url.rsplit("/", 2)[-2] for m in result.matches],
                         ["101-pixel-9", "106-pro"])
        self.assertEqual(result.price, 450.0)
        self.assertEqual(result.currency, "€")
        # The listing is fetched bare: the filter params never reach the site.
        self.assertEqual(client.session.get.call_args[0][0], LISTING)

    def test_include_terms_must_all_match(self):
        client = _client()
        result = client.scrape_product(build_search_url(LISTING, ["Pixel 9", "512"], []))
        self.assertEqual([m.title for m in result.matches], ["Google Pixel 9 Pro XL 512GB"])

    def test_eu_price_format_is_parsed(self):
        client = _client()
        result = client.scrape_product(build_search_url(LISTING, ["512"], []))
        self.assertEqual(result.price, 1234.56)

    def test_advert_links_resolve_against_the_listing_url(self):
        client = _client()
        result = client.scrape_product(build_search_url(LISTING, ["512"], []))
        self.assertEqual(result.matches[0].url,
                         "https://www.insomnia.gr/classifieds/ad/106-pro/")

    def test_no_matching_advert_is_a_priceless_success(self):
        client = _client()
        result = client.scrape_product(build_search_url(LISTING, ["iPhone"], []))
        self.assertIsNone(result.price)
        self.assertEqual(result.matches, [])

    def test_disabled_floor_keeps_bait_priced_adverts(self):
        client = _client(min_advert_price=0)
        result = client.scrape_product(build_search_url(LISTING, ["ευκαιρία"], []))
        self.assertEqual(result.price, 1.0)

    def test_page_without_advert_cards_is_a_parse_error(self):
        client = _client(html="<html><body><p>maintenance</p></body></html>")
        with self.assertRaises(ScraperParseError):
            client.scrape_product(LISTING)

    def test_advert_card_without_price_element_is_a_parse_error(self):
        broken = '<li class="insAdvertsList"><h4><a href="/x/">Ad</a></h4></li>'
        client = _client(html=broken)
        with self.assertRaises(ScraperParseError):
            client.scrape_product(LISTING)

    def test_non_classifieds_url_is_invalid(self):
        client = _client()
        with self.assertRaises(InvalidURLError):
            client.scrape_product("https://www.insomnia.gr/forums/topic/1/")

    def test_http_status_maps_through_the_shared_contract(self):
        client = _client(status_code=429)
        with self.assertRaises(RateLimitError):
            client.scrape_product(LISTING)


if __name__ == "__main__":
    unittest.main()
