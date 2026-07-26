"""Apprise-backed notification transport."""

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
    """Dispatch notification messages through already-validated Apprise URLs."""

    def __init__(self, valid_urls: Sequence[str]) -> None:
        self.app_notif = apprise.Apprise()
        self._has_services = False
        for raw_url in tuple(valid_urls):
            url = raw_url.strip()
            if url and self.app_notif.add(url):
                self._has_services = True

    @property
    def has_services(self) -> bool:
        return self._has_services

    def notify(self, title: str, body: str) -> bool:
        try:
            return bool(self.app_notif.notify(title=title, body=body))
        except Exception:
            return False

    def _dispatch(self, message: NotificationMessage | None) -> bool:
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
        return self._dispatch(stale_items_message(site, stale_items, hours, reference_url))

    def notify_errors(
        self, site: str, failed_items: Sequence[tuple[TrackedItem, Exception]]
    ) -> bool:
        return self._dispatch(scraping_errors_message(site, failed_items))

    def notify_reminder(
        self, update_available: bool | None, interval_display: str, next_due: str
    ) -> bool:
        return self._dispatch(reminder_message(update_available, interval_display, next_due))

    def notify_crash(self) -> bool:
        return self._dispatch(crash_message())

    def notify_test(self) -> list[tuple[str, bool]]:
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
