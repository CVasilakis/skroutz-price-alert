from datetime import datetime, timezone
from unittest import mock

from core.application.contracts import RunReporter
from core.application.items import ItemExecutor
from core.application.pacing import Pacer
from core.exceptions import RateLimitError, ScraperParseError, ServerError
from core.scrapers.api import PriceResult, ScraperClient, TrackedItem, UrlField
from core.scrapers.framework.state import JsonStateRepository, StateEntry

NOW = datetime(2026, 7, 18, 18, 30, tzinfo=timezone.utc)
URL = UrlField("url", domains=("example.com",), accepts_url=lambda _url: True)


def _executor():
    notifier = mock.Mock(has_services=False)
    reporter = mock.create_autospec(RunReporter, instance=True)
    state = mock.create_autospec(JsonStateRepository, instance=True)
    state.get.return_value = StateEntry()
    client = mock.create_autospec(ScraperClient, instance=True)
    pacer = mock.create_autospec(Pacer, instance=True)
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
        reference_url=lambda item: item[URL],
        pacer=pacer,
    )
    return executor, reporter, state, pacer


def _item(**changes):
    values = dict(
        id="one",
        name="One",
        target_price=10,
        _custom={URL: "https://example.com/one"},
    )
    values.update(changes)
    return TrackedItem(**values)


def test_skipped_item_never_paces_or_scrapes():
    executor, reporter, _, pacer = _executor()
    outcome = executor.process(_item(skip=True))
    assert outcome.reported_error is None
    pacer.sleep.assert_not_called()
    executor.client.scrape.assert_not_called()
    reporter.log_result.assert_called_once()


def test_retry_prepares_transport_then_succeeds():
    executor, reporter, state, pacer = _executor()
    executor.client.scrape.side_effect = [ScraperParseError("bad page"), PriceResult(12, "EUR")]
    outcome = executor.process(_item())
    assert outcome.reported_error is None
    assert executor.client.scrape.call_count == 2
    executor.client.prepare_retry.assert_called_once()
    assert pacer.sleep.call_count == 2
    reporter.log_attempt.assert_called_once()
    state.record_priced_check.assert_called_once_with(
        "one",
        12.0,
        NOW,
        price_alert_delivered=False,
        notified_offer_urls=(),
    )


def test_unexpected_fault_exhausts_retries_and_affects_exit_status(monkeypatch):
    executor, reporter, _, _ = _executor()
    executor.client.scrape.side_effect = RuntimeError("broken parser")
    traceback = mock.Mock()
    monkeypatch.setattr("core.application.items.save_traceback", traceback)
    outcome = executor.process(_item())
    assert isinstance(outcome.reported_error, RuntimeError)
    assert outcome.affects_scrape_status
    assert executor.client.scrape.call_count == 3
    assert executor.client.prepare_retry.call_count == 2
    reporter.log_failure.assert_called_once()
    traceback.assert_called_once()


def test_server_error_exhaustion_is_modeled_success_without_retry_preparation():
    executor, reporter, _, _ = _executor()
    executor.client.scrape.side_effect = ServerError("remote failure")
    outcome = executor.process(_item())
    assert outcome.reported_error is None
    assert not outcome.affects_scrape_status
    assert not outcome.abort_target
    executor.client.prepare_retry.assert_not_called()
    reporter.log_failure.assert_called_once()


def test_rate_limit_exhaustion_aborts_target_and_sets_rate_status(monkeypatch):
    executor, _, _, _ = _executor()
    executor.client.scrape.side_effect = RateLimitError("blocked")
    monkeypatch.setattr("core.application.items.save_traceback", mock.Mock())
    outcome = executor.process(_item())
    assert isinstance(outcome.reported_error, RateLimitError)
    assert outcome.abort_target
    assert outcome.rate_limited
    assert not outcome.affects_scrape_status


def test_interruption_after_pacing_does_not_start_scrape():
    executor, _, _, _ = _executor()
    executor.interrupted = lambda: True
    outcome = executor.process(_item())
    assert outcome.reported_error is None
    executor.client.scrape.assert_not_called()
