"""Evaluation of successful scrape results and staged state changes."""

from __future__ import annotations

import datetime
import logging
from collections.abc import Callable

from core import messages
from core.application.contracts import PriceOutcome, RunReporter
from core.constants import MAX_RETRIES, STALE_ITEM_HOURS
from core.infrastructure.logging import save_traceback
from core.infrastructure.persistence import format_utc
from core.notifications.contracts import NotificationService
from core.scrapers.api import ListingResult, ScrapeResult, TrackedItem
from core.scrapers.framework.state import JsonStateRepository


class ResultHandler:
    """Evaluate typed results, notify, and stage one state mutation."""

    def __init__(
        self,
        *,
        target: str,
        display_name: str,
        state: JsonStateRepository,
        notifier: NotificationService,
        reporter: RunReporter,
        logger: logging.Logger,
        now_fn: Callable[[], datetime.datetime],
        suppress_repeated_price_alerts: bool = False,
        reference_url: Callable[[TrackedItem], str | None],
    ) -> None:
        self.target = target
        self.display_name = display_name
        self.state = state
        self.notifier = notifier
        self.reporter = reporter
        self.logger = logger
        self.now_fn = now_fn
        self.suppress_repeated_price_alerts = suppress_repeated_price_alerts
        self.reference_url = reference_url
        self.stale_items: list[TrackedItem] = []

    def stale_note(self, item: TrackedItem) -> str | None:
        """Note and record an item unchecked for too long, or ``None`` if recent.

        Called on failure paths only. It is the safety net for failures that are
        individually quiet: an item that keeps failing eventually surfaces here
        even when nothing else alerts.
        """
        last_checked = self.state.get(item.id).last_checked
        if last_checked is None:
            return None
        if self.now_fn() - last_checked > datetime.timedelta(hours=STALE_ITEM_HOURS):
            self.stale_items.append(item)
            return messages.stale_note(format_utc(last_checked), STALE_ITEM_HOURS)
        return None

    def _try_notification(self, operation: Callable[[], bool]) -> bool:
        try:
            return bool(operation())
        except Exception:
            save_traceback(self.logger, target_name=self.target, log_to_console=False)
            return False

    def _notify_matching_offers(
        self, item: TrackedItem, result: ListingResult, notes: list[str]
    ) -> tuple[PriceOutcome, bool, tuple[str, ...]]:
        offers = tuple(result.offers)
        below = [offer for offer in offers if offer.price < item.target_price]
        notes.append(messages.advert_matches_note(len(offers), len(below)))
        if not below:
            return (
                PriceOutcome.NO_TARGET if item.target_price == 0.0 else PriceOutcome.OK,
                False,
                (),
            )

        previous_urls = set(self.state.get(item.id).notified_offer_urls)
        current_urls = {offer.url for offer in below}
        successfully_notified = previous_urls & current_urls
        failed = 0
        attempted = 0
        suppressed = 0
        seen_urls: set[str] = set()
        for match in below:
            if self.suppress_repeated_price_alerts and (
                match.url in successfully_notified or match.url in seen_urls
            ):
                suppressed += 1
                seen_urls.add(match.url)
                continue
            seen_urls.add(match.url)
            attempted += 1
            if self.notifier.has_services:
                delivered = self._try_notification(
                    lambda match=match: self.notifier.notify_low_price(
                        self.display_name,
                        item.name,
                        item.target_price,
                        match.price,
                        match.url,
                        result.currency,
                        advert_title=match.title,
                    )
                )
                failed += not delivered
                if delivered:
                    successfully_notified.add(match.url)

        if attempted and self.notifier.has_services:
            notes.append(
                messages.advert_notified_ok(attempted)
                if failed == 0
                else messages.advert_notified_fail(failed, attempted)
            )
        elif attempted:
            notes.append(messages.NOTE_NOTIFIED_NONE)
        if suppressed:
            notes.append(messages.advert_alerts_suppressed(suppressed))

        notified_urls = tuple(
            dict.fromkeys(offer.url for offer in below if offer.url in successfully_notified)
        )
        return PriceOutcome.DROP, failed > 0, notified_urls

    def handle(
        self,
        item: TrackedItem,
        result: ScrapeResult,
        retries_used: int,
        attempt_notes: list[str],
    ) -> bool:
        """Evaluate one successful result, notify if warranted, and stage its state.

        The only place a price is compared against a target. Both result shapes end
        the same way — reported to the frontend and staged for the single state
        commit — but they differ in what "below target" means: a single price is
        one continuous episode, while a listing alerts per offer and remembers
        offers by canonical URL.

        State is staged whatever the comparison found, because a check that
        completed is worth recording even when it alerts nothing.

        Returns:
            Whether any notification failed to deliver.
        """
        notes = (
            [messages.succeeded_on_attempt(retries_used + 1, MAX_RETRIES)] if retries_used else []
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
        price_alert_delivered = False
        notified_offer_urls: tuple[str, ...] = ()
        if isinstance(result, ListingResult):
            current_price = min(offer.price for offer in result.offers)
            outcome, notification_failed, notified_offer_urls = self._notify_matching_offers(
                item, result, notes
            )
        else:
            current_price = result.price
            if current_price < item.target_price:
                outcome = PriceOutcome.DROP
                previous = self.state.get(item.id)
                same_episode = (
                    previous.last_price is not None and previous.last_price < item.target_price
                )
                price_alert_delivered = same_episode and previous.price_alert_delivered
                if self.suppress_repeated_price_alerts and price_alert_delivered:
                    notes.append(messages.NOTE_REPEATED_PRICE_ALERT_SUPPRESSED)
                elif self.notifier.has_services:
                    link = result.url or self.reference_url(item)
                    delivered = self._try_notification(
                        lambda: self.notifier.notify_low_price(
                            self.display_name,
                            item.name,
                            item.target_price,
                            current_price,
                            link,
                            result.currency,
                        )
                    )
                    notes.append(
                        messages.NOTE_NOTIFIED_OK if delivered else messages.NOTE_NOTIFIED_FAIL
                    )
                    notification_failed = not delivered
                    price_alert_delivered |= delivered
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
        self.state.record_priced_check(
            item.id,
            current_price,
            checked_at,
            price_alert_delivered=price_alert_delivered,
            notified_offer_urls=notified_offer_urls,
        )
        return notification_failed


__all__ = ["ResultHandler"]
