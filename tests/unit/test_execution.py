from datetime import datetime, timedelta, timezone
from unittest import mock

from core.constants import (
    EXIT_CODE_INTERRUPT,
    EXIT_CODE_NOTIFICATION_ERROR,
    EXIT_CODE_PLUGIN_DEPENDENCY_ERROR,
    EXIT_CODE_PRODUCTS_ERROR,
    EXIT_CODE_RATE_LIMIT_ERROR,
    EXIT_CODE_SCRAPE_ERROR,
    EXIT_CODE_SKIPPED,
    EXIT_CODE_STORAGE_ERROR,
)
from core.exceptions import RateLimitError, ScraperParseError, ServerError
from core.execution import ItemExecutor, policy_for
from core.run import PriceOutcome, RunOutcome, RunReporter
from core.scrapers.api import ListingResult, Offer, PriceResult, ScraperClient, TrackedItem
from core.scrapers.state import JsonStateRepository, StateEntry

NOW = datetime(2026, 7, 18, 18, 30, tzinfo=timezone.utc)


def _executor(*, services=False, delivered=True):
    notifier = mock.Mock()
    notifier.has_services = services
    notifier.notify_low_price.return_value = delivered
    reporter = mock.create_autospec(RunReporter, instance=True)
    state = mock.create_autospec(JsonStateRepository, instance=True)
    state.get.return_value = StateEntry()
    client = mock.create_autospec(ScraperClient, instance=True)
    executor = ItemExecutor(
        target="teststore",
        display_name="Test Store",
        client=client,
        state=state,
        notifier=notifier,
        reporter=reporter,
        logger=mock.Mock(),
        interrupted=lambda: False,
        now_fn=lambda: NOW,
    )
    return executor, notifier, reporter, state


def _item(**changes):
    values = dict(id="one", name="One", url="https://example.com/one", target_price=10)
    values.update(changes)
    return TrackedItem(**values)


def test_run_outcome_exit_priority_and_skipped():
    assert RunOutcome(products_error=True, storage_error=True).exit_code(
        interrupted=False, target_count=1) == EXIT_CODE_PRODUCTS_ERROR
    code = lambda outcome, interrupted=False: outcome.exit_code(
        interrupted=interrupted, target_count=1,
    )
    assert code(RunOutcome(storage_error=True)) == EXIT_CODE_STORAGE_ERROR
    assert code(RunOutcome(dependency_error=True)) == EXIT_CODE_PLUGIN_DEPENDENCY_ERROR
    assert code(RunOutcome(scrape_error=True)) == EXIT_CODE_SCRAPE_ERROR
    assert code(RunOutcome(rate_limited=True)) == EXIT_CODE_RATE_LIMIT_ERROR
    assert code(RunOutcome(notification_error=True)) == EXIT_CODE_NOTIFICATION_ERROR
    assert code(RunOutcome(skipped_count=1)) == EXIT_CODE_SKIPPED
    assert code(RunOutcome(), True) == EXIT_CODE_INTERRUPT


def test_error_policies_preserve_retry_semantics():
    assert policy_for(RateLimitError()).abort
    assert not policy_for(ServerError()).refresh_before_retry
    assert policy_for(ScraperParseError()).affects_exit_status
    assert policy_for(RuntimeError()).save_traceback


def test_price_result_updates_state_and_sends_repeated_drop_notification():
    executor, notifier, reporter, state = _executor(services=True)
    item = _item()
    assert not executor._handle_success(item, PriceResult(5, "EUR"), 0, [])
    notifier.notify_low_price.assert_called_once_with(
        "Test Store", "One", 10, 5.0, item.url, "EUR"
    )
    state.record_priced_check.assert_called_once_with("one", 5.0, NOW)
    assert reporter.log_price_result.call_args.args[4] is PriceOutcome.DROP


def test_listing_no_match_only_refreshes_timestamp():
    executor, notifier, reporter, state = _executor(services=True)
    item = _item()
    assert not executor._handle_success(item, ListingResult("EUR", []), 0, [])
    state.record_no_price_check.assert_called_once_with("one", NOW)
    state.record_priced_check.assert_not_called()
    notifier.notify_low_price.assert_not_called()
    assert reporter.log_price_result.call_args.args[4] is PriceOutcome.NO_MATCH


