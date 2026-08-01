"""Pure construction of notification titles and bodies."""

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
    title: str
    body: str


def price_drop_message(
    site: str,
    item_name: str,
    target_price: float,
    current_price: float,
    url: str | None,
    currency: str,
    advert_title: str | None,
) -> NotificationMessage:
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
    if update_available is True:
        update_line = 'A project update is available — run "scrooge-alert update" to install it.'
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
    return NotificationMessage(
        TITLE_CRASH,
        "The script failed unexpectedly. Please review the error logs for more details on the crash.",
    )


def test_message() -> NotificationMessage:
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
