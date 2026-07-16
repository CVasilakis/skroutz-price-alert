"""Unit tests for the orchestrator's decision logic.

These exercise the methods that hold the real product behavior — the price
outcome + notify gate (``_handle_successful_scrape``), the retry/error-policy
loop (``_run_attempts``), the timestamp staleness/repair logic
(``_check_and_repair_timestamp``), and ``run()``'s exit codes, skip paths and
notification gates — with every collaborator mocked. The UI snapshot suite
proves *how* a call sequence renders; this proves *which* calls the orchestrator
decides to emit, and why. No network, no real notifications, no sleeping.

All doubles come from the autospec'd factories in ``tests/support.py`` (so a
signature change in a collaborator fails here), and every UI payload is asserted
exactly against the ``core.messages`` catalog (so a note added, dropped, or
reordered fails here too — no substring scanning).
"""

import contextlib
import datetime
import unittest
from unittest import mock

from core import messages, orchestrator
from core.orchestrator import ScrapingOrchestrator, RunOutcome
from core.scrapers.base.model import AdvertMatch, BaseTrackedItem, ScrapeResult
from core.scrapers.base.plugin import BasePlugin
from core.scrapers.base.settings import ResolvedSettings, KEY_RETENTION, KEY_NOTIFY
from core.ui.tui import PriceOutcome
from core.exceptions import (
    ProductNotFoundError, InvalidURLError, ServerError, ScraperError,
    ScraperParseError, RateLimitError,
    StorageFileError, LockAcquisitionError, PluginDependencyError,
)
from core.constants import (
    MAX_RETRIES, OLD_ENTRY_HOURS, TIMESTAMP_FORMAT,
    EXIT_CODE_SUCCESS, EXIT_CODE_INTERRUPT, EXIT_CODE_SKIPPED,
    EXIT_CODE_PRODUCTS_ERROR, EXIT_CODE_RATE_LIMIT_ERROR,
    EXIT_CODE_SCRAPE_ERROR, EXIT_CODE_STORAGE_ERROR,
    EXIT_CODE_NOTIFICATION_ERROR, EXIT_CODE_PLUGIN_DEPENDENCY_ERROR,
)
from core.preflight import TargetLoad
from core.utils import describe_signal

from support import mock_notifier, mock_ui, mock_registry, mock_scraper, mock_data_manager

# A fixed "now" for the clock seam: tests derive stored timestamps from it, so
# nothing here depends on the wall clock.
NOW = datetime.datetime(2026, 7, 9, 12, 0, 0)


def _make_orch(notifier=None, ui=None, registry=None, targets=None,
               loads_by_target=None, now_fn=None):
    """A ScrapingOrchestrator with autospec'd mock collaborators and a fixed clock."""
    return ScrapingOrchestrator(
        targets_to_run=targets or ["skroutz"],
        registry=registry or mock_registry(),
        notifier=notifier or mock_notifier(),
        config_dir="/tmp/cfg",
        ui_strategy=ui or mock_ui(),
        loads_by_target=loads_by_target,
        now_fn=now_fn or (lambda: NOW),
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
            notes=[messages.NOTE_NOTIFIED_FAIL], attempt_notes=None,
            delivery_failed=True)

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


# --- Group A2: listing-type results (multi-advert matches / no match) ----------

