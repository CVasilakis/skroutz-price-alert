"""Table-driven tests for the settings normalizers.

These are pure, stdlib-only functions with a lot of edge cases (the bool-vs-int
subclass traps in particular), so they get exhaustive case tables here.

Run from the repo root with the project interpreter::

    PYTHONPATH=src/core venv/bin/python3 -m unittest discover -s tests
"""

import unittest

from core.scrapers.base.settings import (
    normalize_interval, normalize_retention_days, normalize_bool,
)


class TestNormalizeInterval(unittest.TestCase):
    VALID = [
        ("1h", "1h"), ("1 h", "1h"), ("1Hour", "1h"), ("1 hour", "1h"),
        ("60m", "1h"), ("60 minutes", "1h"), ("hourly", "1h"), ("HOURLY", "1h"),
        ("daily", "24h"), ("1d", "24h"), ("1 day", "24h"), ("1440m", "24h"),
        ("15m", "15m"), ("30m", "30m"), ("30 minutes", "30m"),
        ("half-hourly", "30m"), ("half_hourly", "30m"), ("halfhour", "30m"),
        ("2h", "2h"), ("12h", "12h"), ("24h", "24h"),
    ]
    INVALID = ["", "   ", "3h", "45m", "0", "0m", "2d", "1w", "abc",
               "1 month", "-1h", None, 60, 1.5, True]

    def test_valid(self):
        for raw, expected in self.VALID:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_interval(raw), expected)

    def test_invalid(self):
        for raw in self.INVALID:
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_interval(raw))


class TestNormalizeRetentionDays(unittest.TestCase):
    VALID = [
        (7, 7), (1, 1), (30, 30), (15, 15),
        ("4", 4), ("4d", 4), ("4 d", 4), ("4day", 4), ("4 days", 4),
        ("30", 30), (" 1 ", 1),
    ]
    INVALID = [0, 31, -1, 100, "0", "31", "4h", "4 months", "4w",
               4.0, True, False, "abc", "", None, [4]]

    def test_valid(self):
        for raw, expected in self.VALID:
            with self.subTest(raw=raw):
                self.assertEqual(normalize_retention_days(raw), expected)

    def test_invalid(self):
        for raw in self.INVALID:
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_retention_days(raw))

    def test_bool_is_rejected_not_coerced(self):
        # bool is an int subclass; True must NOT read as the day count 1.
        self.assertIsNone(normalize_retention_days(True))
        self.assertIsNone(normalize_retention_days(False))


class TestNormalizeBool(unittest.TestCase):
    TRUE = [True, 1, "true", "yes", "on", "1", "TRUE", "  Yes  ", "ON"]
    FALSE = [False, 0, "false", "no", "off", "0", "FALSE", "  no  "]
    INVALID = [2, -1, 1.0, 0.0, "maybe", "tru", "", "2", None, [], "y"]

    def test_true(self):
        for raw in self.TRUE:
            with self.subTest(raw=raw):
                self.assertIs(normalize_bool(raw), True)

    def test_false(self):
        for raw in self.FALSE:
            with self.subTest(raw=raw):
                self.assertIs(normalize_bool(raw), False)

    def test_invalid(self):
        for raw in self.INVALID:
            with self.subTest(raw=raw):
                self.assertIsNone(normalize_bool(raw))

    def test_string_false_is_not_truthy_cast(self):
        # The footgun this normalizer exists to avoid: bool("false") is True.
        self.assertIs(normalize_bool("false"), False)


if __name__ == "__main__":
    unittest.main()
