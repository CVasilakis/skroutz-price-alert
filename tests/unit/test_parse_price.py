"""Table-driven tests for ``utils.parse_price``.

This is the single price-normalization routine shared by config validation (target
prices) and every scraper (scraped prices), so its documented contract is pinned here
directly rather than only through configuration and scraper call sites.

The load-bearing, counter-intuitive rule is the separator handling: the right-most
``.``/``,`` is the decimal separator and every other one is dropped as grouping, so a
value with a *single* separator reads as a decimal (``"1,234"`` -> ``1.234``). These
cases exist to catch a regression in that rule.
"""

import unittest

from core.scrapers.pricing import parse_price


class TestParsePrice(unittest.TestCase):
    # (raw, expected). Covers passthrough, currency/quote/space stripping, European vs
    # US grouping, sign, and the single-separator "reads as decimal" rule.
    VALID = [
        # Numeric passthrough.
        (0, 0.0), (25, 25.0), (25.5, 25.5), (-3, -3.0),
        # Bare strings.
        ("5", 5.0), ("5.0", 5.0), ("  5.00  ", 5.0),
        # Currency symbols, quotes and whitespace are stripped.
        ("1.299,50 €", 1299.50), ("€1299.50", 1299.50),
        ('"1299,50"', 1299.50), ("$1,299.00", 1299.00),
        # European grouping (right-most separator is the decimal).
        ("1.299,00", 1299.00), ("1.234.567,89", 1234567.89),
        # US grouping.
        ("1,299.00", 1299.00), ("1,234,567.89", 1234567.89),
        # Single separator is the decimal, NOT a thousands group (documented rule).
        ("1,234", 1.234), ("1.234", 1.234),
        # Sign is preserved through the cleaning pass.
        ("-5,00", -5.0), ("-1.299,50 €", -1299.50),
        # No fractional digits after the separator.
        ("1.000,", 1000.0), ("1,299.", 1299.0),
    ]
    # Anything that cannot be read as a number degrades to None (the callers turn that
    # into their own sentinel / skip behavior).
    INVALID = [
        None, True, False, "", "   ", "abc", "not-a-price",
        "€", "-", ",", ".", [1, 2], {"price": 5}, object(),
        float("nan"), float("inf"), float("-inf"), "NaN", "Infinity",
        10 ** 1000,
    ]

    def test_valid(self):
        for raw, expected in self.VALID:
            with self.subTest(raw=raw):
                self.assertAlmostEqual(parse_price(raw), expected)

    def test_invalid_returns_none(self):
        for raw in self.INVALID:
            with self.subTest(raw=raw):
                self.assertIsNone(parse_price(raw))

    def test_bool_is_not_treated_as_int(self):
        # bool is an int subclass; the guard must reject it rather than return 1.0/0.0,
        # so a stray `true` in a config never becomes a price.
        self.assertIsNone(parse_price(True))
        self.assertIsNone(parse_price(False))


if __name__ == "__main__":
    unittest.main()
