from datetime import datetime, timedelta, timezone
from unittest import mock

from core import messages
from core.application.contracts import PriceOutcome, RunReporter
from core.application.results import ResultHandler
from core.scrapers.api import ListingResult, Offer, PriceResult, TrackedItem, UrlField
from core.scrapers.framework.state import JsonStateRepository, StateEntry

NOW = datetime(2026, 7, 18, 18, 30, tzinfo=timezone.utc)
URL = UrlField("url", domains=("example.com",), accepts_url=lambda _url: True)


def _handler(*, services=False, delivered=True, reference_url=None, suppress=False, entry=None):
    notifier = mock.Mock()
    notifier.has_services = services
    notifier.notify_low_price.return_value = delivered
    reporter = mock.create_autospec(RunReporter, instance=True)
    state = mock.create_autospec(JsonStateRepository, instance=True)
    state.get.return_value = entry or StateEntry()
    handler = ResultHandler(
        target="teststore",
        display_name="Test Store",
        state=state,
        notifier=notifier,
        reporter=reporter,
        logger=mock.Mock(),
        now_fn=lambda: NOW,
        suppress_repeated_price_alerts=suppress,
        reference_url=reference_url or (lambda item: item[URL]),
    )
    return handler, notifier, reporter, state


def _item(**changes):
    url = changes.pop("url", "https://example.com/one")
    values = dict(id="one", name="One", target_price=10, _custom={URL: url})
    values.update(changes)
    return TrackedItem(**values)


def test_price_result_updates_state_and_sends_repeated_drop_notification():
    handler, notifier, reporter, state = _handler(services=True)
    item = _item()
    assert not handler.handle(item, PriceResult(5, "EUR"), 0, [])
    notifier.notify_low_price.assert_called_once_with(
        "Test Store", "One", 10, 5.0, item[URL], "EUR"
    )
    state.record_priced_check.assert_called_once_with(
        "one",
        5.0,
        NOW,
        price_alert_delivered=True,
        notified_offer_urls=(),
    )
    assert reporter.log_price_result.call_args.args[4] is PriceOutcome.DROP


def test_result_url_takes_precedence_and_url_free_result_omits_link():
    handler, notifier, _, _ = _handler(services=True)
    item = _item()
    handler.handle(item, PriceResult(5, "EUR", "https://result.example/item"), 0, [])
    assert notifier.notify_low_price.call_args.args[4] == "https://result.example/item"

    handler, notifier, _, _ = _handler(services=True, reference_url=lambda _item: None)
    handler.handle(item, PriceResult(5, "EUR"), 0, [])
    assert notifier.notify_low_price.call_args.args[4] is None


def test_listing_no_match_only_refreshes_timestamp():
    handler, notifier, reporter, state = _handler(services=True)
    item = _item()
    assert not handler.handle(item, ListingResult("EUR", []), 0, [])
    state.record_no_price_check.assert_called_once_with("one", NOW)
    state.record_priced_check.assert_not_called()
    notifier.notify_low_price.assert_not_called()
    assert reporter.log_price_result.call_args.args[4] is PriceOutcome.NO_MATCH


def test_listing_alerts_each_below_target_and_reports_partial_failure():
    handler, notifier, reporter, state = _handler(services=True)
    notifier.notify_low_price.side_effect = [True, False]
    item = _item()
    result = ListingResult(
        "EUR",
        [
            Offer("A", 5, "https://example.com/a"),
            Offer("B", 6, "https://example.com/b"),
            Offer("C", 12, "https://example.com/c"),
        ],
    )
    assert handler.handle(item, result, 0, [])
    assert notifier.notify_low_price.call_count == 2
    assert reporter.log_price_result.call_args.kwargs["delivery_failed"] is True
    state.record_priced_check.assert_called_once_with(
        "one",
        5.0,
        NOW,
        price_alert_delivered=False,
        notified_offer_urls=("https://example.com/a",),
    )


def test_single_price_suppresses_only_after_success_in_the_same_deal_episode():
    handler, notifier, reporter, state = _handler(
        services=True,
        suppress=True,
        entry=StateEntry(last_price=6, price_alert_delivered=True),
    )

    assert not handler.handle(_item(), PriceResult(5, "EUR"), 0, [])

    notifier.notify_low_price.assert_not_called()
    assert (
        messages.NOTE_REPEATED_PRICE_ALERT_SUPPRESSED
        in (reporter.log_price_result.call_args.kwargs["notes"])
    )
    state.record_priced_check.assert_called_once_with(
        "one",
        5.0,
        NOW,
        price_alert_delivered=True,
        notified_offer_urls=(),
    )


def test_single_price_retries_failed_delivery_and_realerts_after_recovery():
    handler, notifier, _, state = _handler(
        services=True,
        suppress=True,
        entry=StateEntry(last_price=6, price_alert_delivered=False),
    )
    handler.handle(_item(), PriceResult(5, "EUR"), 0, [])
    notifier.notify_low_price.assert_called_once()
    assert state.record_priced_check.call_args.kwargs["price_alert_delivered"] is True

    handler, notifier, _, state = _handler(
        services=True,
        suppress=True,
        entry=StateEntry(last_price=12, price_alert_delivered=True),
    )
    handler.handle(_item(), PriceResult(5, "EUR"), 0, [])
    notifier.notify_low_price.assert_called_once()
    assert state.record_priced_check.call_args.kwargs["price_alert_delivered"] is True


def test_listing_deduplicates_successes_by_url_and_retries_failed_urls():
    handler, notifier, reporter, state = _handler(
        services=True,
        suppress=True,
        entry=StateEntry(notified_offer_urls=("https://example.com/a",)),
    )
    item = _item()
    result = ListingResult(
        "EUR",
        [
            Offer("Already sent", 5, "https://example.com/a"),
            Offer("New", 6, "https://example.com/b"),
            Offer("Too expensive", 12, "https://example.com/c"),
        ],
    )

    assert not handler.handle(item, result, 0, [])

    notifier.notify_low_price.assert_called_once()
    assert notifier.notify_low_price.call_args.args[4] == "https://example.com/b"
    assert (
        messages.advert_alerts_suppressed(1) in reporter.log_price_result.call_args.kwargs["notes"]
    )
    assert state.record_priced_check.call_args.kwargs["notified_offer_urls"] == (
        "https://example.com/a",
        "https://example.com/b",
    )


def test_listing_forgets_missing_urls_and_collapses_duplicate_urls_when_enabled():
    handler, notifier, _, state = _handler(
        services=True,
        suppress=True,
        entry=StateEntry(notified_offer_urls=("https://example.com/missing",)),
    )
    duplicate = "https://example.com/a"
    result = ListingResult(
        "EUR",
        [Offer("First", 5, duplicate), Offer("Duplicate", 4, duplicate)],
    )

    handler.handle(_item(), result, 0, [])

    notifier.notify_low_price.assert_called_once()
    assert state.record_priced_check.call_args.kwargs["notified_offer_urls"] == (duplicate,)


def test_staleness_queries_state_by_item_id():
    handler, _, _, state = _handler()
    item = _item()
    state.get.return_value = StateEntry(last_checked=NOW - timedelta(hours=49))
    note = handler.stale_note(item)
    assert note and "48" in note
    assert handler.stale_items == [item]
    state.get.assert_called_with("one")