class TestListingOutcomes(unittest.TestCase):
    """`_handle_successful_scrape` for listing-type results (``matches`` / ``price=None``)."""

    def _call(self, item, result, notifier, ui):
        dm = mock_data_manager()
        orch = _make_orch(notifier=notifier, ui=ui)
        orch._handle_successful_scrape(item, result, dm)
        return dm

    def test_no_match_refreshes_timestamp_only_and_never_alerts(self):
        notifier = mock_notifier(has_services=True)
        ui = mock_ui()
        dm = self._call(_item(target_price=10.0), ScrapeResult(price=None, currency="€"), notifier, ui)
        notifier.notify_low_price.assert_not_called()
        ui.log_price_result.assert_called_once_with(
            "Widget", None, "€", 10.0, PriceOutcome.NO_MATCH, notes=[], attempt_notes=None)
        # last_checked refreshes (the row never goes stale) but last_price is untouched.
        dm.update_item.assert_called_once_with(
            "https://x/s/1/p.html", last_checked=NOW.strftime(TIMESTAMP_FORMAT))

    def test_each_match_below_target_gets_its_own_push(self):
        notifier = mock_notifier(has_services=True, delivery_ok=True)
        ui = mock_ui()
        result = ScrapeResult(price=6.0, currency="€", matches=[
            AdvertMatch("Cheap ad", 6.0, "https://x/classifieds/ad-1/"),
            AdvertMatch("Mid ad", 8.0, "https://x/classifieds/ad-2/"),
            AdvertMatch("Pricey ad", 12.0, "https://x/classifieds/ad-3/"),
        ])
        dm = self._call(_item(target_price=10.0), result, notifier, ui)

        # One push per advert below target, each linking to that advert.
        notifier.notify_low_price.assert_has_calls([
            mock.call("Widget", 10.0, 6.0, "https://x/classifieds/ad-1/", "€", advert_title="Cheap ad"),
            mock.call("Widget", 10.0, 8.0, "https://x/classifieds/ad-2/", "€", advert_title="Mid ad"),
        ])
        self.assertEqual(notifier.notify_low_price.call_count, 2)
        ui.log_price_result.assert_called_once_with(
            "Widget", 6.0, "€", 10.0, PriceOutcome.DROP,
            notes=[messages.advert_matches_note(3, 2), messages.advert_notified_ok(2)],
            attempt_notes=None)
        # The cheapest match's price is persisted as the row's last_price.
        dm.update_item.assert_called_once_with(
            "https://x/s/1/p.html", last_price=6.0, last_checked=mock.ANY)

    def test_partial_delivery_failure_is_footnoted(self):
        notifier = mock_notifier(has_services=True)
        notifier.notify_low_price.side_effect = [True, False]
        ui = mock_ui()
        result = ScrapeResult(price=6.0, currency="€", matches=[
            AdvertMatch("Cheap ad", 6.0, "https://x/classifieds/ad-1/"),
            AdvertMatch("Mid ad", 8.0, "https://x/classifieds/ad-2/"),
        ])
        self._call(_item(target_price=10.0), result, notifier, ui)
        ui.log_price_result.assert_called_once_with(
            "Widget", 6.0, "€", 10.0, PriceOutcome.DROP,
            notes=[messages.advert_matches_note(2, 2), messages.advert_notified_fail(1, 2)],
            attempt_notes=None, delivery_failed=True)

    def test_matches_at_or_above_target_is_ok_without_pushes(self):
        notifier = mock_notifier(has_services=True)
        ui = mock_ui()
        result = ScrapeResult(price=12.0, currency="€", matches=[
            AdvertMatch("Pricey ad", 12.0, "https://x/classifieds/ad-3/"),
        ])
        dm = self._call(_item(target_price=10.0), result, notifier, ui)
        notifier.notify_low_price.assert_not_called()
        ui.log_price_result.assert_called_once_with(
            "Widget", 12.0, "€", 10.0, PriceOutcome.OK,
            notes=[messages.advert_matches_note(1, 0)], attempt_notes=None)
        dm.update_item.assert_called_once_with(
            "https://x/s/1/p.html", last_price=12.0, last_checked=mock.ANY)

    def test_matches_without_target_is_no_target_outcome(self):
        notifier = mock_notifier(has_services=True)
        ui = mock_ui()
        result = ScrapeResult(price=5.0, currency="€", matches=[
            AdvertMatch("Some ad", 5.0, "https://x/classifieds/ad-1/"),
        ])
        self._call(_item(target_price=0.0), result, notifier, ui)
        notifier.notify_low_price.assert_not_called()
        ui.log_price_result.assert_called_once_with(
            "Widget", 5.0, "€", 0.0, PriceOutcome.NO_TARGET,
            notes=[messages.advert_matches_note(1, 0)], attempt_notes=None)

    def test_matches_below_target_without_services_notes_none(self):
        notifier = mock_notifier(has_services=False)
        ui = mock_ui()
        result = ScrapeResult(price=6.0, currency="€", matches=[
            AdvertMatch("Cheap ad", 6.0, "https://x/classifieds/ad-1/"),
        ])
        self._call(_item(target_price=10.0), result, notifier, ui)
        notifier.notify_low_price.assert_not_called()
        ui.log_price_result.assert_called_once_with(
            "Widget", 6.0, "€", 10.0, PriceOutcome.DROP,
            notes=[messages.advert_matches_note(1, 1), messages.NOTE_NOTIFIED_NONE],
            attempt_notes=None)


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
            outcome = orch._run_attempts(item, dm, None, False)
        return ui, outcome, sleep, save_tb

    @staticmethod
    def _attempts(error_type, count=MAX_RETRIES):
        """The per-attempt footnotes for `count` consecutive failures of one type."""
        return [messages.attempt_note(i, error_type) for i in range(1, count + 1)]

    def test_success_first_try_no_retry_no_sleep(self):
        scraper = mock_scraper()
        scraper.scrape_product.return_value = ScrapeResult(price=8.0, currency="€")
        ui, outcome, sleep, _ = self._run(scraper, notifier=mock_notifier())

        self.assertIsNone(outcome.reported_error)
        self.assertFalse(outcome.abort_target)
        sleep.assert_not_called()
        scraper.scrape_product.assert_called_once()
        scraper.refresh_identity.assert_not_called()
        ui.log_price_result.assert_called_once_with(
            "Widget", 8.0, "€", 5.0, PriceOutcome.OK, notes=[], attempt_notes=[])

    def test_skip_error_is_red_but_not_reported(self):
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = ProductNotFoundError("gone")
        ui, outcome, sleep, _ = self._run(scraper)

        self.assertIsNone(outcome.reported_error)
        self.assertFalse(outcome.abort_target)
        # Terminal for the item: one attempt, no retry, no back-off.
        scraper.scrape_product.assert_called_once()
        sleep.assert_not_called()
        ui.log_error.assert_called_once_with(
            "Widget", messages.skipping_warning("ProductNotFoundError"),
            notes=["gone"], attempt_notes=[])

    def test_parse_error_exhausts_retries_and_counts_as_failure(self):
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = ScraperParseError("bad html")
        logger = mock.Mock()
        ui, outcome, sleep, save_tb = self._run(scraper, logger=logger)

        # ScraperParseError's policy: counts_as_failure -> the exception is returned.
        self.assertIsInstance(outcome.reported_error, ScraperParseError)
        self.assertTrue(outcome.affects_scrape_status)
        self.assertFalse(outcome.abort_target)
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
        ui, outcome, sleep, save_tb = self._run(scraper, logger=logger)

        self.assertIsInstance(outcome.reported_error, RuntimeError)
        self.assertTrue(outcome.affects_scrape_status)
        self.assertFalse(outcome.abort_target)
        save_tb.assert_called_once()
        # The default policy is the one that points at the error log.
        ui.log_failure.assert_called_once_with(
            "Widget", "RuntimeError", attempt_notes=self._attempts("RuntimeError"),
            extra_notes=[messages.errors_log_pointer("skroutz")])

    def test_default_policy_skips_traceback_without_logger(self):
        # Same default policy, but no logger -> the save_traceback branch is guarded.
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = RuntimeError("boom")
        ui, outcome, sleep, save_tb = self._run(scraper, logger=None)
        save_tb.assert_not_called()

    def test_server_error_retries_without_refresh_and_not_counted(self):
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = ServerError("503")
        ui, outcome, sleep, save_tb = self._run(scraper)

        # ServerError policy: not counted as failure -> None; identity not rotated;
        # no errors.txt pointer (nothing was logged as a failure).
        self.assertIsNone(outcome.reported_error)
        self.assertFalse(outcome.affects_scrape_status)
        self.assertEqual(sleep.call_count, MAX_RETRIES - 1)
        scraper.refresh_identity.assert_not_called()
        ui.log_failure.assert_called_once_with(
            "Widget", "ServerError",
            attempt_notes=self._attempts("ServerError"), extra_notes=None)

    def test_generic_scraper_error_is_reported_but_does_not_affect_status(self):
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = ScraperError("empty response")

        _, outcome, _, _ = self._run(scraper)

        self.assertIsInstance(outcome.reported_error, ScraperError)
        self.assertFalse(outcome.affects_scrape_status)

    def test_invalid_url_error_is_red_and_reported_without_affecting_status(self):
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = InvalidURLError("bad product id")

        ui, outcome, sleep, _ = self._run(scraper)

        self.assertIsInstance(outcome.reported_error, InvalidURLError)
        self.assertFalse(outcome.affects_scrape_status)
        sleep.assert_not_called()
        ui.log_error.assert_called_once_with(
            "Widget", messages.skipping_warning("InvalidURLError"),
            notes=["bad product id"], attempt_notes=[],
        )

    def test_rate_limit_aborts_the_run(self):
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = RateLimitError("429")
        ui, outcome, sleep, _ = self._run(scraper)

        self.assertIsInstance(outcome.reported_error, RateLimitError)
        self.assertTrue(outcome.abort_target)  # RateLimit policy aborts the whole target.
        self.assertTrue(outcome.rate_limited)
        ui.log_failure.assert_called_once_with(
            "Widget", "RateLimitError", attempt_notes=self._attempts("RateLimitError"),
            extra_notes=[messages.NOTE_RATE_LIMIT_ABORTED, messages.errors_log_pointer("skroutz")])

    def test_retry_then_success_records_retries_used(self):
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = [
            ScraperParseError("transient"),
            ScrapeResult(price=8.0, currency="€"),
        ]
        ui, outcome, sleep, _ = self._run(scraper, notifier=mock_notifier())

        self.assertIsNone(outcome.reported_error)
        self.assertEqual(scraper.scrape_product.call_count, 2)
        self.assertEqual(sleep.call_count, 1)
        self.assertEqual(scraper.refresh_identity.call_count, 1)
        # The success row surfaces that it recovered on attempt 2, with the
        # collapsed footnote of the failed first attempt.
        ui.log_price_result.assert_called_once_with(
            "Widget", 8.0, "€", 5.0, PriceOutcome.OK,
            notes=[messages.succeeded_on_attempt(2, MAX_RETRIES)],
            attempt_notes=[messages.attempt_note(1, "ScraperParseError")])

    def test_notification_exception_does_not_retry_the_successful_scrape(self):
        scraper = mock_scraper()
        scraper.scrape_product.return_value = ScrapeResult(price=1.0, currency="€")
        notifier = mock_notifier(has_services=True)
        notifier.notify_low_price.side_effect = RuntimeError("transport crashed")

        ui, outcome, sleep, _ = self._run(scraper, notifier=notifier)

        self.assertTrue(outcome.notification_failed)
        scraper.scrape_product.assert_called_once()
        sleep.assert_not_called()
        ui.log_price_result.assert_called_once_with(
            "Widget", 1.0, "€", 5.0, PriceOutcome.DROP,
            notes=[messages.NOTE_NOTIFIED_FAIL], attempt_notes=[],
            delivery_failed=True,
        )


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
            exit_code = orch.run()

        self.assertEqual(exit_code, EXIT_CODE_STORAGE_ERROR)
        ui.log_error.assert_called_once_with(
            "Storage", messages.save_failed("custom-name.json"), "disk full")