def test_listing_alerts_each_below_target_and_reports_partial_failure():
    executor, notifier, reporter, state = _executor(services=True)
    notifier.notify_low_price.side_effect = [True, False]
    item = _item()
    result = ListingResult("EUR", [
        Offer("A", 5, "https://example.com/a"),
        Offer("B", 6, "https://example.com/b"),
        Offer("C", 12, "https://example.com/c"),
    ])
    assert executor._handle_success(item, result, 0, [])
    assert notifier.notify_low_price.call_count == 2
    assert reporter.log_price_result.call_args.kwargs["delivery_failed"] is True
    state.record_priced_check.assert_called_once_with("one", 5.0, NOW)


def test_staleness_queries_state_by_item_id():
    executor, _, _, state = _executor()
    item = _item()
    state.get.return_value = StateEntry(last_checked=NOW - timedelta(hours=49))
    note = executor._stale_note(item)
    assert note and "48" in note
    assert executor.stale_items == [item]
    state.get.assert_called_with("one")


def test_skipped_item_never_sleeps_or_scrapes():
    executor, _, reporter, _ = _executor()
    executor.sleep_with_jitter = mock.Mock()

    outcome = executor.process(_item(skip=True))

    assert outcome.reported_error is None
    executor.sleep_with_jitter.assert_not_called()
    executor.client.scrape.assert_not_called()
    reporter.log_result.assert_called_once()


def test_retry_refreshes_identity_then_succeeds():
    executor, _, reporter, state = _executor()
    executor.sleep_with_jitter = mock.Mock()
    executor.client.scrape.side_effect = [ScraperParseError("bad page"), PriceResult(12, "EUR")]

    outcome = executor.process(_item())

    assert outcome.reported_error is None
    assert executor.client.scrape.call_count == 2
    executor.client.refresh_identity.assert_called_once()
    assert executor.sleep_with_jitter.call_count == 2
    reporter.log_attempt.assert_called_once()
    state.record_priced_check.assert_called_once_with("one", 12.0, NOW)


def test_unexpected_fault_exhausts_retries_and_affects_exit_status(monkeypatch):
    executor, _, reporter, _ = _executor()
    executor.sleep_with_jitter = mock.Mock()
    executor.client.scrape.side_effect = RuntimeError("broken parser")
    traceback = mock.Mock()
    monkeypatch.setattr("core.execution.save_traceback", traceback)

    outcome = executor.process(_item())

    assert isinstance(outcome.reported_error, RuntimeError)
    assert outcome.affects_scrape_status
    assert executor.client.scrape.call_count == 3
    assert executor.client.refresh_identity.call_count == 2
    reporter.log_failure.assert_called_once()
    traceback.assert_called_once()


def test_server_error_exhaustion_is_modeled_success_without_identity_refresh():
    executor, _, reporter, _ = _executor()
    executor.sleep_with_jitter = mock.Mock()
    executor.client.scrape.side_effect = ServerError("remote failure")

    outcome = executor.process(_item())

    assert outcome.reported_error is None
    assert not outcome.affects_scrape_status
    assert not outcome.abort_target
    executor.client.refresh_identity.assert_not_called()
    reporter.log_failure.assert_called_once()


def test_rate_limit_exhaustion_aborts_target_and_sets_rate_status(monkeypatch):
    executor, _, _, _ = _executor()
    executor.sleep_with_jitter = mock.Mock()
    executor.client.scrape.side_effect = RateLimitError("blocked")
    monkeypatch.setattr("core.execution.save_traceback", mock.Mock())

    outcome = executor.process(_item())

    assert isinstance(outcome.reported_error, RateLimitError)
    assert outcome.abort_target
    assert outcome.rate_limited
    assert not outcome.affects_scrape_status


def test_interruption_after_pacing_does_not_start_scrape():
    executor, _, _, _ = _executor()
    executor.sleep_with_jitter = mock.Mock()
    executor.interrupted = lambda: True

    outcome = executor.process(_item())

    assert outcome.reported_error is None
    executor.client.scrape.assert_not_called()
