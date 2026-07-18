"""Sequential item execution: pacing, retries, policies, and result evaluation."""

from __future__ import annotations

import datetime
import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass

from core import messages
from core.constants import (
    MAX_RETRIES,
    MIN_DELAY_SECONDS,
    OLD_ENTRY_HOURS,
    RANDOM_DELAY_MAX,
    RANDOM_DELAY_MIN,
    RETRY_DELAY_MULTIPLIER,
)
from core.exceptions import (
    InvalidURLError,
    ProductNotFoundError,
    ProductUnavailableError,
    RateLimitError,
    ScraperError,
    ScraperParseError,
    ServerError,
)
from core.logger import save_traceback
from core.notifier import Notifier
from core.persistence import format_utc
from core.run import ItemRunOutcome, Notes, PriceOutcome, RunReporter
from core.scrapers.api import (
    ListingResult,
    ScrapeResult,
    ScraperClient,
    TrackedItem,
    validate_scrape_result,
)
from core.scrapers.state import JsonStateRepository

SKIP_ERRORS = (ProductNotFoundError, ProductUnavailableError, InvalidURLError)
ERRORS_LOG_TOKEN = "<errors_log>"


@dataclass(frozen=True)
class ErrorPolicy:
    prepare_before_retry: bool = True
    abort: bool = False
    counts_as_failure: bool = True
    affects_exit_status: bool = False
    save_traceback: bool = False
    extra_notes: tuple[str, ...] = ()


DEFAULT_POLICY = ErrorPolicy(
    affects_exit_status=True,
    save_traceback=True,
    extra_notes=(ERRORS_LOG_TOKEN,),
)

RETRY_POLICIES: tuple[tuple[type[Exception], ErrorPolicy], ...] = (
    (RateLimitError, ErrorPolicy(
        abort=True,
        save_traceback=True,
        extra_notes=(messages.NOTE_RATE_LIMIT_ABORTED, ERRORS_LOG_TOKEN),
    )),
    (ServerError, ErrorPolicy(prepare_before_retry=False, counts_as_failure=False)),
    (ScraperParseError, ErrorPolicy(affects_exit_status=True)),
    (ScraperError, ErrorPolicy(save_traceback=True, extra_notes=(ERRORS_LOG_TOKEN,))),
)


def policy_for(exc: Exception) -> ErrorPolicy:
    return next((policy for exc_type, policy in RETRY_POLICIES if isinstance(exc, exc_type)), DEFAULT_POLICY)