# --- Group D: timestamp staleness / repair --------------------------------------

class TestTimestampRepair(unittest.TestCase):
    """`_check_and_repair_timestamp` repairs corrupt timestamps and flags stale ones.

    Uses the injected clock seam (``now_fn``), so the staleness window is asserted
    against a fixed NOW instead of the wall clock.
    """

    def _check(self, last_checked):
        orch = _make_orch()
        dm = mock_data_manager()
        item = _item(last_checked=last_checked, last_price=9.5)
        note = orch._check_and_repair_timestamp(item, dm)
        return note, dm, orch, item

    def test_corrupt_timestamp_is_repaired_in_place(self):
        note, dm, orch, item = self._check("not-a-date")
        self.assertEqual(note, messages.NOTE_CORRUPTED_TIMESTAMP)
        # The repair write keeps the stored price and stamps the current time.
        dm.update_item.assert_called_once_with(
            item.url, last_price=9.5, last_checked=NOW.strftime(TIMESTAMP_FORMAT))
        self.assertEqual(orch._stale_items, [])

    def test_non_string_timestamp_is_repaired_in_place(self):
        note, dm, orch, item = self._check(123)
        self.assertEqual(note, messages.NOTE_CORRUPTED_TIMESTAMP)
        dm.update_item.assert_called_once_with(
            item.url, last_price=9.5, last_checked=NOW.strftime(TIMESTAMP_FORMAT))
        self.assertEqual(orch._stale_items, [])

    def test_stale_timestamp_is_flagged_and_recorded(self):
        stale = (NOW - datetime.timedelta(hours=OLD_ENTRY_HOURS + 1)).strftime(TIMESTAMP_FORMAT)
        note, dm, orch, item = self._check(stale)
        self.assertEqual(note, messages.stale_note(stale, OLD_ENTRY_HOURS))
        # Stale items are accumulated for the aggregated end-of-target notification.
        self.assertEqual(orch._stale_items, [item])
        dm.update_item.assert_not_called()

    def test_exactly_at_threshold_is_not_stale(self):
        # The window uses a strict '>', so an entry exactly OLD_ENTRY_HOURS old is fresh.
        boundary = (NOW - datetime.timedelta(hours=OLD_ENTRY_HOURS)).strftime(TIMESTAMP_FORMAT)
        note, dm, orch, _ = self._check(boundary)
        self.assertIsNone(note)
        self.assertEqual(orch._stale_items, [])
        dm.update_item.assert_not_called()

    def test_fresh_timestamp_yields_no_note(self):
        fresh = (NOW - datetime.timedelta(hours=1)).strftime(TIMESTAMP_FORMAT)
        note, dm, orch, _ = self._check(fresh)
        self.assertIsNone(note)
        dm.update_item.assert_not_called()

    def test_empty_timestamp_yields_no_note_and_no_write(self):
        note, dm, orch, _ = self._check("")
        self.assertIsNone(note)
        dm.update_item.assert_not_called()


