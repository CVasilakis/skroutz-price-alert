"""Tests for the Skroutz storage path rule.

The storage scrapability check (``_matches_product_path``) must agree with the shape the
client parses (``/s/<id>``), so an item the storage calls "scrapable" is one the client
can actually parse — no URL passes storage only to die in the client with InvalidURLError.
Importing the storage module is import-light (no transport library), so the manager can be
constructed directly here.
"""

import unittest

from core.scrapers.skroutz.storage import SkroutzDataManager


class TestSkroutzProductPath(unittest.TestCase):
    def setUp(self):
        # No file I/O is performed by _matches_product_path; a dummy path is fine.
        self.manager = SkroutzDataManager("/tmp/does-not-exist.json")

    def test_accepts_product_url(self):
        self.assertTrue(self.manager._matches_product_path("https://www.skroutz.gr/s/123/Name.html"))

    def test_rejects_non_product_numeric_url(self):
        # A numeric category URL (/c/123/) has a digit segment but is not a product page;
        # the old "/\d+/" rule wrongly accepted it, the "/s/\d+" rule rejects it.
        self.assertFalse(self.manager._matches_product_path("https://www.skroutz.gr/c/123/home-garden.html"))

    def test_rejects_non_numeric_url(self):
        self.assertFalse(self.manager._matches_product_path("https://www.skroutz.gr/search?keyphrase=home"))


if __name__ == "__main__":
    unittest.main()
