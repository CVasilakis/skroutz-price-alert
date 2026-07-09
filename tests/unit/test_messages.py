"""Sanity checks for the user-facing message catalog (core.messages).

Deliberately does NOT pin exact wording - that would recreate the duplicated-literal
problem the catalog exists to solve. The final rendered text is pinned by the UI
snapshot suite; these tests only guard the catalog's structural invariants: every
entry produces a non-empty string, and no two fixed constants collapse into the
same wording.
"""

import inspect
import unittest

from core import messages


def _public_constants() -> dict[str, str]:
    """Returns the catalog's UPPER_CASE string constants, keyed by name."""
    return {
        name: value for name, value in vars(messages).items()
        if name.isupper() and not name.startswith("_")
    }


def _public_functions() -> dict[str, object]:
    """Returns the catalog's message functions, keyed by name."""
    return {
        name: fn for name, fn in vars(messages).items()
        if not name.startswith("_") and inspect.isfunction(fn)
    }


class TestMessageCatalog(unittest.TestCase):
    def test_catalog_is_not_empty(self):
        self.assertTrue(_public_constants())
        self.assertTrue(_public_functions())

    def test_constants_are_nonempty_strings(self):
        for name, value in _public_constants().items():
            with self.subTest(constant=name):
                self.assertIsInstance(value, str)
                self.assertTrue(value.strip())

    def test_constants_are_pairwise_distinct(self):
        constants = _public_constants()
        values = list(constants.values())
        self.assertEqual(len(values), len(set(values)),
                         f"duplicate wording between constants in {sorted(constants)}")

    def test_functions_return_nonempty_strings(self):
        # Every message function is exercised with placeholder arguments matched
        # by parameter count, so a formatting regression (e.g. a stray brace or a
        # dropped parameter) fails here without pinning any wording.
        sample_args = {
            "stale_note": ("01-01-2026 00:00:00", 48),
            "succeeded_on_attempt": (2, 3),
            "invalid_target_price": ("abc", "€"),
            "missing_target_price": ("€",),
            "skipping_warning": ("ProductNotFoundError",),
            "attempt_note": (1, "ServerError"),
            "errors_log_pointer": ("skroutz",),
            "plugin_dependency_detail": ("skroutz", "tls_client"),
            "save_failed": ("skroutz.json",),
            "not_found_detail": (404,),
            "rate_limited_detail": (429,),
            "server_error_detail": (503,),
            "http_failed_detail": (418,),
        }
        functions = _public_functions()
        self.assertEqual(sorted(functions), sorted(sample_args),
                         "sample_args must cover exactly the catalog's public functions")
        for name, fn in functions.items():
            with self.subTest(function=name):
                result = fn(*sample_args[name])
                self.assertIsInstance(result, str)
                self.assertTrue(result.strip())


if __name__ == "__main__":
    unittest.main()