# --- Group E: run()'s exit codes, skip paths, and notification gates -------------

def _wired_target(rows, item=None, notify=True, config_filename="skroutz.json"):
    """A (registry, manager, settings) trio wired for a full run() pass over ``rows``.

    The settings double answers the two built-in keys run() reads (retention for
    the logger, the notify gate); the manager serves ``rows`` and parses every row
    to ``item`` (a skip item by default, the cheapest way through the loop).
    """
    plugin = mock.create_autospec(BasePlugin, instance=True)
    plugin.get_config_filename.return_value = config_filename

    settings = mock.create_autospec(ResolvedSettings, instance=True)
    settings.views.return_value = []
    settings.block_warning = None
    settings.value.side_effect = lambda key: {KEY_RETENTION: 7, KEY_NOTIFY: notify}[key]

    manager = mock_data_manager()
    manager.get_items.return_value = rows
    manager.get_item_count.return_value = len(rows)
    manager.get_faulty_indices.return_value = []
    manager.is_scrapable_item.return_value = True
    manager.parse_item.return_value = item or BaseTrackedItem(name="Widget", skip=True)

    registry = mock_registry()
    registry.settings_for.return_value = settings
    registry.get_manager.return_value = manager
    registry.get_plugin.return_value = plugin
    return registry, manager, settings


