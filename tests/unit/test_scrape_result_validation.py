import math
import unittest

from core.exceptions import InvalidScrapeResultError, ScraperParseError
from core.scrapers.base.model import (
    ListingResult,
    OfferMatch,
    PriceResult,
    validate_scrape_result,
)


class TestScrapeResultValidation(unittest.TestCase):
    def assertInvalid(self, value):
        with self.assertRaises(InvalidScrapeResultError):
            validate_scrape_result(value)

    def test_error_participates_in_parser_retry_policy(self):
        self.assertTrue(issubclass(InvalidScrapeResultError, ScraperParseError))

    def test_valid_result_shapes_are_normalized(self):
        product = validate_scrape_result(PriceResult(10, "€"))
        empty = validate_scrape_result(ListingResult("€"))
        listing = validate_scrape_result(
            ListingResult("€", (OfferMatch("Offer", 5, "https://example.com/ad/1"),))
        )
        self.assertEqual(product.price, 10.0)
        self.assertEqual(empty.offers, ())
        self.assertEqual(listing.offers[0].price, 5.0)

    def test_wrong_shape_currency_metadata_and_offer_container_are_rejected(self):
        self.assertInvalid(object())
        self.assertInvalid(PriceResult(1, " "))
        self.assertInvalid(PriceResult(1, "€", metadata=[]))  # type: ignore[arg-type]
        self.assertInvalid(ListingResult("€", offers=[]))  # type: ignore[arg-type]
        self.assertInvalid(ListingResult("€", offers=(object(),)))  # type: ignore[arg-type]
        self.assertInvalid(ListingResult("€", offers=(OfferMatch(" ", 1, "https://x/a"),)))

    def test_prices_must_be_finite_nonnegative_numbers_not_bool(self):
        for price in (True, -1, math.nan, math.inf, "5"):
            with self.subTest(price=price):
                self.assertInvalid(PriceResult(price, "€"))  # type: ignore[arg-type]
                self.assertInvalid(
                    ListingResult("€", (OfferMatch("Offer", price, "https://example.com/a"),))
                )

    def test_offer_urls_must_be_absolute_http(self):
        self.assertInvalid(ListingResult("€", (OfferMatch("Offer", 5, "/a"),)))
        self.assertInvalid(ListingResult("€", (OfferMatch("Offer", 5, "ftp://example.com/a"),)))


if __name__ == "__main__":
    unittest.main()
