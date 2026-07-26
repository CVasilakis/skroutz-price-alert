"""Application-facing notification contract."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from core.scrapers.api import TrackedItem


class NotificationService(Protocol):
    """Notification operations used by application workflows."""

    @property
    def has_services(self) -> bool: ...

    def notify_low_price(
        self,
        site: str,
        item_name: str,
        target_price: float,
        current_price: float,
        url: str | None,
        currency: str = "€",
        advert_title: str | None = None,
    ) -> bool: ...

    def notify_stale_items(
        self,
        site: str,
        stale_items: Sequence[TrackedItem],
        hours: int,
        reference_url: Callable[[TrackedItem], str | None] | None = None,
    ) -> bool: ...

    def notify_errors(
        self, site: str, failed_items: Sequence[tuple[TrackedItem, Exception]]
    ) -> bool: ...

    def notify_reminder(
        self, update_available: bool | None, interval_display: str, next_due: str
    ) -> bool: ...

    def notify_crash(self) -> bool: ...


__all__ = ["NotificationService"]
