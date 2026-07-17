"""Tests for the single successful-scrape validation boundary."""

import math
import unittest

from core.exceptions import InvalidScrapeResultError, ScraperParseError
from core.scrapers.base.model import AdvertMatch, ScrapeResult, validate_scrape_result


class TestScrapeResultValidation(unittest.TestCase):
    def assertInvalid(self, value):
        with self.assertRaises(InvalidScrapeResultError):
            validate_scrape_result(value)

    def test_invalid_result_error_is_retryable_parse_error(self):
        self.assertTrue(issubclass(InvalidScrapeResultError, ScraperParseError))

    def test_valid_classic_no_match_and_listing_results(self):
        classic = validate_scrape_result(ScrapeResult(10, "€"))
        self.assertEqual(classic.price, 10.0)
        no_match = validate_scrape_result(ScrapeResult(None, "€"))
        self.assertIsNone(no_match.price)
        listing = validate_scrape_result(ScrapeResult(
            5, "€", [AdvertMatch("Offer", 5, "https://example.com/ad/1")]
        ))
        self.assertEqual(listing.price, 5.0)
        self.assertEqual(listing.matches[0].price, 5.0)

    def test_wrong_shapes_and_strings_are_rejected(self):
        self.assertInvalid(None)
        self.assertInvalid(ScrapeResult(1, " "))
        self.assertInvalid(ScrapeResult(1, "€", metadata=[]))
        self.assertInvalid(ScrapeResult(1, "€", matches=()))
        self.assertInvalid(ScrapeResult(1, "€", matches=[object()]))
        self.assertInvalid(ScrapeResult(1, "€", matches=[AdvertMatch(" ", 1, "https://x/a")]))

    def test_invalid_prices_are_rejected_everywhere(self):
        for price in (True, -1, math.nan, math.inf, -math.inf, "1", 10 ** 10000):
            with self.subTest(price=price):
                self.assertInvalid(ScrapeResult(price, "€"))
                self.assertInvalid(ScrapeResult(
                    1, "€", [AdvertMatch("Offer", price, "https://example.com/a")]
                ))

    def test_listing_consistency_and_advert_url_are_enforced(self):
        match = AdvertMatch("Offer", 5, "https://example.com/a")
        self.assertInvalid(ScrapeResult(None, "€", [match]))
        self.assertInvalid(ScrapeResult(6, "€", [match]))
        self.assertInvalid(ScrapeResult(5, "€", [AdvertMatch("Offer", 5, "/a")]))
        self.assertInvalid(ScrapeResult(5, "€", [AdvertMatch("Offer", 5, "ftp://example.com/a")]))


if __name__ == "__main__":
    unittest.main()
