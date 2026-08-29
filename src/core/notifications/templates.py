"""Pure construction of notification titles and bodies.

Message wording lives here, apart from any transport, so what a user receives can
be tested without a network and reworded without touching delivery. Nothing in
this module knows an endpoint exists.

Summaries list a few items and then count the rest: a push notification is read on
a lock screen, and an unbounded body would be truncated by the transport at an
arbitrary point rather than at a sentence the reader can act on.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.scrapers.api import TrackedItem

TITLE_PRICE_DROP = "Scrooge Alert - Price Drop!"
TITLE_STATUS_UPDATE = "Scrooge Alert - Status Update"
TITLE_CRASH = "Scrooge Alert - Script Crash"
TITLE_TEST = "Scrooge Alert - Test Notification"


@dataclass(frozen=True)
class NotificationMessage:
    """One composed notification, ready for any transport.

    ``None`` in place of a message means there is nothing worth sending, which is
    how the summary templates report an empty subject.
    """

    title: str
    """The notification title, which most services show on its own."""

    body: str
    """Plain text; no markup, since transports render it differently or not at all."""


def price_drop_message(
    site: str,
    item_name: str,
    target_price: float,
    current_price: float,
    url: str | None,
    currency: str,
    advert_title: str | None,
) -> NotificationMessage:
    """Compose the alert for one price below its target."""
    advert_line = f"\nAdvert: {advert_title}" if advert_title else ""
    link_line = f"\nView it here: {url}" if url else ""
    return NotificationMessage(
        TITLE_PRICE_DROP,
        f"{item_name} is now available for {current_price}{currency} in {site}, "
        f"which is below your target of {target_price}{currency}.{advert_line}{link_line}",
    )


def _summary_message(
    title: str,
    header: str,
    items: Sequence[Any],
    format_item: Callable[[Any], str],
    footer: str,
    more_noun: str = "",
    max_show: int = 3,
) -> NotificationMessage:
    body_lines = [header]
    body_lines.extend(f"- {format_item(item)}" for item in items[:max_show])
    if len(items) > max_show:
        body_lines.append(f"... and {len(items) - max_show} more{more_noun}.")
    body_lines.append(footer)
    return NotificationMessage(title, "\n".join(body_lines))


def stale_items_message(
    site: str,
    stale_items: Sequence[TrackedItem],
    hours: int,
    reference_url: Callable[[TrackedItem], str | None] | None,
) -> NotificationMessage | None:
    """Compose the warning for items unchecked past the staleness threshold.

    Returns ``None`` when nothing is stale, so callers never send an empty summary.
    """
    if not stale_items:
        return None

    def format_item(item: TrackedItem) -> str:
        url = reference_url(item) if reference_url is not None else None
        return f"{item.name}: {url}" if url else item.name

    return _summary_message(
        title=f"Scrooge Alert - Tracking Stale on {site}",
        header=(
            f"{len(stale_items)} tracked item(s) on {site} haven't been successfully scraped "
            f"in over {hours} hours:\n"
        ),
        items=stale_items,
        format_item=format_item,
        footer="\nPlease check the error logs or verify the URLs are still valid.",
    )


def scraping_errors_message(
    site: str, failed_items: Sequence[tuple[TrackedItem, Exception]]
) -> NotificationMessage | None:
    """Compose the summary of items whose failures the user should act on.

    Returns ``None`` when nothing failed.
    """
    if not failed_items:
        return None
    return _summary_message(
        title=f"Scrooge Alert - Scraping Errors on {site}",
        header=(
            f"The script encountered errors while checking {len(failed_items)} "
            f"tracked item(s) on {site}:\n"
        ),
        items=failed_items,
        format_item=lambda pair: f"{pair[0].name}: {type(pair[1]).__name__}",
        footer="\nPlease review the error logs for more details.",
        more_noun=" errors",
    )


def reminder_message(
    update_available: bool | None, interval_display: str, next_due: str
) -> NotificationMessage:
    """Compose the periodic still-running reminder, including update availability.

    ``update_available`` is tri-state: ``None`` means the check itself failed, which
    is reported as unknown rather than silently as "up to date".
    """
    if update_available is True:
        update_line = 'A project update is available — run "./scrooge-alert update" to install it.'
    elif update_available is False:
        update_line = "You are running the latest version."
    else:
        update_line = "The update check failed; could not determine whether an update is available."
    return NotificationMessage(
        TITLE_STATUS_UPDATE,
        f"This is your {interval_display} reminder that the scrapers are still running in "
        f"the background.\n{update_line}\nNext reminder: on or shortly after {next_due} "
        '(local time).\nTo disable these reminders, set "reminder": "off" in '
        "config/general.json.",
    )


def crash_message() -> NotificationMessage:
    """Compose the notice that the run failed outright."""
    return NotificationMessage(
        TITLE_CRASH,
        "The script failed unexpectedly. Please review the error logs for more details on the crash.",
    )


def test_message() -> NotificationMessage:
    """Compose the ``ping`` payload used to verify notification delivery."""
    return NotificationMessage(
        TITLE_TEST,
        "This is a test message to confirm that your Scrooge Alert notifications are configured correctly!",
    )


__all__ = [
    "NotificationMessage",
    "TITLE_CRASH",
    "TITLE_PRICE_DROP",
    "TITLE_STATUS_UPDATE",
    "TITLE_TEST",
    "crash_message",
    "price_drop_message",
    "reminder_message",
    "scraping_errors_message",
    "stale_items_message",
    "test_message",
]
