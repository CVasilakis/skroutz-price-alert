import unittest

from core.settings import STATUS_INVALID, resolve_spec
from core.scrapers.insomnia.model import (
    AdvertSearch,
    is_valid_terms_field,
    parse_terms,
    search_row_key,
)
from core.scrapers.insomnia.plugin import SPEC_MIN_ADVERT_PRICE

LISTING = "https://www.insomnia.gr/classifieds/category/174-google/"


class TestSearchIdentity(unittest.TestCase):
    def test_same_listing_with_different_terms_gets_distinct_keys(self):
        self.assertNotEqual(
            search_row_key(LISTING, ["Pixel", "512"], []),
            search_row_key(LISTING, ["Pixel", "256"], []),
        )

    def test_key_ignores_term_order_case_query_and_fragment(self):
        self.assertEqual(
            search_row_key(LISTING + "?page=2#anchor", ["Pixel", "512"], ["9A"]),
            search_row_key(LISTING, ["512", "pixel"], ["9a"]),
        )

    def test_include_and_exclude_are_not_interchangeable(self):
        self.assertNotEqual(
            search_row_key(LISTING, ["9a"], []),
            search_row_key(LISTING, [], ["9a"]),
        )


class TestTermsParsing(unittest.TestCase):
    def test_parsing_and_validation(self):
        self.assertEqual(parse_terms([" Pixel ", "", 5, "128"]), ["Pixel", "128"])
        self.assertEqual(parse_terms("Pixel"), [])
        self.assertTrue(is_valid_terms_field(None))
        self.assertTrue(is_valid_terms_field(["Pixel", "128"]))
        self.assertFalse(is_valid_terms_field(["Pixel", 5]))


class TestAdvertSearchFromDict(unittest.TestCase):
    def test_base_and_extra_fields_are_parsed_without_rewriting_url(self):
        item = AdvertSearch.from_dict({
            "name": "Pixel", "url": LISTING, "target_price": 200,
            "title_include": ["Pixel 9", "128"], "title_exclude": ["9a"],
        })
        self.assertEqual(item.url, LISTING)
        self.assertEqual(item.title_include, ["Pixel 9", "128"])
        self.assertEqual(item.title_exclude, ["9a"])
        self.assertEqual(
            item.identity_key(),
            search_row_key(LISTING, ["Pixel 9", "128"], ["9a"]),
        )

    def test_invalid_target_price_uses_safe_zero(self):
        item = AdvertSearch.from_dict({"name": "X", "url": LISTING, "target_price": "abc"})
        self.assertEqual(item.target_price, 0.0)

    def test_non_string_url_is_safe(self):
        item = AdvertSearch.from_dict({"name": "X", "url": 123, "target_price": 100})
        self.assertEqual(item.url, "")


class TestMinAdvertPriceSetting(unittest.TestCase):
    def test_non_finite_values_are_invalid(self):
        for raw in (float("nan"), float("inf"), float("-inf"), 10 ** 1000):
            with self.subTest(raw=raw):
                resolved = resolve_spec(SPEC_MIN_ADVERT_PRICE, {"min_advert_price": raw}, None)
                self.assertEqual(resolved.status, STATUS_INVALID)


if __name__ == "__main__":
    unittest.main()
