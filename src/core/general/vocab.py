"""The general-settings vocabulary: reminder cadence, weekday and time-of-day.

The analog of :mod:`core.scrapers.framework.intervals` for the project-wide settings -
the tolerant normalizers that fold the many ways a user might spell a value onto a
canonical form, plus the display/parse helpers the specs and the reminder scheduler read.
Kept separate from :mod:`core.general.settings` (which wires these into ``SettingSpec``
objects) so adding a general setting has a home for its vocabulary, mirroring the
per-scraper split.

Single source of truth for cadences:
    :data:`_REMINDERS` maps each canonical key to its ``(whole-week count, display)`` pair;
    :data:`SUPPORTED_REMINDERS` and the display table are *derived* from it, so adding a
    cadence is one row here and cannot leave the week-count and the display spelling out of
    sync (the drift the per-scraper ``intervals.py`` avoids the same way).

Import-light: stdlib plus the shared token-folding helpers only.
"""

import re

from core.settings.normalizers import alias_form, fold_token

DEFAULT_REMINDER = "1m"

# The weekday and time the reminder grid is anchored to, in the host's *local* time (the
# user picks these; the grid is naive-local wall clock, see core.general.reminder). The
# defaults reproduce the historical fixed grid: Saturday 13:00.
DEFAULT_REMINDER_DAY = "Saturday"
DEFAULT_REMINDER_TIME = "13:00"

# The single authoritative cadence table: canonical key -> (whole-week count, display).
# Whole weeks keep ``slot + interval`` on the anchor slot grid by construction (see
# core.general.reminder), so "1 month" means "every 4th anchor day" - no month-length or
# leap-year arithmetic anywhere. A count of ``None`` means the reminder is disabled.
# Everything else about a cadence (its week count, its user-facing spelling) is read from
# here, so the two can never drift.
_REMINDERS: dict[str, tuple[int | None, str]] = {
    "off": (None, "off"),
    "1w": (1, "1 week"),
    "1m": (4, "1 month"),
    "3m": (13, "3 months"),
    "6m": (26, "6 months"),
    "1y": (52, "1 year"),
}

# Canonical reminder cadences mapped to their whole-week count (derived; None = disabled).
SUPPORTED_REMINDERS: dict[str, int | None] = {key: weeks for key, (weeks, _) in _REMINDERS.items()}

# Canonical key -> the user-facing spelling shown in panels and the notification body
# (derived from the authoritative table).
_REMINDER_DISPLAY: dict[str, str] = {key: display for key, (_, display) in _REMINDERS.items()}

# Canonical weekday name (as displayed) -> its datetime.weekday() index (Monday is 0).
_WEEKDAYS: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)
_WEEKDAY_INDEX: dict[str, int] = {name.lower(): i for i, name in enumerate(_WEEKDAYS)}

# Short spellings the user may type for a weekday (folded to lowercase first).
_WEEKDAY_ALIASES: dict[str, str] = {
    "mon": "Monday",
    "tue": "Tuesday",
    "tues": "Tuesday",
    "wed": "Wednesday",
    "weds": "Wednesday",
    "thu": "Thursday",
    "thur": "Thursday",
    "thurs": "Thursday",
    "fri": "Friday",
    "sat": "Saturday",
    "sun": "Sunday",
}

# Named cadences the user may type instead of a number+unit (whitespace, case, hyphens
# and underscores are stripped before this lookup).
_NAMED_ALIASES: dict[str, str] = {
    "off": "off",
    "disabled": "off",
    "never": "off",
    "none": "off",
    "weekly": "1w",
    "monthly": "1m",
    "quarterly": "3m",
    "yearly": "1y",
    "annually": "1y",
}

# Unit token group -> supported count -> canonical key. Counts are matched exactly
# (only the advertised cadences exist); "12 months" is folded onto "1y" so the two
# spellings of a year agree. Note the vocabulary is per-setting: here "1m" reads as
# "1 month", unlike execution_interval where "m" means minutes.
_UNIT_CANONICAL: tuple[tuple[tuple[str, ...], dict[int, str]], ...] = (
    (("w", "wk", "wks", "week", "weeks"), {1: "1w"}),
    (("m", "mo", "mos", "month", "months"), {1: "1m", 3: "3m", 6: "6m", 12: "1y"}),
    (("y", "yr", "yrs", "year", "years"), {1: "1y"}),
)


