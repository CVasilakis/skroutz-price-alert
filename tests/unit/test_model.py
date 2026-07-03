"""Unit tests for the tracked-item read-side parsing (``from_dict``).

Covers the base parsing contract (defaults, the invalid ``target_price``
sentinel) and — the reason ``_base_field_kwargs`` exists — that a store adding
its own fields composes the base parsing instead of re-implementing it, so the
sentinel rule can never drift between stores.
"""

import unittest
from dataclasses import dataclass

from scrapers.base.model import BaseTrackedItem


@dataclass
class _SkuItem(BaseTrackedItem):
    """A store-specific item with one extra field, per the documented pattern."""
    sku: str = ""

    @classmethod
    def from_dict(cls, data):
        return cls(**cls._base_field_kwargs(data), sku=data.get("sku", ""))


class TestBaseFromDict(unittest.TestCase):
    def test_missing_keys_fall_back_to_defaults(self):
        item = BaseTrackedItem.from_dict({})
        self.assertEqual(item.name, "Unknown")
        self.assertEqual(item.url, "")
        self.assertEqual(item.target_price, 0.0)
        self.assertEqual(item.last_price, 0.0)
        self.assertFalse(item.skip)
        self.assertEqual(item.last_checked, "")

    def test_invalid_target_price_becomes_sentinel(self):
        item = BaseTrackedItem.from_dict({"target_price": "not-a-price"})
        self.assertEqual(item.target_price, -1.0)

    def test_string_target_price_is_parsed(self):
        item = BaseTrackedItem.from_dict({"target_price": "1.299,50 €"})
        self.assertEqual(item.target_price, 1299.50)


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

    def test_subclass_inherits_target_price_sentinel(self):
        # The sentinel rule flows through _base_field_kwargs — the subclass did not
        # (and must not) re-implement it.
        item = _SkuItem.from_dict({"target_price": None, "sku": "SKU-42"})
        self.assertEqual(item.target_price, -1.0)
        self.assertEqual(item.sku, "SKU-42")


if __name__ == "__main__":
    unittest.main()
