"""Unit tests for the orchestrator's decision logic.

These exercise the two methods that hold the real product behavior — the price
outcome + notify gate (``_handle_successful_scrape``) and the retry/error-policy
loop (``_run_attempts``) — with every collaborator mocked. The UI snapshot suite
proves *how* a call sequence renders; this proves *which* calls the orchestrator
decides to emit, and why. No network, no real notifications, no sleeping.
"""

import contextlib
import unittest
from unittest import mock
from unittest.mock import Mock

from core import orchestrator
from core.orchestrator import ScrapingOrchestrator
from core.scrapers.base.model import BaseTrackedItem, ScrapeResult
from core.ui.tui import PriceOutcome
from core.exceptions import (
    ProductNotFoundError, ServerError, ScraperParseError, RateLimitError,
    StorageFileError,
)
from core.constants import MAX_RETRIES


def _make_orch(notifier=None, ui=None, registry=None):
    """A ScrapingOrchestrator with mocked collaborators."""
    return ScrapingOrchestrator(
        targets_to_run=["skroutz"],
        registry=registry or Mock(),
        notifier=notifier or Mock(),
        config_dir="/tmp/cfg",
        ui_strategy=ui or Mock(),
    )


def _item(target_price=10.0, **kw):
    return BaseTrackedItem(name="Widget", url="https://x/s/1/p.html",
                           target_price=target_price, **kw)


# --- Group A: price outcome + notify-on-drop gate ------------------------------

class TestPriceOutcome(unittest.TestCase):
    """`_handle_successful_scrape` picks the outcome and gates the low-price push."""

    def _call(self, item, price, notifier, ui):
        dm = Mock()
        orch = _make_orch(notifier=notifier, ui=ui)
        orch._handle_successful_scrape(item, ScrapeResult(price=price, currency="€"), dm)
        return dm, ui.log_price_result.call_args

    def test_drop_notifies_when_services_configured(self):
        notifier = Mock()
        notifier.has_services = True
        notifier.notify_low_price.return_value = True
        ui = Mock()
        dm, called = self._call(_item(target_price=10.0), 8.0, notifier, ui)

        notifier.notify_low_price.assert_called_once_with(
            "Widget", 10.0, 8.0, "https://x/s/1/p.html", "€")
        self.assertEqual(called.args[4], PriceOutcome.DROP)
        self.assertTrue(any("delivered" in n for n in called.args[5]))
        # The new price is always persisted.
        self.assertEqual(dm.update_item.call_args.kwargs["last_price"], 8.0)

    def test_drop_reports_failed_delivery(self):
        notifier = Mock()
        notifier.has_services = True
        notifier.notify_low_price.return_value = False
        ui = Mock()
        _, called = self._call(_item(target_price=10.0), 8.0, notifier, ui)
        self.assertEqual(called.args[4], PriceOutcome.DROP)
        self.assertTrue(any("failed" in n for n in called.args[5]))

    def test_drop_without_services_does_not_notify(self):
        notifier = Mock()
        notifier.has_services = False
        ui = Mock()
        _, called = self._call(_item(target_price=10.0), 8.0, notifier, ui)
        notifier.notify_low_price.assert_not_called()
        self.assertEqual(called.args[4], PriceOutcome.DROP)
        self.assertTrue(any("not configured" in n for n in called.args[5]))

    def test_no_target_price_is_no_target_outcome(self):
        notifier = Mock()
        notifier.has_services = True
        ui = Mock()
        _, called = self._call(_item(target_price=0.0), 5.0, notifier, ui)
        self.assertEqual(called.args[4], PriceOutcome.NO_TARGET)
        notifier.notify_low_price.assert_not_called()

    def test_at_or_above_target_is_ok_outcome(self):
        notifier = Mock()
        notifier.has_services = True
        ui = Mock()
        _, called = self._call(_item(target_price=10.0), 12.0, notifier, ui)
        self.assertEqual(called.args[4], PriceOutcome.OK)
        notifier.notify_low_price.assert_not_called()


# --- Group B: retry loop + error policy ----------------------------------------