@contextlib.contextmanager
def _run_patches(orch, lock_fn=None):
    """The patches every run()-level test needs: no signals, no logger, no sleeping.

    ``lock_fn`` replaces ``acquire_lock`` (default: an always-free lock), so a test
    can simulate contention without touching the filesystem.
    """
    with mock.patch.object(orchestrator.signal, "signal"), \
         mock.patch.object(orchestrator, "get_target_logger"), \
         mock.patch.object(orchestrator, "save_traceback"), \
         mock.patch.object(orch, "_sleep_with_jitter"), \
         mock.patch.object(orchestrator, "acquire_lock",
                           lock_fn or (lambda target: contextlib.nullcontext())):
        yield


class TestRunExitCodes(unittest.TestCase):
    """run()'s exit codes: interrupt, the precedence ladder, and the skip path."""

    def test_interrupt_mid_run_exits_130_without_writing(self):
        import signal as signal_module
        product_row = {"name": "Widget", "url": "https://x/s/1/p.html", "target_price": 5}
        registry, manager, _ = _wired_target([product_row], item=_item(target_price=5.0))
        scraper = mock_scraper()
        ui = mock_ui()
        orch = _make_orch(ui=ui, registry=registry)

        def interrupt_then_succeed(url):
            # The real handler runs mid-scrape (as a signal would), so the loop
            # must discard the completed result and stop before any write.
            orch.signal_handler(signal_module.SIGINT, None)
            return ScrapeResult(price=1.0, currency="€")

        scraper.scrape_product.side_effect = interrupt_then_succeed
        registry.get_scraper.return_value = scraper

        with _run_patches(orch):
            exit_code = orch.run()

        self.assertEqual(exit_code, EXIT_CODE_INTERRUPT)
        ui.log_interrupt.assert_called_once_with(
            f"Received signal {describe_signal(signal_module.SIGINT)}")
        # The interrupted product's own result is discarded (no update_item), but
        # save() still runs under the lock: progress from products completed
        # *before* the interrupt must be persisted, not thrown away.
        manager.update_item.assert_not_called()
        manager.save.assert_called_once()

    def test_products_error_outranks_rate_limit(self):
        # One target's config failed to load, another aborts on rate limits: the
        # persistent setup problem (15) must win over the transient block (17).
        product_row = {"name": "Widget", "url": "https://x/s/1/p.html", "target_price": 5}
        registry, _, _ = _wired_target([product_row], item=_item(target_price=5.0))
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = RateLimitError("429")
        registry.get_scraper.return_value = scraper
        notifier = mock_notifier(has_services=True)
        orch = _make_orch(
            notifier=notifier, registry=registry, targets=["broken", "limited"],
            loads_by_target={"broken": TargetLoad("broken", error="invalid JSON")},
        )

        with _run_patches(orch):
            exit_code = orch.run()

        self.assertEqual(exit_code, EXIT_CODE_PRODUCTS_ERROR)
        # The healthy target still ran (and its failures were still notified).
        scraper.scrape_product.assert_called()
        notifier.notify_errors.assert_called_once()

    def test_all_targets_locked_exits_42(self):
        registry, _, _ = _wired_target([{"skip": True}])
        ui = mock_ui()
        orch = _make_orch(ui=ui, registry=registry, targets=["one", "two"])

        def held(target):
            raise LockAcquisitionError

        with _run_patches(orch, lock_fn=held):
            exit_code = orch.run()

        self.assertEqual(exit_code, EXIT_CODE_SKIPPED)
        ui.log_error.assert_has_calls([
            mock.call("System", messages.ERR_LOCK_HELD),
            mock.call("System", messages.ERR_LOCK_HELD),
        ])

    def test_one_of_two_locked_is_still_success(self):
        registry, _, _ = _wired_target([{"skip": True}])
        orch = _make_orch(registry=registry, targets=["one", "two"])

        locks = iter([LockAcquisitionError(), None])

        def contended(target):
            outcome = next(locks)
            if outcome is not None:
                raise outcome
            return contextlib.nullcontext()

        with _run_patches(orch, lock_fn=contended):
            exit_code = orch.run()

        self.assertEqual(exit_code, EXIT_CODE_SUCCESS)

    def test_run_outcome_precedence(self):
        outcome = RunOutcome(notification_error=True)
        self.assertEqual(outcome.exit_code(interrupted=False, target_count=1),
                         EXIT_CODE_NOTIFICATION_ERROR)

        outcome.rate_limited = True
        self.assertEqual(outcome.exit_code(interrupted=False, target_count=1),
                         EXIT_CODE_RATE_LIMIT_ERROR)
        outcome.scrape_error = True
        self.assertEqual(outcome.exit_code(interrupted=False, target_count=1),
                         EXIT_CODE_SCRAPE_ERROR)
        outcome.dependency_error = True
        self.assertEqual(outcome.exit_code(interrupted=False, target_count=1),
                         EXIT_CODE_PLUGIN_DEPENDENCY_ERROR)
        outcome.storage_error = True
        self.assertEqual(outcome.exit_code(interrupted=False, target_count=1),
                         EXIT_CODE_STORAGE_ERROR)
        outcome.products_error = True
        self.assertEqual(outcome.exit_code(interrupted=False, target_count=1),
                         EXIT_CODE_PRODUCTS_ERROR)
        self.assertEqual(outcome.exit_code(interrupted=True, target_count=1),
                         EXIT_CODE_INTERRUPT)

    def test_zero_item_target_is_rendered_and_succeeds(self):
        registry, manager, _ = _wired_target([])
        ui = mock_ui()
        orch = _make_orch(registry=registry, ui=ui)

        with _run_patches(orch):
            exit_code = orch.run()

        self.assertEqual(exit_code, EXIT_CODE_SUCCESS)
        ui.start_target.assert_called_once()
        ui.complete_target.assert_called_once()
        manager.save.assert_not_called()

    def test_failed_price_push_sets_notification_exit_without_stopping_save(self):
        row = {"name": "Widget", "url": "https://x/s/1/p.html", "target_price": 5}
        registry, manager, _ = _wired_target([row], item=_item(target_price=5.0))
        scraper = mock_scraper()
        scraper.scrape_product.return_value = ScrapeResult(price=1.0, currency="€")
        registry.get_scraper.return_value = scraper
        notifier = mock_notifier(has_services=True, delivery_ok=False)
        orch = _make_orch(registry=registry, notifier=notifier)

        with _run_patches(orch):
            exit_code = orch.run()

        self.assertEqual(exit_code, EXIT_CODE_NOTIFICATION_ERROR)
        manager.save.assert_called_once()

    def test_one_failed_advert_push_sets_notification_exit_and_continues(self):
        row = {"name": "Widget", "url": "https://x/s/1/p.html", "target_price": 5}
        registry, manager, _ = _wired_target([row], item=_item(target_price=5.0))
        scraper = mock_scraper()
        scraper.scrape_product.return_value = ScrapeResult(
            price=1.0,
            currency="€",
            matches=[
                AdvertMatch("First", 1.0, "https://x/ad/1"),
                AdvertMatch("Second", 2.0, "https://x/ad/2"),
            ],
        )
        registry.get_scraper.return_value = scraper
        notifier = mock_notifier(has_services=True)
        notifier.notify_low_price.side_effect = [True, False]
        orch = _make_orch(registry=registry, notifier=notifier)

        with _run_patches(orch):
            exit_code = orch.run()

        self.assertEqual(exit_code, EXIT_CODE_NOTIFICATION_ERROR)
        self.assertEqual(notifier.notify_low_price.call_count, 2)
        manager.save.assert_called_once()


