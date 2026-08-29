"""Apprise-backed notification transport.

The only module that talks to Apprise. It receives URLs that
``notifications.configuration`` already validated and bodies that
``notifications.templates`` already composed, so its own job is narrow: register
endpoints, dispatch, and convert every transport outcome into a ``bool``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING

import apprise

from core.notifications.templates import (
    NotificationMessage,
    crash_message,
    price_drop_message,
    reminder_message,
    scraping_errors_message,
    stale_items_message,
    test_message,
)

if TYPE_CHECKING:
    from core.scrapers.api import TrackedItem


class AppriseNotifier:
    """Dispatch notification messages through already-validated Apprise URLs.

    Constructed once per run. A URL that Apprise itself rejects at registration is
    dropped silently here: the configuration layer has already classified and
    reported invalid URLs to the user, so failing again would double-report a
    problem the user has been told about, and would take down the endpoints that
    do work.
    """

    def __init__(self, valid_urls: Sequence[str]) -> None:
        self.app_notif = apprise.Apprise()
        self._has_services = False
        for raw_url in tuple(valid_urls):
            url = raw_url.strip()
            if url and self.app_notif.add(url):
                self._has_services = True

    @property
    def has_services(self) -> bool:
        """Whether at least one URL registered successfully."""
        return self._has_services

    def notify(self, title: str, body: str) -> bool:
        """Deliver one message to every registered endpoint.

        Absorbs every transport exception and returns ``False``: a push service
        being unreachable is a delivery outcome the run reports, never a reason to
        unwind a scrape whose prices are already valid and worth persisting.
        """
        try:
            return bool(self.app_notif.notify(title=title, body=body))
        except Exception:
            return False

    def _dispatch(self, message: NotificationMessage | None) -> bool:
        """Deliver a composed message, treating "nothing to say" as not delivered.

        A ``None`` message means the template found an empty subject (no stale
        items, no failures). Callers already guard those cases, so this is
        defensive only and must not be read as a delivery failure worth alerting
        on.
        """
        return False if message is None else self.notify(message.title, message.body)

    def notify_low_price(
        self,
        site: str,
        item_name: str,
        target_price: float,
        current_price: float,
        url: str | None,
        currency: str = "€",
        advert_title: str | None = None,
    ) -> bool:
        """Compose and deliver the price-drop alert."""
        return self._dispatch(
            price_drop_message(
                site,
                item_name,
                target_price,
                current_price,
                url,
                currency,
                advert_title,
            )
        )

    def notify_stale_items(
        self,
        site: str,
        stale_items: Sequence[TrackedItem],
        hours: int,
        reference_url: Callable[[TrackedItem], str | None] | None = None,
    ) -> bool:
        """Compose and deliver the stale-tracking warning."""
        return self._dispatch(stale_items_message(site, stale_items, hours, reference_url))

    def notify_errors(
        self, site: str, failed_items: Sequence[tuple[TrackedItem, Exception]]
    ) -> bool:
        """Compose and deliver the scraping-errors summary."""
        return self._dispatch(scraping_errors_message(site, failed_items))

    def notify_reminder(
        self, update_available: bool | None, interval_display: str, next_due: str
    ) -> bool:
        """Compose and deliver the periodic reminder."""
        return self._dispatch(reminder_message(update_available, interval_display, next_due))

    def notify_crash(self) -> bool:
        """Compose and deliver the crash notice."""
        return self._dispatch(crash_message())

    def notify_test(self) -> list[tuple[str, bool]]:
        """Deliver the ping payload to each endpoint separately, reporting each result.

        Unlike every other method this reports per endpoint, because ``ping``
        exists to tell the user *which* URL is misconfigured. The identifier is
        redacted twice over: Apprise's own privacy form hides credentials, and
        everything after the host is replaced with ``/...`` because a path segment
        is itself a secret in webhook-style URLs.
        """
        message = test_message()
        results: list[tuple[str, bool]] = []
        for server in self.app_notif.servers:
            identifier = server.url(privacy=True)
            schema_end = identifier.find("://")
            if schema_end != -1:
                first_slash = identifier.find("/", schema_end + 3)
                if first_slash != -1:
                    identifier = identifier[:first_slash] + "/..."
            try:
                success = bool(server.notify(title=message.title, body=message.body))
            except Exception:
                success = False
            results.append((identifier, success))
        return results


__all__ = ["AppriseNotifier"]
