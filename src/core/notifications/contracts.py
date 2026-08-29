"""Application-facing notification contract.

The seam between deciding *that* something is worth telling the user and knowing
*how* to reach them. The application depends only on this protocol, so it never
imports Apprise, never sees a notification URL, and can be tested with a plain
double.

Every method returns ``bool`` rather than raising: a delivery failure is a
reportable run condition, not an error that should unwind a scrape whose results
are already valid. Implementations therefore absorb transport exceptions and
return ``False``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from core.scrapers.api import TrackedItem


class NotificationService(Protocol):
    """Notification operations used by application workflows.

    One method per distinct thing the project tells a user about. They are
    separate rather than one ``notify(kind, payload)`` because each carries
    genuinely different data, and because a caller must not be able to invent a
    new notification kind without a contract change.
    """

    @property
    def has_services(self) -> bool:
        """Whether any endpoint is configured and could accept a message.

        Callers check this before composing an alert, so an unconfigured install
        reports "no notification sent" instead of a delivery failure.
        """
        ...

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
        """Alert that one price fell below its target.

        ``advert_title`` distinguishes one advert of a listing result; ``url`` may
        be ``None`` when neither the result nor the item offers a link.
        """
        ...

    def notify_stale_items(
        self,
        site: str,
        stale_items: Sequence[TrackedItem],
        hours: int,
        reference_url: Callable[[TrackedItem], str | None] | None = None,
    ) -> bool:
        """Warn that items have gone unchecked for too long.

        The safety net for failures that are individually quiet, such as a long
        remote outage. ``reference_url`` resolves each item's link, since the
        notifier cannot read plugin-declared fields itself.
        """
        ...

    def notify_errors(
        self, site: str, failed_items: Sequence[tuple[TrackedItem, Exception]]
    ) -> bool:
        """Report the items whose failures the user should act on.

        Gated by the target's ``notify_scraping_errors`` setting; failures the
        policy keeps quiet never reach here.
        """
        ...

    def notify_reminder(
        self, update_available: bool | None, interval_display: str, next_due: str
    ) -> bool:
        """Send the periodic still-running reminder, with update availability."""
        ...

    def notify_crash(self) -> bool:
        """Report that the run failed outright, pointing at the error log."""
        ...


__all__ = ["NotificationService"]
