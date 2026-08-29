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
from core.exceptions import InvalidURLError
from core.exit_status import ExitStatus
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
        self._request_started = False
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
        """Items found unchecked for too long, collected for one summary alert."""
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

    def _prepare_retry(self, attempt: int, attempt_notes: list[str]) -> None:
        """Reset transport state, folding a preparation fault into the failed attempt.

        A client that cannot reset itself is not a separate failure mode: the next
        attempt runs with whatever transport state remains and is classified by the
        normal policy. Containing the fault here keeps one item's reset from ending
        the target and discarding the run's still-unsaved checks, while the quiet
        traceback preserves the diagnostic an escaping exception used to leave.
        """
        try:
            self.client.prepare_retry()
        except Exception as exc:
            save_traceback(self.logger, target_name=self.target, log_to_console=False)
            attempt_notes.append(messages.retry_preparation_note(attempt + 1, type(exc).__name__))

    def process(self, item: TrackedItem) -> ItemRunOutcome:
        """Execute one item to completion: pace, attempt, retry, report, stage state.

        The loop applies policy, it does not decide it: which failures retry, abort,
        alert, or raise a status all comes from ``retry.py``. Interruption is polled
        between every step so a signal stops the run promptly without abandoning a
        request mid-flight.

        Only the scrape itself is classified. Evaluation and reporting run after the
        classifying block deliberately, so a defect on this side can never be
        answered by re-requesting a page that was already read; see the comment on
        that boundary before moving anything across it.

        Returns:
            What this item contributed to the run; never raises for a scrape
            failure, which is reported as an outcome instead. A fault in the
            post-success work does propagate, ending the target.
        """
        if item.skip:
            self.reporter.log_result("✅", item.name, "Skipped", messages.NOTE_SKIP_FIELD)
            return ItemRunOutcome(item)
        # Pacing separates consecutive requests, so this target's first request starts
        # immediately; run start is already spread out by the timer's randomized delay.
        if self._request_started:
            self.pacer.sleep(MIN_DELAY_SECONDS)
            if self.interrupted():
                return ItemRunOutcome(item)
        self._request_started = True

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
                        statuses=(
                            frozenset({policy.exit_status})
                            if policy.exit_status is not None
                            else frozenset()
                        ),
                        abort_target=policy.abort,
                    )
                if policy.prepare_before_retry:
                    self._prepare_retry(attempt, attempt_notes)
                self.pacer.sleep(MIN_DELAY_SECONDS, attempt, is_retry=True)
                continue

            # The scrape succeeded; everything below is post-success work and sits
            # outside the classifier above on purpose. A fault here is a defect in
            # our own evaluation or reporting, not a scraping failure, and the
            # classifier would answer it by re-requesting a page that was already
            # read correctly — tripling load on a third-party store to work around
            # a bug on this side.
            #
            # Do not wrap this in its own handler to keep the run's staged results.
            # Nothing here can fail for an environmental reason: deliveries are
            # already contained by ResultHandler, its diagnostic write by
            # try_save_traceback, state recording is in-memory over pre-validated
            # values, and both reporters absorb their own faults. What is left is
            # only a programming error, and letting it reach TargetRunner's
            # lifecycle handler keeps it loud and costs one cycle of state that the
            # next timer firing rebuilds. Catching it here would dress a defect up
            # as an ordinary handled outcome and it would stop being investigated.
            if self.interrupted():
                break
            notification_failed = self.results.handle(item, result, attempt, attempt_notes)
            statuses = (
                frozenset({ExitStatus.NOTIFICATION_ERROR}) if notification_failed else frozenset()
            )
            return ItemRunOutcome(item, statuses=statuses)
        return ItemRunOutcome(item)


__all__ = ["ItemExecutor"]