class TestRunNotificationGates(unittest.TestCase):
    """run()'s end-of-target notifications: the notify_scraping_errors gate and stale alerts."""

    def _run_failing_target(self, notify, last_checked=""):
        product_row = {"name": "Widget", "url": "https://x/s/1/p.html", "target_price": 5}
        item = _item(target_price=5.0, last_checked=last_checked)
        registry, manager, _ = _wired_target([product_row], item=item, notify=notify)
        scraper = mock_scraper()
        scraper.scrape_product.side_effect = ScraperParseError("bad html")
        registry.get_scraper.return_value = scraper
        notifier = mock_notifier(has_services=True)
        orch = _make_orch(notifier=notifier, registry=registry)
        with _run_patches(orch):
            exit_code = orch.run()
        return exit_code, notifier, item

    def test_gate_on_sends_the_errors_push(self):
        exit_code, notifier, item = self._run_failing_target(notify=True)
        self.assertEqual(exit_code, EXIT_CODE_SCRAPE_ERROR)
        notifier.notify_errors.assert_called_once()
        (failed_items,), _ = notifier.notify_errors.call_args
        self.assertEqual([(i, type(e)) for i, e in failed_items],
                         [(item, ScraperParseError)])

    def test_gate_off_suppresses_the_errors_push_but_not_stale_alerts(self):
        stale = (NOW - datetime.timedelta(hours=OLD_ENTRY_HOURS + 1)).strftime(TIMESTAMP_FORMAT)
        exit_code, notifier, item = self._run_failing_target(notify=False, last_checked=stale)
        self.assertEqual(exit_code, EXIT_CODE_SCRAPE_ERROR)
        # The per-scraper opt-out silences only the Scraping Errors push...
        notifier.notify_errors.assert_not_called()
        # ...while the stale-tracking alert still goes out, so a persistent
        # problem cannot be muted entirely.
        notifier.notify_old_entries.assert_called_once_with([item], OLD_ENTRY_HOURS)

    def test_invalid_config_url_is_aggregated_without_scrape_exit(self):
        row = {"name": "Bad URL", "url": "https://wrong.example/item", "target_price": 5}
        item = BaseTrackedItem(name="Bad URL", url=row["url"], target_price=5.0)
        registry, manager, _ = _wired_target([row], item=item)
        manager.is_scrapable_item.return_value = False
        notifier = mock_notifier(has_services=True)
        notifier.notify_errors.return_value = True
        orch = _make_orch(registry=registry, notifier=notifier)

        with _run_patches(orch):
            exit_code = orch.run()

        self.assertEqual(exit_code, EXIT_CODE_SUCCESS)
        (failed_items,), _ = notifier.notify_errors.call_args
        self.assertEqual(len(failed_items), 1)
        self.assertIsInstance(failed_items[0][1], InvalidURLError)

    def test_failed_aggregated_error_push_sets_notification_exit(self):
        row = {"name": "Bad URL", "url": "https://wrong.example/item", "target_price": 5}
        item = BaseTrackedItem(name="Bad URL", url=row["url"], target_price=5.0)
        registry, manager, _ = _wired_target([row], item=item)
        manager.is_scrapable_item.return_value = False
        notifier = mock_notifier(has_services=True)
        notifier.notify_errors.return_value = False
        ui = mock_ui()
        orch = _make_orch(registry=registry, notifier=notifier, ui=ui)

        with _run_patches(orch):
            exit_code = orch.run()

        self.assertEqual(exit_code, EXIT_CODE_NOTIFICATION_ERROR)
        ui.log_warning.assert_called_once_with(
            "Notifications", messages.WARN_ERROR_NOTIFICATION_FAILED,
        )

    def test_failed_stale_push_sets_notification_exit(self):
        stale = (NOW - datetime.timedelta(hours=OLD_ENTRY_HOURS + 1)).strftime(TIMESTAMP_FORMAT)
        row = {"name": "Bad URL", "url": "https://wrong.example/item", "target_price": 5}
        item = BaseTrackedItem(
            name="Bad URL", url=row["url"], target_price=5.0, last_checked=stale,
        )
        registry, manager, _ = _wired_target([row], item=item, notify=False)
        manager.is_scrapable_item.return_value = False
        notifier = mock_notifier(has_services=True)
        notifier.notify_old_entries.return_value = False
        ui = mock_ui()
        orch = _make_orch(registry=registry, notifier=notifier, ui=ui)

        with _run_patches(orch):
            exit_code = orch.run()

        self.assertEqual(exit_code, EXIT_CODE_NOTIFICATION_ERROR)
        notifier.notify_old_entries.assert_called_once_with([item], OLD_ENTRY_HOURS)
        notifier.notify_errors.assert_not_called()
        ui.log_warning.assert_called_once_with(
            "Notifications", messages.WARN_STALE_NOTIFICATION_FAILED,
        )