class ItemExecutor:
    """Execute validated items for one already-selected plugin target."""

    def __init__(
        self,
        *,
        target: str,
        display_name: str,
        client: ScraperClient,
        state: JsonStateRepository,
        notifier: Notifier,
        reporter: RunReporter,
        logger: logging.Logger,
        interrupted: Callable[[], bool],
        now_fn: Callable[[], datetime.datetime],
    ) -> None:
        self.target = target
        self.display_name = display_name
        self.client = client
        self.state = state
        self.notifier = notifier
        self.reporter = reporter
        self.logger = logger
        self.interrupted = interrupted
        self.now_fn = now_fn
        self.stale_items: list[TrackedItem] = []

    def sleep_with_jitter(self, base_delay: float, attempt: int = 0,
                          *, is_retry: bool = False) -> None:
        jitter = random.uniform(RANDOM_DELAY_MIN, RANDOM_DELAY_MAX)
        total_delay = base_delay + RETRY_DELAY_MULTIPLIER * attempt + jitter
        start_time = time.monotonic()
        retry_attempt = attempt + 2 if is_retry else 0
        self.reporter.start_sleep(total_delay, retry_attempt, MAX_RETRIES if is_retry else 0)
        while time.monotonic() - start_time < total_delay:
            if self.interrupted():
                break
            remaining = max(0.0, total_delay - (time.monotonic() - start_time))
            self.reporter.update_sleep(remaining)
            time.sleep(0.05)
        if not self.interrupted():
            self.reporter.complete_sleep(time.monotonic() - start_time)

    def _stale_note(self, item: TrackedItem) -> str | None:
        last_checked = self.state.get(item.id).last_checked
        if last_checked is None:
            return None
        if self.now_fn() - last_checked > datetime.timedelta(hours=OLD_ENTRY_HOURS):
            self.stale_items.append(item)
            return messages.stale_note(format_utc(last_checked), OLD_ENTRY_HOURS)
        return None

    @staticmethod
    def _combine_notes(*notes: Notes) -> list[str] | None:
        flattened: list[str] = []
        for note in notes:
            if isinstance(note, str):
                flattened.append(note)
            elif note:
                flattened.extend(note)
        return flattened or None

    def _try_notification(self, operation: Callable[[], bool]) -> bool:
        try:
            return bool(operation())
        except Exception:
            save_traceback(self.logger, target_name=self.target, log_to_console=False)
            return False

    def _notify_matching_offers(
        self, item: TrackedItem, result: ListingResult, notes: list[str]
    ) -> tuple[PriceOutcome, bool]:
        offers = tuple(result.offers)
        below = [offer for offer in offers if offer.price < item.target_price]
        notes.append(messages.advert_matches_note(len(offers), len(below)))
        if not below:
            return (
                PriceOutcome.NO_TARGET if item.target_price == 0.0 else PriceOutcome.OK,
                False,
            )
        failed = 0
        if self.notifier.has_services:
            for match in below:
                delivered = self._try_notification(lambda match=match: self.notifier.notify_low_price(
                    self.display_name,
                    item.name,
                    item.target_price,
                    match.price,
                    match.url,
                    result.currency,
                    advert_title=match.title,
                ))
                failed += not delivered
            notes.append(
                messages.advert_notified_ok(len(below))
                if failed == 0
                else messages.advert_notified_fail(failed, len(below))
            )
        else:
            notes.append(messages.NOTE_NOTIFIED_NONE)
        return PriceOutcome.DROP, failed > 0

    def _handle_success(
        self,
        item: TrackedItem,
        result: ScrapeResult,
        retries_used: int,
        attempt_notes: list[str],
    ) -> bool:
        notes = (
            [messages.succeeded_on_attempt(retries_used + 1, MAX_RETRIES)]
            if retries_used else []
        )
        checked_at = self.now_fn()
        if isinstance(result, ListingResult) and not result.offers:
            self.reporter.log_price_result(
                item.name,
                None,
                result.currency,
                item.target_price,
                PriceOutcome.NO_MATCH,
                notes=notes,
                attempt_notes=attempt_notes,
            )
            self.state.record_no_price_check(item.id, checked_at)
            return False

        notification_failed = False
        if isinstance(result, ListingResult):
            current_price = min(offer.price for offer in result.offers)
            outcome, notification_failed = self._notify_matching_offers(item, result, notes)
        else:
            current_price = result.price
            if current_price < item.target_price:
                outcome = PriceOutcome.DROP
                if self.notifier.has_services:
                    delivered = self._try_notification(lambda: self.notifier.notify_low_price(
                        self.display_name,
                        item.name,
                        item.target_price,
                        current_price,
                        item.url,
                        result.currency,
                    ))
                    notes.append(messages.NOTE_NOTIFIED_OK if delivered else messages.NOTE_NOTIFIED_FAIL)
                    notification_failed = not delivered
                else:
                    notes.append(messages.NOTE_NOTIFIED_NONE)
            elif item.target_price == 0.0:
                outcome = PriceOutcome.NO_TARGET
            else:
                outcome = PriceOutcome.OK

        self.reporter.log_price_result(
            item.name,
            current_price,
            result.currency,
            item.target_price,
            outcome,
            notes=notes,
            attempt_notes=attempt_notes,
            delivery_failed=notification_failed,
        )
        self.state.record_priced_check(item.id, current_price, checked_at)
        return notification_failed

    def process(self, item: TrackedItem) -> ItemRunOutcome:
        if item.skip:
            self.reporter.log_result("✅", item.name, "Skipped", messages.NOTE_SKIP_FIELD)
            return ItemRunOutcome(item)
        self.sleep_with_jitter(MIN_DELAY_SECONDS)
        if self.interrupted():
            return ItemRunOutcome(item)

        attempt_notes: list[str] = []
        for attempt in range(MAX_RETRIES):
            if self.interrupted():
                break
            try:
                self.reporter.start_scraping(item.name, attempt + 1, MAX_RETRIES)
                try:
                    result = validate_scrape_result(self.client.scrape(item))
                finally:
                    self.reporter.complete_scraping()
                if self.interrupted():
                    break
                notification_failed = self._handle_success(item, result, attempt, attempt_notes)
                return ItemRunOutcome(item, notification_failed=notification_failed)
            except SKIP_ERRORS as exc:
                self.reporter.log_error(
                    item.name,
                    messages.skipping_warning(type(exc).__name__),
                    notes=self._combine_notes(str(exc), self._stale_note(item)),
                    attempt_notes=attempt_notes,
                )
                reported = exc if isinstance(exc, InvalidURLError) else None
                return ItemRunOutcome(item, reported_error=reported)
            except Exception as exc:
                policy = policy_for(exc)
                self.reporter.log_attempt(
                    item.name,
                    attempt + 1,
                    MAX_RETRIES,
                    f"{type(exc).__name__}: {exc}",
                )
                attempt_notes.append(messages.attempt_note(attempt + 1, type(exc).__name__))
                if attempt == MAX_RETRIES - 1:
                    extra = [
                        messages.errors_log_pointer(self.target)
                        if note == ERRORS_LOG_TOKEN else note
                        for note in policy.extra_notes
                    ]
                    self.reporter.log_failure(
                        item.name,
                        type(exc).__name__,
                        attempt_notes=attempt_notes,
                        extra_notes=self._combine_notes(extra, self._stale_note(item)),
                    )
                    if policy.save_traceback:
                        save_traceback(
                            self.logger,
                            target_name=self.target,
                            url=item.url,
                            diagnostic_context=self.client.diagnostic_context(),
                            log_to_console=False,
                        )
                    return ItemRunOutcome(
                        item,
                        reported_error=exc if policy.counts_as_failure else None,
                        affects_scrape_status=policy.affects_exit_status,
                        abort_target=policy.abort,
                        rate_limited=isinstance(exc, RateLimitError),
                    )
                if policy.prepare_before_retry:
                    self.client.prepare_retry()
                self.sleep_with_jitter(MIN_DELAY_SECONDS, attempt, is_retry=True)
        return ItemRunOutcome(item)


__all__ = ["ErrorPolicy", "ItemExecutor", "policy_for"]
