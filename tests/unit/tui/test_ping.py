"""Unit tests for ping's URL obfuscation and the panel's decision logic.

The PING snapshots pin the rendered panel for pre-obfuscated inputs; this pins
the actual ``obfuscate_invalid_url`` branches (token lengths, missing scheme,
the ``***`` fallback) and ``build_ping_panel``'s border color + the alignment of
delivery results with the valid URLs they belong to.
"""

import unittest

from core.tui.ping import build_ping_panel, obfuscate_invalid_url


class TestObfuscateInvalidUrl(unittest.TestCase):
    def test_long_token_keeps_first_and_last_char(self):
        self.assertEqual(obfuscate_invalid_url("tgram://secrettoken/chat123"), "tgram://s...n/...")

    def test_two_char_token_keeps_only_first_char(self):
        self.assertEqual(obfuscate_invalid_url("tgram://ab/chat"), "tgram://a.../...")

    def test_one_char_token_keeps_only_first_char(self):
        self.assertEqual(obfuscate_invalid_url("x"), "x...")

    def test_no_scheme_no_path(self):
        self.assertEqual(obfuscate_invalid_url("abcdef"), "a...f")

    def test_no_scheme_with_path(self):
        self.assertEqual(obfuscate_invalid_url("abcdef/rest/of/it"), "a...f/...")

    def test_empty_input_falls_back_to_stars(self):
        # Nothing recognizable to show: never echo the raw value, even blank.
        self.assertEqual(obfuscate_invalid_url(""), "***")


class TestBuildPingPanel(unittest.TestCase):
    def test_all_delivered_is_green(self):
        panel, color = build_ping_panel(
            url_entries=[("json://a", True), ("json://b", True)],
            test_results=[("json://a/...", True), ("json://b/...", True)],
            config_error_msg="",
        )
        self.assertEqual(color, "green")
        self.assertEqual(panel.icons, ["✅", "✅"])

    def test_mixed_success_and_error_is_yellow(self):
        panel, color = build_ping_panel(
            url_entries=[("bad", False), ("json://a", True)],
            test_results=[("json://a/...", True)],
            config_error_msg="",
        )
        self.assertEqual(color, "yellow")

    def test_all_failed_is_red(self):
        panel, color = build_ping_panel(
            url_entries=[("json://a", True)],
            test_results=[("json://a/...", False)],
            config_error_msg="",
        )
        self.assertEqual(color, "red")
        self.assertEqual(panel.icons, ["🛑"])

    def test_nothing_configured_is_red_with_config_message(self):
        panel, color = build_ping_panel([], [], config_error_msg="General config is unreadable")
        self.assertEqual(color, "red")
        self.assertEqual(panel.icons, ["🛑"])

    def test_results_align_with_valid_urls_in_configuration_order(self):
        # An invalid URL between two valid ones must not shift which delivery
        # result lands on which row: test_results holds only the *valid* URLs'
        # outcomes, in the same relative order.
        panel, _ = build_ping_panel(
            url_entries=[("json://first", True), ("broken", False), ("json://second", True)],
            test_results=[("json://first/...", True), ("json://second/...", False)],
            config_error_msg="",
        )
        self.assertEqual(panel.icons, ["✅", "❗", "🛑"])