class TestRunDependencySkips(unittest.TestCase):
    """A scraper whose dependencies are missing is skipped alone; the run proceeds."""

    def test_manager_dependency_error_skips_only_that_target(self):
        registry, manager, settings = _wired_target([{"skip": True}])
        registry.get_manager.side_effect = [PluginDependencyError("deps missing"), manager]
        ui = mock_ui()
        orch = _make_orch(ui=ui, registry=registry, targets=["heavy", "healthy"])

        with _run_patches(orch):
            exit_code = orch.run()

        self.assertEqual(exit_code, EXIT_CODE_PLUGIN_DEPENDENCY_ERROR)
        ui.log_error.assert_called_once_with("System", "deps missing")
        # The healthy target still completed a full pass (its save ran).
        manager.save.assert_called_once()

    def test_scraper_dependency_error_skips_only_that_target(self):
        product_row = {"name": "Widget", "url": "https://x/s/1/p.html", "target_price": 5}
        registry, manager, _ = _wired_target([product_row], item=_item(target_price=5.0))
        registry.get_scraper.side_effect = PluginDependencyError("client deps missing")
        ui = mock_ui()
        orch = _make_orch(ui=ui, registry=registry)

        with _run_patches(orch):
            exit_code = orch.run()

        self.assertEqual(exit_code, EXIT_CODE_PLUGIN_DEPENDENCY_ERROR)
        ui.log_error.assert_called_once_with("System", "client deps missing")
        ui.complete_target.assert_called()


if __name__ == "__main__":
    unittest.main()
