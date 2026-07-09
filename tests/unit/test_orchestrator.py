"""Unit tests for the orchestrator's decision logic.

These exercise the two methods that hold the real product behavior — the price
outcome + notify gate (``_handle_successful_scrape``) and the retry/error-policy
loop (``_run_attempts``) — with every collaborator mocked. The UI snapshot suite
proves *how* a call sequence renders; this proves *which* calls the orchestrator
decides to emit, and why. No network, no real notifications, no sleeping.

All doubles come from the autospec'd factories in ``tests/support.py`` (so a
signature change in a collaborator fails here), and every UI payload is asserted
exactly against the ``core.messages`` catalog (so a note added, dropped, or
reordered fails here too — no substring scanning).
"""

import contextlib
import unittest
from unittest import mock

from core import messages, orchestrator
from core.orchestrator import ScrapingOrchestrator
from core.scrapers.base.model import BaseTrackedItem, ScrapeResult
from core.scrapers.base.plugin import BasePlugin
from core.scrapers.base.settings import ResolvedSettings
from core.ui.tui import PriceOutcome
from core.exceptions import (
    ProductNotFoundError, ServerError, ScraperParseError, RateLimitError,
    StorageFileError,
)
from core.constants import MAX_RETRIES

from support import mock_notifier, mock_ui, mock_registry, mock_scraper, mock_data_manager


def _make_orch(notifier=None, ui=None, registry=None):
    """A ScrapingOrchestrator with autospec'd mock collaborators."""
    return ScrapingOrchestrator(
        targets_to_run=["skroutz"],
        registry=registry or mock_registry(),
        notifier=notifier or mock_notifier(),
        config_dir="/tmp/cfg",
        ui_strategy=ui or mock_ui(),
    )


def _item(target_price=10.0, **kw):
    return BaseTrackedItem(name="Widget", url="https://x/s/1/p.html",
                           target_price=target_price, **kw)


# --- Group A: price outcome + notify-on-drop gate ------------------------------

class TestPriceOutcome(unittest.TestCase):
    """`_handle_successful_scrape` picks the outcome and gates the low-price push."""

    def _call(self, item, price, notifier, ui):
        dm = mock_data_manager()
        orch = _make_orch(notifier=notifier, ui=ui)
        orch._handle_successful_scrape(item, ScrapeResult(price=price, currency="€"), dm)
        return dm

    def test_drop_notifies_when_services_configured(self):
        notifier = mock_notifier(has_services=True, delivery_ok=True)
        ui = mock_ui()
        dm = self._call(_item(target_price=10.0), 8.0, notifier, ui)

        notifier.notify_low_price.assert_called_once_with(
            "Widget", 10.0, 8.0, "https://x/s/1/p.html", "€")
        ui.log_price_result.assert_called_once_with(
            "Widget", 8.0, "€", 10.0, PriceOutcome.DROP,
            notes=[messages.NOTE_NOTIFIED_OK], attempt_notes=None)
        # The new price is always persisted.
        dm.update_item.assert_called_once_with(
            "https://x/s/1/p.html", last_price=8.0, last_checked=mock.ANY)

    def test_drop_reports_failed_delivery(self):
        notifier = mock_notifier(has_services=True, delivery_ok=False)
        ui = mock_ui()
        self._call(_item(target_price=10.0), 8.0, notifier, ui)
        ui.log_price_result.assert_called_once_with(
            "Widget", 8.0, "€", 10.0, PriceOutcome.DROP,
            notes=[messages.NOTE_NOTIFIED_FAIL], attempt_notes=None)

    def test_drop_without_services_does_not_notify(self):
        notifier = mock_notifier(has_services=False)
        ui = mock_ui()
        self._call(_item(target_price=10.0), 8.0, notifier, ui)
        notifier.notify_low_price.assert_not_called()
        ui.log_price_result.assert_called_once_with(
            "Widget", 8.0, "€", 10.0, PriceOutcome.DROP,
            notes=[messages.NOTE_NOTIFIED_NONE], attempt_notes=None)

    def test_no_target_price_is_no_target_outcome(self):
        notifier = mock_notifier(has_services=True)
        ui = mock_ui()
        self._call(_item(target_price=0.0), 5.0, notifier, ui)
        notifier.notify_low_price.assert_not_called()
        # No target means no notification chatter: the notes list is empty.
        ui.log_price_result.assert_called_once_with(
            "Widget", 5.0, "€", 0.0, PriceOutcome.NO_TARGET, notes=[], attempt_notes=None)

    def test_at_or_above_target_is_ok_outcome(self):
        notifier = mock_notifier(has_services=True)
        ui = mock_ui()
        self._call(_item(target_price=10.0), 12.0, notifier, ui)
        notifier.notify_low_price.assert_not_called()
        ui.log_price_result.assert_called_once_with(
            "Widget", 12.0, "€", 10.0, PriceOutcome.OK, notes=[], attempt_notes=None)

    def test_invalid_target_price_is_footnoted(self):
        notifier = mock_notifier(has_services=True)
        ui = mock_ui()
        dm = mock_data_manager()
        orch = _make_orch(notifier=notifier, ui=ui)
        orch._handle_successful_scrape(
            _item(target_price=0.0), ScrapeResult(price=5.0, currency="€"), dm,
            original_invalid_price="abc")
        ui.log_price_result.assert_called_once_with(
            "Widget", 5.0, "€", 0.0, PriceOutcome.NO_TARGET,
            notes=[messages.invalid_target_price("abc", "€")], attempt_notes=None)

    def test_missing_target_price_is_footnoted(self):
        notifier = mock_notifier(has_services=True)
        ui = mock_ui()
        dm = mock_data_manager()
        orch = _make_orch(notifier=notifier, ui=ui)
        orch._handle_successful_scrape(
            _item(target_price=0.0), ScrapeResult(price=5.0, currency="€"), dm,
            missing_target_price=True)
        ui.log_price_result.assert_called_once_with(
            "Widget", 5.0, "€", 0.0, PriceOutcome.NO_TARGET,
            notes=[messages.missing_target_price("€")], attempt_notes=None)


