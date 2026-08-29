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
        name: value
        for name, value in vars(messages).items()
        if name.isupper() and not name.startswith("_")
    }


def _public_functions() -> dict[str, object]:
    """Returns the catalog's message functions, keyed by name."""
    return {
        name: fn
        for name, fn in vars(messages).items()
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
        self.assertEqual(
            len(values),
            len(set(values)),
            f"duplicate wording between constants in {sorted(constants)}",
        )

    def test_functions_return_nonempty_strings(self):
        # Every message function is exercised with placeholder arguments matched
        # by parameter count, so a formatting regression (e.g. a stray brace or a
        # dropped parameter) fails here without pinning any wording.
        sample_args = {
            "stale_note": ("01-01-2026 00:00:00", 48),
            "succeeded_on_attempt": (2, 3),
            "advert_matches_note": (3, 2),
            "advert_notified_ok": (2,),
            "advert_notified_fail": (1, 2),
            "advert_alerts_suppressed": (1,),
            "skipping_warning": ("ResourceNotFoundError",),
            "attempt_note": (1, "ServerError"),
            "errors_log_pointer": ("skroutz",),
            "plugin_dependency_detail": ("skroutz", "tls_client"),
            "plugin_lifecycle_failed": ("RuntimeError",),
            "state_load_failed": ("skroutz",),
            "state_save_failed": ("skroutz",),
            "lock_storage_failed": (),
            "lock_storage_unavailable": ("skroutz",),
            "missing_config": ("config/skroutz.json",),
            "malformed_json": ("config/skroutz.json", 2, 4),
            "invalid_utf8": ("config/skroutz.json",),
            "storage_read_permission": ("config/skroutz.json",),
            "storage_read_failed": ("config/skroutz.json",),
            "json_object_required": ("config/skroutz.json",),
            "storage_save_permission": ("state/skroutz.json",),
            "storage_save_failed": ("state/skroutz.json",),
            "invalid_state": ("state/skroutz.json",),
            "unsupported_config_keys": ("config/skroutz.json",),
            "config_schema_version_invalid": ("config/skroutz.json", 1),
            "items_array_required": ("config/skroutz.json",),
            "settings_object_required": ("config/skroutz.json",),
            "unsupported_settings": ("config/skroutz.json",),
            "required_settings_invalid": ("config/skroutz.json",),
            "settings_invalid": ("config/skroutz.json",),
            "notifications_object_required": ("config/general.json",),
            "unsupported_notification_settings": ("config/general.json",),
            "notification_urls_array_required": ("config/general.json",),
            "notification_url_string_required": ("config/general.json",),
            "notifications_invalid": ("config/general.json",),
            "misconfigured_items": ("config/skroutz.json",),
            "retry_preparation_note": (2, "OSError"),
            "not_found_detail": (404,),
            "rate_limited_detail": (429,),
            "server_error_detail": (503,),
            "http_failed_detail": (418,),
        }
        functions = _public_functions()
        self.assertEqual(
            sorted(functions),
            sorted(sample_args),
            "sample_args must cover exactly the catalog's public functions",
        )
        for name, fn in functions.items():
            with self.subTest(function=name):
                result = fn(*sample_args[name])
                self.assertIsInstance(result, str)
                self.assertTrue(result.strip())


if __name__ == "__main__":
    unittest.main()
