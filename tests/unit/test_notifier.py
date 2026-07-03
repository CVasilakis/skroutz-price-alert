"""Unit tests for the notifier's dispatch and formatting logic.

apprise is mocked, so nothing is ever sent and no network is touched. These lock
down the decisions the review flagged as untested: the ``has_services`` gate,
delegation to apprise, site-name resolution, and summary truncation.
"""

import unittest
from unittest import mock

from core.notifier import Notifier
from core.scrapers.base.model import BaseTrackedItem


def _make_notifier(urls, add_return=True, valid=True):
    """Builds a Notifier whose apprise instance is a mock. Returns (notifier, app)."""
    with mock.patch("core.notifier.apprise.Apprise") as app_cls, \
         mock.patch("core.notifier.is_valid_apprise_url", return_value=valid):
        app = app_cls.return_value
        app.add.return_value = add_return
        notifier = Notifier(urls)
    return notifier, app


class TestHasServicesGate(unittest.TestCase):
    """`has_services` is True only when a URL is both valid AND accepted by apprise."""

    def test_valid_and_added_enables_services(self):
        notifier, app = _make_notifier("tgram://token/chat", valid=True, add_return=True)
        self.assertTrue(notifier.has_services)
        app.add.assert_called_once_with("tgram://token/chat")

    def test_invalid_url_short_circuits_before_add(self):
        notifier, app = _make_notifier("not-a-url", valid=False)
        self.assertFalse(notifier.has_services)
        app.add.assert_not_called()  # `is_valid and add` short-circuits.

    def test_apprise_rejection_leaves_services_off(self):
        notifier, app = _make_notifier("tgram://token/chat", valid=True, add_return=False)
        self.assertFalse(notifier.has_services)
        app.add.assert_called_once()

    def test_empty_string_configures_nothing(self):
        notifier, app = _make_notifier("", valid=True)
        self.assertFalse(notifier.has_services)
        app.add.assert_not_called()


class TestNotifyDelegation(unittest.TestCase):
    def test_notify_returns_apprise_result_as_bool(self):
        notifier, app = _make_notifier("tgram://token/chat")
        app.notify.return_value = 1  # apprise returns truthy non-bool
        result = notifier.notify("T", "B")
        self.assertIs(result, True)
        app.notify.assert_called_once_with(title="T", body="B")

    def test_notify_low_price_sends_price_drop(self):
        notifier, app = _make_notifier("tgram://token/chat")
        app.notify.return_value = True
        with mock.patch.object(notifier, "_extract_site", return_value="Skroutz"):
            ok = notifier.notify_low_price("Widget", 12.0, 9.0, "https://x/1", "€")
        self.assertTrue(ok)
        kwargs = app.notify.call_args.kwargs
        self.assertEqual(kwargs["title"], "Scrooge Alert - Price Drop!")
        self.assertIn("Skroutz", kwargs["body"])
        self.assertIn("9.0", kwargs["body"])


class TestExtractSite(unittest.TestCase):
    def test_prefers_plugin_display_name(self):
        notifier, _ = _make_notifier("tgram://token/chat")
        plugin = mock.Mock()
        plugin.get_display_name.return_value = "Skroutz"
        with mock.patch("core.notifier.ScraperRegistry.plugin_for_url", return_value=plugin):
            self.assertEqual(notifier._extract_site("https://www.skroutz.gr/s/1/p"), "Skroutz")

    def test_domain_fallback_strips_www_and_capitalizes(self):
        notifier, _ = _make_notifier("tgram://token/chat")
        with mock.patch("core.notifier.ScraperRegistry.plugin_for_url", return_value=None):
            self.assertEqual(notifier._extract_site("https://www.example.gr/x"), "Example")

    def test_empty_url_is_unknown_site(self):
        notifier, _ = _make_notifier("tgram://token/chat")
        self.assertEqual(notifier._extract_site(""), "Unknown Site")

    def test_unparseable_url_is_unknown_site(self):
        notifier, _ = _make_notifier("tgram://token/chat")
        with mock.patch("core.notifier.ScraperRegistry.plugin_for_url", return_value=None):
            self.assertEqual(notifier._extract_site("garbage-no-netloc"), "Unknown Site")


class TestSummaryTruncation(unittest.TestCase):
    """`_build_summary` (via notify_errors) caps the bullet list at max_show."""

    def _errors(self, n):
        return [(BaseTrackedItem(name=f"P{i}", url=f"https://x/{i}"), ValueError("boom"))
                for i in range(n)]

    def _send_errors(self, n):
        notifier, app = _make_notifier("tgram://token/chat")
        app.notify.return_value = True
        with mock.patch.object(notifier, "_extract_site", return_value="Site"):
            notifier.notify_errors(self._errors(n))
        return app.notify.call_args.kwargs["body"]

    def test_truncates_beyond_max_show(self):
        body = self._send_errors(5)
        self.assertIn("... and 2 more errors.", body)
        self.assertIn("P0: ValueError", body)
        self.assertNotIn("P4:", body)  # 4th+ items are truncated away.

    def test_no_truncation_at_or_below_max_show(self):
        body = self._send_errors(3)
        self.assertNotIn("more errors", body)

    def test_empty_failed_items_sends_nothing(self):
        notifier, app = _make_notifier("tgram://token/chat")
        self.assertFalse(notifier.notify_errors([]))
        app.notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
