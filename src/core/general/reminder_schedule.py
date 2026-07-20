"""Pure calendar arithmetic for periodic reminder slots."""

import datetime

from core.general.vocab import (
    DEFAULT_REMINDER_DAY,
    DEFAULT_REMINDER_TIME,
    time_parts,
    weekday_index,
)

_DEFAULT_SLOT_WEEKDAY = weekday_index(DEFAULT_REMINDER_DAY)
_DEFAULT_SLOT_HOUR, _DEFAULT_SLOT_MINUTE = time_parts(DEFAULT_REMINDER_TIME)


def most_recent_slot(
    now: datetime.datetime,
    weekday: int = _DEFAULT_SLOT_WEEKDAY,
    hour: int = _DEFAULT_SLOT_HOUR,
    minute: int = _DEFAULT_SLOT_MINUTE,
) -> datetime.datetime:
    """Return the latest matching weekday/time grid slot at or before ``now``."""
    days_back = (now.weekday() - weekday) % 7
    candidate = (now - datetime.timedelta(days=days_back)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if candidate > now:
        candidate -= datetime.timedelta(days=7)
    return candidate


def next_due_slot(last_slot: datetime.datetime, weeks: int) -> datetime.datetime:
    """Return the grid slot at which the next reminder becomes due."""
    return last_slot + datetime.timedelta(weeks=weeks)


__all__ = ["most_recent_slot", "next_due_slot"]