# --- Group B: retry loop + error policy ----------------------------------------

class TestRunAttempts(unittest.TestCase):
    """`_run_attempts` maps each error through its policy across up to MAX_RETRIES."""

    def _run(self, scraper, item=None, notifier=None, logger=None):
        item = item or _item(target_price=5.0)
        registry = mock_registry()
        registry.get_scraper.return_value = scraper
        ui = mock_ui()
        orch = _make_orch(notifier=notifier or mock_notifier(), ui=ui, registry=registry)
        orch._current_target = "skroutz"
        orch._current_logger = logger
        dm = mock_data_manager()
        with mock.patch.object(orch, "_sleep_with_jitter") as sleep, \
             mock.patch.object(orchestrator, "save_traceback") as save_tb:
            err, abort = orch._run_attempts(item, dm, None, False)
        return ui, err, abort, sleep, save_tb

    @staticmethod
    def _attempts(error_type, count=MAX_RETRIES):
        """The per-attempt footnotes for `count` consecutive failures of one type."""
        return [messages.attempt_note(i, error_type) for i in range(1, count + 1)]

    def test_success_first_try_no_retry_no_sleep(self):
        scraper = mock_scraper()
        scraper.scrape_product.return_value = ScrapeResult(price=8.0, currency="€")
        ui, err, abort, sleep, _ = self._run(scraper, notifier=mock_notifier())

        self.assertEqual((err, abort), (None, False))
        sleep.assert_not_called()
        scraper.scrape_product.assert_called_once()
        scraper.refresh_identity.assert_not_called()
        ui.log_price_result.assert_called_once_with(
            "Widget", 8.0, "€", 5.0, PriceOutcome.OK, notes=[], attempt_notes=[])

    def test_skip_error_is_warning_not_failure(self):
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = ProductNotFoundError("gone")
        ui, err, abort, sleep, _ = self._run(scraper)

        self.assertEqual((err, abort), (None, False))
        # Terminal for the item: one attempt, no retry, no back-off.
        scraper.scrape_product.assert_called_once()
        sleep.assert_not_called()
        ui.log_warning.assert_called_once_with(
            "Widget", messages.skipping_warning("ProductNotFoundError"),
            notes=["gone"], attempt_notes=[])

    def test_parse_error_exhausts_retries_and_counts_as_failure(self):
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = ScraperParseError("bad html")
        logger = mock.Mock()
        ui, err, abort, sleep, save_tb = self._run(scraper, logger=logger)

        # ScraperParseError's policy: counts_as_failure -> the exception is returned.
        self.assertIsInstance(err, ScraperParseError)
        self.assertFalse(abort)
        self.assertEqual(scraper.scrape_product.call_count, MAX_RETRIES)
        # Refresh + sleep happen between attempts, not after the last one.
        self.assertEqual(sleep.call_count, MAX_RETRIES - 1)
        self.assertEqual(scraper.refresh_identity.call_count, MAX_RETRIES - 1)
        # No traceback is saved, so no errors.txt pointer is footnoted either.
        ui.log_failure.assert_called_once_with(
            "Widget", "ScraperParseError",
            attempt_notes=self._attempts("ScraperParseError"), extra_notes=None)
        save_tb.assert_not_called()

    def test_unknown_error_uses_default_policy_and_saves_traceback(self):
        # An exception not in the policy table nor SKIP_ERRORS falls to the default
        # policy: counted as a failure, and a traceback is saved when a logger exists.
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = RuntimeError("boom")
        logger = mock.Mock()
        ui, err, abort, sleep, save_tb = self._run(scraper, logger=logger)

        self.assertIsInstance(err, RuntimeError)
        self.assertFalse(abort)
        save_tb.assert_called_once()
        # The default policy is the one that points at the error log.
        ui.log_failure.assert_called_once_with(
            "Widget", "RuntimeError", attempt_notes=self._attempts("RuntimeError"),
            extra_notes=[messages.errors_log_pointer("skroutz")])

    def test_default_policy_skips_traceback_without_logger(self):
        # Same default policy, but no logger -> the save_traceback branch is guarded.
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = RuntimeError("boom")
        ui, err, abort, sleep, save_tb = self._run(scraper, logger=None)
        save_tb.assert_not_called()

    def test_server_error_retries_without_refresh_and_not_counted(self):
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = ServerError("503")
        ui, err, abort, sleep, save_tb = self._run(scraper)

        # ServerError policy: not counted as failure -> None; identity not rotated;
        # no errors.txt pointer (nothing was logged as a failure).
        self.assertEqual((err, abort), (None, False))
        self.assertEqual(sleep.call_count, MAX_RETRIES - 1)
        scraper.refresh_identity.assert_not_called()
        ui.log_failure.assert_called_once_with(
            "Widget", "ServerError",
            attempt_notes=self._attempts("ServerError"), extra_notes=None)

    def test_rate_limit_aborts_the_run(self):
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = RateLimitError("429")
        ui, err, abort, sleep, _ = self._run(scraper)

        self.assertIsInstance(err, RateLimitError)
        self.assertTrue(abort)  # RateLimit policy aborts the whole target.
        ui.log_failure.assert_called_once_with(
            "Widget", "RateLimitError", attempt_notes=self._attempts("RateLimitError"),
            extra_notes=[messages.NOTE_RATE_LIMIT_ABORTED, messages.errors_log_pointer("skroutz")])

    def test_retry_then_success_records_retries_used(self):
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = [
            ScraperParseError("transient"),
            ScrapeResult(price=8.0, currency="€"),
        ]
        ui, err, abort, sleep, _ = self._run(scraper, notifier=mock_notifier())

        self.assertEqual((err, abort), (None, False))
        self.assertEqual(scraper.scrape_product.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(scraper.refresh_identity.call_count, 1)
        # The success row surfaces that it recovered on attempt 2, with the
        # collapsed footnote of the failed first attempt.
        ui.log_price_result.assert_called_once_with(
            "Widget", 8.0, "€", 5.0, PriceOutcome.OK,
            notes=[messages.succeeded_on_attempt(2, MAX_RETRIES)],
            attempt_notes=[messages.attempt_note(1, "ScraperParseError")])


# --- Group C: run()'s save-failure reporting -----------------------------------

class TestSaveFailureMessage(unittest.TestCase):
    """A failed save names the plugin's *declared* config filename, not <target>.json."""

    def test_save_error_names_plugin_config_filename(self):
        plugin = mock.create_autospec(BasePlugin, instance=True)
        plugin.get_config_filename.return_value = "custom-name.json"

        settings = mock.create_autospec(ResolvedSettings, instance=True)
        settings.views.return_value = []
        settings.block_warning = None

        manager = mock_data_manager()
        manager.get_item_count.return_value = 1
        manager.get_faulty_indices.return_value = []
        manager.get_items.return_value = [{"skip": True}]
        # A skipped item still marks the run dirty, so save() is attempted and fails.
        manager.parse_item.return_value = BaseTrackedItem(name="Widget", skip=True)
        manager.save.side_effect = StorageFileError("disk full")

        registry = mock_registry()
        registry.settings_for.return_value = settings
        registry.get_manager.return_value = manager
        registry.get_plugin.return_value = plugin

        ui = mock_ui()
        orch = _make_orch(ui=ui, registry=registry)
        with mock.patch.object(orchestrator.signal, "signal"), \
             mock.patch.object(orchestrator, "acquire_lock",
                               return_value=contextlib.nullcontext()), \
             mock.patch.object(orchestrator, "get_target_logger"):
            orch.run()

        ui.log_error.assert_called_once_with(
            "Storage", messages.save_failed("custom-name.json"), "disk full")


if __name__ == "__main__":
    unittest.main()
