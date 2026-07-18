"""Unit tests for tracked-item parsing and plugin-owned identity."""

import unittest
from dataclasses import dataclass

from core.scrapers.base.model import BaseTrackedItem


@dataclass
class _SkuItem(BaseTrackedItem):
    """A store-specific item with one extra field, per the documented pattern."""
    sku: str = ""

    @classmethod
    def parse_extra_fields(cls, data):
        return {"sku": data.get("sku", "")}

    def identity_key(self):
        return f"{super().identity_key()}|{self.sku}"


class TestBaseFromDict(unittest.TestCase):
    def test_missing_keys_fall_back_to_defaults(self):
        item = BaseTrackedItem.from_dict({})
        self.assertEqual(item.name, "Unknown")
        self.assertEqual(item.url, "")
        self.assertEqual(item.target_price, 0.0)
        self.assertEqual(item.last_price, 0.0)
        self.assertFalse(item.skip)
        self.assertEqual(item.last_checked, "")

    def test_invalid_target_price_uses_safe_default(self):
        item = BaseTrackedItem.from_dict({"target_price": "not-a-price"})
        self.assertEqual(item.target_price, 0.0)

    def test_string_target_price_is_parsed(self):
        item = BaseTrackedItem.from_dict({"target_price": "1.299,50 €"})
        self.assertEqual(item.target_price, 1299.50)

    def test_unsafe_field_types_are_normalized_for_consumers(self):
        item = BaseTrackedItem.from_dict({
            "name": None,
            "url": 123,
            "last_price": {},
            "skip": "false",
            "last_checked": 42,
        })
        self.assertEqual(item.name, "Unknown")
        self.assertEqual(item.url, "")
        self.assertEqual(item.last_price, 0.0)
        self.assertFalse(item.skip)
        self.assertEqual(item.last_checked, "42")


class TestSubclassComposition(unittest.TestCase):
    def test_subclass_reads_base_fields_and_its_own(self):
        item = _SkuItem.from_dict({
            "name": "Widget",
            "url": "https://store.example/p/1",
            "target_price": 25.0,
            "sku": "SKU-42",
        })
        self.assertEqual(item.name, "Widget")
        self.assertEqual(item.url, "https://store.example/p/1")
        self.assertEqual(item.target_price, 25.0)
        self.assertEqual(item.sku, "SKU-42")

    def test_subclass_inherits_base_normalization(self):
        item = _SkuItem.from_dict({"target_price": None, "sku": "SKU-42"})
        self.assertEqual(item.target_price, 0.0)
        self.assertEqual(item.sku, "SKU-42")

    def test_subclass_extends_identity_in_one_place(self):
        item = _SkuItem.from_dict({"url": "https://store.example/p/1#fragment", "sku": "SKU-42"})
        self.assertEqual(item.identity_key(), "https://store.example/p/1|SKU-42")


if __name__ == "__main__":
    unittest.main()
