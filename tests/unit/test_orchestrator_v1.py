from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest

from core.constants import (
    EXIT_CODE_INTERRUPT, EXIT_CODE_NOTIFICATION_ERROR, EXIT_CODE_PLUGIN_DEPENDENCY_ERROR,
    EXIT_CODE_PRODUCTS_ERROR, EXIT_CODE_RATE_LIMIT_ERROR, EXIT_CODE_SCRAPE_ERROR,
    EXIT_CODE_SKIPPED, EXIT_CODE_STORAGE_ERROR,
)
from core.orchestrator import RunOutcome, ScrapingOrchestrator, _policy_for
from core.exceptions import RateLimitError, ScraperParseError, ServerError
from core.scrapers.api import ListingResult, Offer, PriceResult, TrackedItem
from core.scrapers.registry import ScraperRegistry
from core.scrapers.state import JsonStateRepository
from core.ui.tui import ExecutionStrategy, PriceOutcome

NOW = datetime(2026, 7, 18, 18, 30, tzinfo=timezone.utc)


def _orch(*, services=False, delivered=True):
    notifier = mock.Mock()
    notifier.has_services = services
    notifier.notify_low_price.return_value = delivered
    ui = mock.create_autospec(ExecutionStrategy, instance=True)
    orch = ScrapingOrchestrator([], ScraperRegistry("/tmp/no-config"), notifier,
                               ui_strategy=ui, now_fn=lambda: NOW)
    return orch, notifier, ui


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
    assert _policy_for(RateLimitError()).abort
    assert not _policy_for(ServerError()).refresh_before_retry
    assert _policy_for(ScraperParseError()).affects_exit_status
    assert _policy_for(RuntimeError()).save_traceback


def test_price_result_updates_state_and_sends_drop_notification():
    orch, notifier, ui = _orch(services=True)
    state = mock.create_autospec(JsonStateRepository, instance=True)
    item = _item()
    failed = orch._handle_successful_scrape(item, PriceResult(5, "EUR"), state)
    assert not failed
    notifier.notify_low_price.assert_called_once()
    state.update_item.assert_called_once_with(item, last_price=5.0, last_checked=NOW)
    assert ui.log_price_result.call_args.args[4] is PriceOutcome.DROP


def test_listing_no_match_only_refreshes_timestamp():
    orch, notifier, ui = _orch(services=True)
    state = mock.create_autospec(JsonStateRepository, instance=True)
    item = _item(last_price=8)
    assert not orch._handle_successful_scrape(item, ListingResult("EUR", []), state)
    state.update_item.assert_called_once_with(item, last_checked=NOW)
    notifier.notify_low_price.assert_not_called()
    assert ui.log_price_result.call_args.args[4] is PriceOutcome.NO_MATCH


def test_listing_alerts_each_below_target_and_reports_partial_failure():
    orch, notifier, ui = _orch(services=True)
    notifier.notify_low_price.side_effect = [True, False]
    state = mock.create_autospec(JsonStateRepository, instance=True)
    item = _item()
    result = ListingResult("EUR", [
        Offer("A", 5, "https://example.com/a"),
        Offer("B", 6, "https://example.com/b"),
        Offer("C", 12, "https://example.com/c"),
    ])
    assert orch._handle_successful_scrape(item, result, state)
    assert notifier.notify_low_price.call_count == 2
    assert ui.log_price_result.call_args.kwargs["delivery_failed"] is True
    state.update_item.assert_called_once_with(item, last_price=5.0, last_checked=NOW)


def test_stale_aware_timestamp_is_aggregated():
    orch, _, _ = _orch()
    stale = _item(last_checked=NOW - timedelta(hours=49))
    note = orch._check_staleness(stale)
    assert note and "48" in note
    assert orch._stale_items == [stale]
    assert orch._check_staleness(_item(last_checked=None)) is None