class TestRunAttempts(unittest.TestCase):
    """`_run_attempts` maps each error through its policy across up to MAX_RETRIES."""

    def _run(self, scraper, item=None, notifier=None, logger=None):
        item = item or _item(target_price=5.0)
        registry = Mock()
        registry.get_scraper.return_value = scraper
        ui = Mock()
        orch = _make_orch(notifier=notifier or Mock(), ui=ui, registry=registry)
        orch._current_logger = logger
        dm = Mock()
        with mock.patch.object(orch, "_sleep_with_jitter") as sleep, \
             mock.patch.object(orchestrator, "save_traceback") as save_tb:
            err, abort = orch._run_attempts(item, dm, None, False)
        return ui, err, abort, sleep, save_tb

    def test_success_first_try_no_retry_no_sleep(self):
        scraper = Mock()
        scraper.scrape_product.return_value = ScrapeResult(price=8.0, currency="€")
        notifier = Mock()
        notifier.has_services = False
        ui, err, abort, sleep, _ = self._run(scraper, notifier=notifier)

        self.assertEqual((err, abort), (None, False))
        sleep.assert_not_called()
        scraper.scrape_product.assert_called_once()
        scraper.refresh_identity.assert_not_called()

    def test_skip_error_is_warning_not_failure(self):
        scraper = Mock()
        scraper.scrape_product.side_effect = ProductNotFoundError("gone")
        ui, err, abort, sleep, _ = self._run(scraper)

        self.assertEqual((err, abort), (None, False))
        # Terminal for the item: one attempt, no retry, no back-off.
        scraper.scrape_product.assert_called_once()
        sleep.assert_not_called()
        ui.log_warning.assert_called_once()

    def test_parse_error_exhausts_retries_and_counts_as_failure(self):
        scraper = Mock()
        scraper.scrape_product.side_effect = ScraperParseError("bad html")
        logger = Mock()
        ui, err, abort, sleep, save_tb = self._run(scraper, logger=logger)

        # Default policy: counts_as_failure -> the exception is returned.
        self.assertIsInstance(err, ScraperParseError)
        self.assertFalse(abort)
        self.assertEqual(scraper.scrape_product.call_count, MAX_RETRIES)
        # Refresh + sleep happen between attempts, not after the last one.
        self.assertEqual(sleep.call_count, MAX_RETRIES - 1)
        self.assertEqual(scraper.refresh_identity.call_count, MAX_RETRIES - 1)
        ui.log_failure.assert_called_once()
        # ScraperParseError's policy does not save a traceback.
        save_tb.assert_not_called()

    def test_unknown_error_uses_default_policy_and_saves_traceback(self):
        # An exception not in the policy table nor SKIP_ERRORS falls to the default
        # policy: counted as a failure, and a traceback is saved when a logger exists.
        scraper = Mock()
        scraper.scrape_product.side_effect = RuntimeError("boom")
        logger = Mock()
        ui, err, abort, sleep, save_tb = self._run(scraper, logger=logger)

        self.assertIsInstance(err, RuntimeError)
        self.assertFalse(abort)
        save_tb.assert_called_once()

    def test_default_policy_skips_traceback_without_logger(self):
        # Same default policy, but no logger -> the save_traceback branch is guarded.
        scraper = Mock()
        scraper.scrape_product.side_effect = RuntimeError("boom")
        ui, err, abort, sleep, save_tb = self._run(scraper, logger=None)
        save_tb.assert_not_called()

    def test_server_error_retries_without_refresh_and_not_counted(self):
        scraper = Mock()
        scraper.scrape_product.side_effect = ServerError("503")
        ui, err, abort, sleep, save_tb = self._run(scraper)

        # ServerError policy: not counted as failure -> None; identity not rotated.
        self.assertEqual((err, abort), (None, False))
        self.assertEqual(sleep.call_count, MAX_RETRIES - 1)
        scraper.refresh_identity.assert_not_called()
        ui.log_failure.assert_called_once()

    def test_rate_limit_aborts_the_run(self):
        scraper = Mock()
        scraper.scrape_product.side_effect = RateLimitError("429")
        ui, err, abort, sleep, _ = self._run(scraper)

        self.assertIsInstance(err, RateLimitError)
        self.assertTrue(abort)  # RateLimit policy aborts the whole target.

    def test_retry_then_success_records_retries_used(self):
        scraper = Mock()
        scraper.scrape_product.side_effect = [
            ScraperParseError("transient"),
            ScrapeResult(price=8.0, currency="€"),
        ]
        notifier = Mock()
        notifier.has_services = False
        ui, err, abort, sleep, _ = self._run(scraper, notifier=notifier)

        self.assertEqual((err, abort), (None, False))
        self.assertEqual(scraper.scrape_product.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(scraper.refresh_identity.call_count, 1)
        # The success row surfaces that it recovered on attempt 2.
        notes = ui.log_price_result.call_args.args[5]
        self.assertTrue(any("attempt 2" in n for n in notes))


# --- Group C: run()'s save-failure reporting -----------------------------------

class TestSaveFailureMessage(unittest.TestCase):
    """A failed save names the plugin's *declared* config filename, not <target>.json."""

    def test_save_error_names_plugin_config_filename(self):
        plugin = Mock()
        plugin.get_config_filename.return_value = "custom-name.json"

        settings = Mock()
        settings.views.return_value = []
        settings.block_warning = None

        manager = Mock()
        manager.get_item_count.return_value = 1
        manager.get_faulty_indices.return_value = []
        manager.get_items.return_value = [{"skip": True}]
        # A skipped item still marks the run dirty, so save() is attempted and fails.
        manager.parse_item.return_value = BaseTrackedItem(name="Widget", skip=True)
        manager.save.side_effect = StorageFileError("disk full")

        registry = Mock()
        registry.settings_for.return_value = settings
        registry.get_manager.return_value = manager
        registry.get_plugin.return_value = plugin

        ui = Mock()
        orch = _make_orch(ui=ui, registry=registry)
        with mock.patch.object(orchestrator.signal, "signal"), \
             mock.patch.object(orchestrator, "acquire_lock",
                               return_value=contextlib.nullcontext()), \
             mock.patch.object(orchestrator, "get_target_logger"):
            orch.run()

        ui.log_error.assert_called_once_with(
            "Storage", "Failed to update config/custom-name.json file!", "disk full")


if __name__ == "__main__":
    unittest.main()
