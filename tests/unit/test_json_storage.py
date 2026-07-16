"""Shared JSON-storage boundary tests.

These cases belong to the base backend rather than either concrete store: every JSON
plugin inherits the same document-shape, row-validation, cleanup, and save-merge rules.
"""

import json
import os
import tempfile
import unittest

from core.exceptions import StorageFileError
from core.scrapers.base.storage import JsonProductDataManager
from support import fake_plugin


URL = "https://store.example/p/1"


class _DataManager(JsonProductDataManager):
    def _matches_product_path(self, url: str) -> bool:
        return url.startswith("https://store.example/p/")


class JsonStorageCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "store.json")
        self.plugin = fake_plugin(domains=("store.example",))

    def _write(self, data) -> None:
        with open(self.path, "w") as file:
            json.dump(data, file)

    def _load(self, products) -> _DataManager:
        self._write({"products": products})
        manager = _DataManager(self.path, self.plugin)
        manager.load()
        return manager


class TestDocumentShape(JsonStorageCase):
    def test_invalid_top_level_shapes_raise_storage_error(self):
        for data in (None, [], {}, {"products": None}, {"products": {}}):
            with self.subTest(data=data):
                self._write(data)
                manager = _DataManager(self.path, self.plugin)
                with self.assertRaises(StorageFileError):
                    manager.load()

    def test_invalid_utf8_raises_storage_error(self):
        with open(self.path, "wb") as file:
            file.write(b"\xff")
        with self.assertRaises(StorageFileError):
            _DataManager(self.path, self.plugin).load()


class TestRowBoundary(JsonStorageCase):
    def test_non_object_rows_are_faulty_preserved_and_never_deduplicated(self):
        malformed = [None, None, 7, "row", []]
        manager = self._load(malformed + [
            {"name": "Good", "url": URL, "target_price": 10},
        ])

        self.assertEqual(manager.get_faulty_indices(), [1, 2, 3, 4, 5])
        manager.clean_storage()
        self.assertEqual(manager.get_items()[:5], malformed)
        manager.save()

        with open(self.path) as file:
            saved = json.load(file)["products"]
        self.assertEqual(saved[:5], malformed)
        self.assertEqual(saved[5]["name"], "Good")

    def test_mapping_field_types_and_malformed_url_are_reported(self):
        rows = [
            {"name": None, "url": URL, "target_price": 10},
            {"name": "Skip", "url": URL, "target_price": 10, "skip": "false"},
            {"name": "Price", "url": URL, "target_price": 10, "last_price": float("nan")},
            {"name": "Time", "url": URL, "target_price": 10, "last_checked": 42},
            {"name": "Time", "url": URL, "target_price": 10, "last_checked": "bad"},
            {"name": "Target", "url": URL, "target_price": float("inf")},
            {"name": "URL", "url": "https://[", "target_price": 10},
            {"name": "Good", "url": URL, "target_price": 10},
        ]
        manager = self._load(rows)

        self.assertEqual(manager.get_faulty_indices(), [1, 2, 3, 4, 5, 6, 7])

    def test_model_normalizes_unsafe_mapping_fields(self):
        manager = self._load([])
        item = manager.parse_item({
            "name": None,
            "url": 123,
            "target_price": float("inf"),
            "last_price": [],
            "skip": "false",
            "last_checked": 42,
        })

        self.assertEqual(item.name, "Unknown")
        self.assertEqual(item.url, "")
        self.assertEqual(item.target_price, -1.0)
        self.assertEqual(item.last_price, 0.0)
        self.assertFalse(item.skip)
        self.assertEqual(item.last_checked, "42")


class TestSaveBoundary(JsonStorageCase):
    def test_valid_json_with_invalid_structure_is_preserved_and_rejected(self):
        manager = self._load([
            {"name": "Good", "url": URL, "target_price": 10},
        ])
        manager.update_item(URL, last_price=5.0)
        with open(self.path, "w") as file:
            file.write("null")

        with self.assertRaises(StorageFileError):
            manager.save()
        with open(self.path) as file:
            self.assertIsNone(json.load(file))

    def test_invalid_utf8_is_backed_up_and_self_healed(self):
        manager = self._load([
            {"name": "Good", "url": URL, "target_price": 10},
        ])
        manager.update_item(URL, last_price=5.0)
        with open(self.path, "wb") as file:
            file.write(b"\xff")

        manager.save()

        with open(self.path + ".corrupt", "rb") as file:
            self.assertEqual(file.read(), b"\xff")
        with open(self.path) as file:
            saved = json.load(file)
        self.assertEqual(saved["products"][0]["last_price"], 5.0)


if __name__ == "__main__":
    unittest.main()
