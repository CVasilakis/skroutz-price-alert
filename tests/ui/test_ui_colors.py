"""Semantic border-color assertions.

The plain-text snapshots lock layout and (via the header) record the border color, but
this module asserts the color *decision* directly: every scenario resolves to a valid
border color, and a curated, representative set resolves to a specific expected color —
covering each color-decision branch across all surfaces (for the shell surfaces the
decision is the exit-code mapping: 0 -> green, anything else -> red). Inner styled spans (a green
drop price, a red error row) are verified visually via ``gallery.py`` and structurally via
the snapshot text.
"""

import unittest

from ui.catalog import ALL_SCENARIOS

VALID_COLORS = {"green", "yellow", "red", "blue"}

# Representative scenarios, each pinned to the color its decision branch must produce.
EXPECTED_COLORS = {
    # RUN: drop celebration wins; OK-completed settles green; warning -> yellow;
    # error -> red; in-progress -> blue.
    "run__success_drop_notified": "green",
    "run__success_drop_notify_failed": "yellow",
    "run__listing_matches_notify_failed": "yellow",
    "run__success_ok": "green",
    "run__no_target_zero": "yellow",
    "run__settings_each_invalid": "yellow",
    "run__failure_all_parse": "red",
    "run__skip_invalid_url_error": "red",
    "run__system_lock_held": "red",
    "run__interrupt_during_scraping": "red",
    "run__config_faulty": "yellow",         # a faulty 'Config' row tints the panel yellow
    "run__config_failed_skip": "red",       # a failed 'Config' row (skipped target) -> red
    "e2e-run__mixed_unsafe_and_valid": "yellow",
    "run__scraping_spinner": "blue",
    "run__sleeping_pacing": "blue",
    # STATUS
    "status__service_healthy": "green",
    "status__service_invalid_retention": "yellow",
    "status__config_faulty": "yellow",      # a faulty 'Config' row tints the panel yellow
    "status__config_failed": "red",         # a failed 'Config' row -> red
    "status__exec_notification_error": "yellow",
    "status__schedule_drift": "green",      # drift is a footnote on a ✅ row, not a warning
    "status__timer_inactive": "red",
    "status__not_installed": "red",
    "status__orphan": "red",
    # PING (custom mixed/green/red logic)
    "ping__all_delivered": "green",
    "ping__mixed_valid_invalid": "yellow",
    "ping__delivered_and_failed": "yellow",
    "ping__invalid_only": "red",
    "ping__not_configured_default": "red",
    # CONFIG
    "config__all_good": "green",
    "config__update_available": "yellow",
    "config__env_mixed": "yellow",
    "config__env_not_configured": "red",
    "config__worst_case": "red",
    # SHELL (border derives from the exit code: 0 -> green, else red)
    "sh-install__systemctl_missing": "red",
    "sh-install__reinstall_all_configured": "green",
    "sh-update__dirty_declined": "red",
    "sh-update__new_scrapers_available": "green",
    "sh-schedule__registry_unreadable_venv_missing": "red",
    "sh-schedule__invalid_interval": "green",  # a warning notice, but the script exits 0
    "sh-enable__enable_fails": "red",
    "sh-disable__disable_success": "green",
    "sh-stop__not_running": "green",
    "sh-run__ping_not_alone": "red",
    "sh-uninstall__full_teardown": "green",
}


class TestBorderColors(unittest.TestCase):
    def test_every_scenario_has_a_valid_color(self):
        for sc in ALL_SCENARIOS:
            with self.subTest(scenario=sc.snapshot_key):
                self.assertIn(sc.build().border_color, VALID_COLORS)

    def test_representative_colors(self):
        by_key = {s.snapshot_key: s for s in ALL_SCENARIOS}
        for key, expected in EXPECTED_COLORS.items():
            with self.subTest(scenario=key):
                self.assertIn(key, by_key, f"Unknown scenario '{key}' in EXPECTED_COLORS.")
                self.assertEqual(by_key[key].build().border_color, expected)


if __name__ == "__main__":
    unittest.main()
