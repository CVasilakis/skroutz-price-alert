"""Tests for the insomnia search model and its row-identity helpers.

The virtual-URL round trip (``build_search_url`` / ``split_search_url``) and the
canonical row key (``search_row_key``) are the load-bearing pieces: they let the
single-URL scrape contract transport the filter terms, and they keep dedup
grouping (from the stored dict) and update caching (from the item URL) agreeing
for rows that share one listing URL. All import-light — no transport library.
"""

import unittest

from core.settings import STATUS_INVALID, resolve_spec
from core.scrapers.insomnia.model import (
    AdvertSearch, build_search_url, split_search_url, search_row_key,
    parse_terms, is_valid_terms_field,
)
from core.scrapers.insomnia.plugin import SPEC_MIN_ADVERT_PRICE

LISTING = "https://www.insomnia.gr/classifieds/category/174-google/"


class TestVirtualSearchUrl(unittest.TestCase):
    def test_round_trips_terms_through_the_url(self):
        virtual = build_search_url(LISTING, ["Pixel 9", "128"], ["9a"])
        listing, include, exclude = split_search_url(virtual)
        self.assertEqual(listing, LISTING)
        self.assertEqual(include, ["Pixel 9", "128"])
        self.assertEqual(exclude, ["9a"])

    def test_no_terms_leaves_the_url_untouched(self):
        self.assertEqual(build_search_url(LISTING, [], []), LISTING)

    def test_preserves_foreign_query_parameters(self):
        virtual = build_search_url(LISTING + "?page=2", ["Pixel"], [])
        listing, include, _ = split_search_url(virtual)
        self.assertEqual(listing, LISTING + "?page=2")
        self.assertEqual(include, ["Pixel"])


class TestSearchRowKey(unittest.TestCase):
    def test_same_listing_with_different_terms_gets_distinct_keys(self):
        self.assertNotEqual(
            search_row_key(LISTING, ["Pixel", "512"], []),
            search_row_key(LISTING, ["Pixel", "256"], []),
        )

    def test_key_ignores_term_order_and_case(self):
        self.assertEqual(
            search_row_key(LISTING, ["Pixel", "512"], ["9A"]),
            search_row_key(LISTING, ["512", "pixel"], ["9a"]),
        )

    def test_key_ignores_listing_query_and_fragment(self):
        self.assertEqual(
            search_row_key(LISTING + "?page=2#anchor", ["Pixel"], []),
            search_row_key(LISTING, ["Pixel"], []),
        )

    def test_include_and_exclude_terms_are_not_interchangeable(self):
        self.assertNotEqual(
            search_row_key(LISTING, ["9a"], []),
            search_row_key(LISTING, [], ["9a"]),
        )

    def test_dict_side_and_url_side_agree(self):
        # The invariant the storage hooks rely on: the key built from a stored
        # row equals the key built from that row's virtual item URL.
        include, exclude = ["Pixel 9", "128"], ["9a"]
        virtual = build_search_url(LISTING, include, exclude)
        listing, url_include, url_exclude = split_search_url(virtual)
        self.assertEqual(
            search_row_key(LISTING, include, exclude),
            search_row_key(listing, url_include, url_exclude),
        )


class TestTermsParsing(unittest.TestCase):
    def test_parse_terms_strips_and_drops_blank_or_non_string_entries(self):
        self.assertEqual(parse_terms([" Pixel ", "", 5, "128"]), ["Pixel", "128"])

    def test_parse_terms_of_non_list_is_empty(self):
        self.assertEqual(parse_terms("Pixel"), [])
        self.assertEqual(parse_terms(None), [])

    def test_terms_field_validation(self):
        self.assertTrue(is_valid_terms_field(None))
        self.assertTrue(is_valid_terms_field([]))
        self.assertTrue(is_valid_terms_field(["Pixel", "128"]))
        self.assertFalse(is_valid_terms_field("Pixel"))
        self.assertFalse(is_valid_terms_field(["Pixel", 5]))


class TestAdvertSearchFromDict(unittest.TestCase):
    def test_composes_base_fields_and_synthesizes_the_virtual_url(self):
        item = AdvertSearch.from_dict({
            "name": "Google Pixel 9 (128 GB)",
            "url": LISTING,
            "target_price": 200,
            "title_include": ["Pixel 9", "128"],
            "title_exclude": ["9a"],
        })
        self.assertEqual(item.name, "Google Pixel 9 (128 GB)")
        self.assertEqual(item.target_price, 200.0)
        self.assertEqual(item.title_include, ["Pixel 9", "128"])
        self.assertEqual(item.title_exclude, ["9a"])
        self.assertEqual(item.url, build_search_url(LISTING, ["Pixel 9", "128"], ["9a"]))

    def test_filterless_row_keeps_its_plain_url(self):
        item = AdvertSearch.from_dict({"name": "Any deal", "url": LISTING, "target_price": 100})
        self.assertEqual(item.url, LISTING)
        self.assertEqual(item.title_include, [])
        self.assertEqual(item.title_exclude, [])

    def test_invalid_target_price_keeps_the_base_sentinel(self):
        # The sentinel rule lives in _base_field_kwargs; composing it must not drift.
        item = AdvertSearch.from_dict({"name": "X", "url": LISTING, "target_price": "abc"})
        self.assertEqual(item.target_price, -1.0)

    def test_non_string_url_with_terms_is_safe(self):
        item = AdvertSearch.from_dict({
            "name": "X", "url": 123, "target_price": 100,
            "title_include": ["Pixel"],
        })
        self.assertEqual(item.url, "?title_include=Pixel")
        self.assertEqual(item.title_include, ["Pixel"])


class TestMinAdvertPriceSetting(unittest.TestCase):
    def test_non_finite_values_are_invalid(self):
        for raw in (float("nan"), float("inf"), float("-inf"), 10 ** 1000):
            with self.subTest(raw=raw):
                resolved = resolve_spec(
                    SPEC_MIN_ADVERT_PRICE, {"min_advert_price": raw}, None,
                )
                self.assertEqual(resolved.status, STATUS_INVALID)


if __name__ == "__main__":
    unittest.main()
