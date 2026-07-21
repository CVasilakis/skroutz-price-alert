"""Sequential execution of one validated scraper item."""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable

from core import messages
from core.application.contracts import ItemRunOutcome, Notes, RunReporter
from core.application.pacing import Pacer
from core.application.results import ResultHandler
from core.application.retry import ERRORS_LOG_TOKEN, SKIP_ERRORS, policy_for
from core.constants import MAX_RETRIES, MIN_DELAY_SECONDS
from core.exceptions import InvalidURLError, RateLimitError
from core.infrastructure.logging import save_traceback
from core.notifications.contracts import NotificationService
from core.scrapers.api import ScraperClient, TrackedItem, validate_scrape_result
from core.scrapers.framework.state import JsonStateRepository


class ItemExecutor:
    """Execute one item at a time for one already-selected plugin target."""

    def __init__(
        self,
        *,
        target: str,
        display_name: str,
        client: ScraperClient,
        state: JsonStateRepository,
        notifier: NotificationService,
        reporter: RunReporter,
        logger: logging.Logger,
        interrupted: Callable[[], bool],
        now_fn: Callable[[], datetime.datetime],
        suppress_repeated_price_alerts: bool = False,
        reference_url: Callable[[TrackedItem], str | None] | None = None,
        pacer: Pacer | None = None,
    ) -> None:
        self.target = target
        self.client = client
        self.reporter = reporter
        self.logger = logger
        self.interrupted = interrupted
        self.reference_url = reference_url or (lambda _item: None)
        self.pacer = pacer or Pacer(reporter, interrupted)
        self.results = ResultHandler(
            target=target,
            display_name=display_name,
            state=state,
            notifier=notifier,
            reporter=reporter,
            logger=logger,
            now_fn=now_fn,
            suppress_repeated_price_alerts=suppress_repeated_price_alerts,
            reference_url=self.reference_url,
        )

    @property
    def stale_items(self) -> list[TrackedItem]:
        return self.results.stale_items

    @staticmethod
    def _combine_notes(*notes: Notes) -> list[str] | None:
        flattened: list[str] = []
        for note in notes:
            if isinstance(note, str):
                flattened.append(note)
            elif note:
                flattened.extend(note)
        return flattened or None

    def process(self, item: TrackedItem) -> ItemRunOutcome:
        if item.skip:
            self.reporter.log_result("✅", item.name, "Skipped", messages.NOTE_SKIP_FIELD)
            return ItemRunOutcome(item)
        self.pacer.sleep(MIN_DELAY_SECONDS)
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
                notification_failed = self.results.handle(item, result, attempt, attempt_notes)
                return ItemRunOutcome(item, notification_failed=notification_failed)
            except SKIP_ERRORS as exc:
                self.reporter.log_error(
                    item.name,
                    messages.skipping_warning(type(exc).__name__),
                    notes=self._combine_notes(str(exc), self.results.stale_note(item)),
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
                        if note == ERRORS_LOG_TOKEN
                        else note
                        for note in policy.extra_notes
                    ]
                    self.reporter.log_failure(
                        item.name,
                        type(exc).__name__,
                        attempt_notes=attempt_notes,
                        extra_notes=self._combine_notes(extra, self.results.stale_note(item)),
                    )
                    if policy.save_traceback:
                        save_traceback(
                            self.logger,
                            target_name=self.target,
                            url=self.reference_url(item),
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
                self.pacer.sleep(MIN_DELAY_SECONDS, attempt, is_retry=True)
        return ItemRunOutcome(item)


__all__ = ["ItemExecutor"]
