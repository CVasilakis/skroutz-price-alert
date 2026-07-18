"""Tests for the insomnia storage: path rule, composite row identity, write-back.

Several insomnia rows legitimately share one listing URL and differ only in
their filter terms, so the manager overrides the base row-identity hooks. These
tests pin the behavior that makes that safe: dedup keeps same-URL rows with
different terms (and still collapses true duplicates), and a scraped update
lands on exactly the row it was scraped for. Importing the storage module is
import-light (no transport library), so the manager runs on real temp files here.
"""

import json
import os
import shutil
import tempfile
import unittest

from core.scrapers.registry import ScraperRegistry
from core.scrapers.insomnia.storage import InsomniaDataManager

LISTING = "https://www.insomnia.gr/classifieds/category/174-google/"


def _row(name, include=None, exclude=None, url=LISTING, target=100):
    row = {"name": name, "url": url, "target_price": target}
    if include is not None:
        row["title_include"] = include
    if exclude is not None:
        row["title_exclude"] = exclude
    return row


class InsomniaStorageCase(unittest.TestCase):
    def _manager(self, products):
        tmp_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, tmp_dir, ignore_errors=True)
        plugin = ScraperRegistry.get_plugin("insomnia")
        filepath = os.path.join(tmp_dir, plugin.config_filename)
        with open(filepath, "w") as f:
            json.dump({"products": products}, f)
        manager = InsomniaDataManager(filepath, plugin=plugin)
        manager.load()
        return manager


class TestInsomniaProductPath(unittest.TestCase):
    def setUp(self):
        # No file I/O is performed by _matches_product_path; a dummy path is fine.
        self.manager = InsomniaDataManager("/tmp/does-not-exist.json")

    def test_accepts_classifieds_listing_url(self):
        self.assertTrue(self.manager._matches_product_path(LISTING))

    def test_rejects_non_classifieds_url(self):
        self.assertFalse(self.manager._matches_product_path("https://www.insomnia.gr/forums/topic/12345/"))


class TestSameUrlRowsSurviveDedup(InsomniaStorageCase):
    def test_different_terms_on_one_listing_are_distinct_rows(self):
        manager = self._manager([
            _row("Pixel 512", include=["Pixel", "512"]),
            _row("Pixel 256", include=["Pixel", "256"]),
            _row("Any Pixel"),
        ])
        manager.clean_storage()
        self.assertEqual([r["name"] for r in manager.get_items()],
                         ["Pixel 512", "Pixel 256", "Any Pixel"])

    def test_true_duplicates_still_collapse(self):
        manager = self._manager([
            _row("Pixel 512", include=["Pixel", "512"]),
            _row("Pixel 512 again", include=["512", "pixel"]),  # same terms, reordered/re-cased
        ])
        manager.clean_storage()
        self.assertEqual([r["name"] for r in manager.get_items()], ["Pixel 512"])


class TestWriteBackLandsOnTheRightRow(InsomniaStorageCase):
    def test_update_reaches_only_the_scraped_row(self):
        manager = self._manager([
            _row("Pixel 512", include=["Pixel", "512"]),
            _row("Pixel 256", include=["Pixel", "256"]),
        ])
        manager.clean_storage()
        # The orchestrator updates with the parsed row, whose identity includes its filters.
        item = manager.parse_item(manager.get_items()[1])
        manager.update_item(item, last_price=199.0, last_checked="01-01-2026 00:00:00")
        manager.save()

        with open(manager.filepath) as f:
            saved = json.load(f)["products"]
        self.assertNotIn("last_price", saved[0])
        self.assertEqual(saved[1]["last_price"], 199.0)
        self.assertEqual(saved[1]["last_checked"], "01-01-2026 00:00:00")


class TestTermsFieldValidation(InsomniaStorageCase):
    def test_malformed_terms_field_marks_the_row_faulty(self):
        manager = self._manager([
            _row("Good", include=["Pixel"]),
            _row("Bad include", include="Pixel"),        # string, not a list
            _row("Bad exclude", exclude=["ok", 5]),      # non-string entry
        ])
        self.assertEqual(manager.get_faulty_indices(), [2, 3])


if __name__ == "__main__":
    unittest.main()