def normalize_reminder(raw: object) -> str | None:
    """Normalizes a broad user reminder value to a canonical key, or ``None``.

    Folds the many ways a user might write a cadence onto one of the
    :data:`SUPPORTED_REMINDERS` keys: e.g. ``"1 month"``, ``"1Month"``, ``"1mo"`` and
    ``"monthly"`` all return ``"1m"``; ``"12 months"`` and ``"yearly"`` return ``"1y"``;
    ``"off"``, ``"disabled"`` and ``"never"`` return ``"off"``. Whitespace and case are
    ignored. A bare number is rejected (no unit to disambiguate it).

    Args:
        raw: The user-supplied reminder value (any type; a non-string is rejected).

    Returns:
        str | None: The canonical key (e.g. ``"1m"``), or ``None`` if the value is
            unrecognized or resolves to an unsupported cadence.
    """
    # Collapse case and strip *all* whitespace so "1 Month" == "1month".
    token = fold_token(raw)
    if token is None:
        return None

    alias_key = alias_form(token)
    if alias_key in _NAMED_ALIASES:
        return _NAMED_ALIASES[alias_key]

    match = re.fullmatch(r"(\d+)([a-z]+)", token)
    if not match:
        return None

    count = int(match.group(1))
    unit = match.group(2)
    for unit_tokens, canonical_by_count in _UNIT_CANONICAL:
        if unit in unit_tokens:
            return canonical_by_count.get(count)
    return None


def normalize_reminder_day(raw: object) -> str | None:
    """Normalizes a user weekday value to a canonical capitalized name, or ``None``.

    Accepts full names and common short forms, case- and whitespace-insensitive: e.g.
    ``"saturday"``, ``"Sat"`` and ``"SAT"`` all return ``"Saturday"``; ``"tues"`` returns
    ``"Tuesday"``. Anything else (a non-string, an empty value, ``"funday"``) is rejected.

    Args:
        raw: The user-supplied weekday value (any type; a non-string is rejected).

    Returns:
        str | None: The canonical weekday name (e.g. ``"Saturday"``), or ``None``.
    """
    token = fold_token(raw)
    if token is None:
        return None
    if token in _WEEKDAY_INDEX:
        return _WEEKDAYS[_WEEKDAY_INDEX[token]]
    return _WEEKDAY_ALIASES.get(token)


def normalize_reminder_time(raw: object) -> str | None:
    """Normalizes a user time-of-day value to canonical ``"HH:MM"`` (24h), or ``None``.

    Accepts 24-hour (``"13"``, ``"13:00"``, ``"9:05"``) and 12-hour am/pm
    (``"1pm"``, ``"1:30pm"``, ``"12am"``) spellings, case- and whitespace-insensitive;
    a bare hour fills in ``:00``. The value is interpreted in the host's **local** time.
    Out-of-range or unparseable values are rejected.

    Args:
        raw: The user-supplied time value (any type; a non-string is rejected).

    Returns:
        str | None: The canonical ``"HH:MM"`` string, or ``None``.
    """
    token = fold_token(raw)
    if token is None:
        return None

    ampm = None
    if token.endswith("am") or token.endswith("pm"):
        ampm, token = token[-2:], token[:-2]

    match = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?", token)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2)) if match.group(2) is not None else 0

    if ampm is not None:
        if not 1 <= hour <= 12:
            return None
        if ampm == "am":
            hour = 0 if hour == 12 else hour
        else:
            hour = 12 if hour == 12 else hour + 12

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return f"{hour:02d}:{minute:02d}"


def weekday_index(canonical_day: str) -> int:
    """Returns the ``datetime.weekday()`` index (Monday is 0) for a canonical weekday name."""
    return _WEEKDAY_INDEX[canonical_day.lower()]


def time_parts(canonical_time: str) -> tuple[int, int]:
    """Splits a canonical ``"HH:MM"`` reminder time into an ``(hour, minute)`` pair."""
    hour, minute = canonical_time.split(":")
    return int(hour), int(minute)


def weeks_for(canonical: str) -> int | None:
    """Returns the whole-week count for a canonical reminder key (``None`` for ``"off"``)."""
    return SUPPORTED_REMINDERS[canonical]


def display_reminder(canonical: str) -> str:
    """Returns the user-facing spelling of a canonical reminder key (e.g. ``"1 month"``)."""
    return _REMINDER_DISPLAY[canonical]


def display_reminder_row(canonical: str) -> str:
    """Returns the reminder's settings-panel value: ``"off"``, or ``"Every <cadence>"``.

    Distinct from :func:`display_reminder` (the bare cadence, e.g. ``"1 month"``, used in
    the notification sentence "This is your 1 month reminder..."): the panel row reads as a
    schedule, e.g. ``"Every 1 month"``, while a disabled reminder stays ``"off"``.
    """
    if canonical == "off":
        return _REMINDER_DISPLAY[canonical]
    return f"Every {display_reminder(canonical)}"
