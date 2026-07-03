"""Execution-interval vocabulary and the systemd ``OnCalendar`` mapping (stdlib only)."""

import re


# Canonical execution intervals, in first-seen order, each mapped to the systemd
# OnCalendar expression it generates. This is the authoritative set of supported
# cadences: a normalized user value must resolve to one of these keys.
SUPPORTED_INTERVALS: dict[str, str] = {
    "15m": "*:0/15",
    "30m": "*:0/30",
    "1h": "hourly",
    "2h": "*-*-* 00/2:00:00",
    "4h": "*-*-* 00/4:00:00",
    "8h": "*-*-* 00/8:00:00",
    "12h": "*-*-* 00/12:00:00",
    "24h": "daily",
}

# Reverse of SUPPORTED_INTERVALS: a systemd OnCalendar expression back to its
# canonical key, for displaying an effective interval the user can recognize.
_ONCALENDAR_TO_CANONICAL: dict[str, str] = {v: k for k, v in SUPPORTED_INTERVALS.items()}

# Canonical key -> total minutes. Any broad user spelling that resolves to a
# supported number of minutes maps back to its canonical key through this table.
_CANONICAL_MINUTES: dict[str, int] = {
    "15m": 15, "30m": 30, "1h": 60, "2h": 120,
    "4h": 240, "8h": 480, "12h": 720, "24h": 1440,
}
_MINUTES_TO_CANONICAL: dict[int, str] = {m: k for k, m in _CANONICAL_MINUTES.items()}

# Named cadences the user may type instead of a number+unit (whitespace, case,
# hyphens and underscores are stripped before this lookup).
_NAMED_ALIASES: dict[str, int] = {
    "hourly": 60,
    "daily": 1440,
    "halfhourly": 30,
    "halfhour": 30,
}

# Unit token -> minutes-per-unit. A bare number (no unit) is read as minutes.
_UNIT_MINUTES = (
    (("", "m", "min", "mins", "minute", "minutes"), 1),
    (("h", "hr", "hrs", "hour", "hours"), 60),
    (("d", "day", "days"), 1440),
)


def normalize_interval(raw: object) -> str | None:
    """Normalizes a broad user interval string to a canonical key, or ``None``.

    Folds the many ways a user might write a cadence onto one of the
    :data:`SUPPORTED_INTERVALS` keys: e.g. ``"1h"``, ``"1 h"``, ``"1Hour"``,
    ``"1 hour"``, ``"60m"`` and ``"hourly"`` all return ``"1h"``; ``"daily"``,
    ``"1d"`` and ``"1440m"`` return ``"24h"``. Whitespace and case are ignored.

    Args:
        raw: The user-supplied interval value (any type; a non-string is rejected).

    Returns:
        str | None: The canonical key (e.g. ``"1h"``), or ``None`` if the value
            is unrecognized or resolves to an unsupported cadence.
    """
    if not isinstance(raw, str):
        return None

    # Collapse case and strip *all* whitespace so "1 Hour" == "1hour".
    token = re.sub(r"\s+", "", raw).lower()
    if not token:
        return None

    # Hyphens/underscores only appear in word aliases ("half-hourly"); drop them
    # for the alias lookup so both "half-hourly" and "halfhourly" resolve.
    alias_key = token.replace("-", "").replace("_", "")
    if alias_key in _NAMED_ALIASES:
        return _MINUTES_TO_CANONICAL.get(_NAMED_ALIASES[alias_key])

    match = re.fullmatch(r"(\d+)([a-z]*)", token)
    if not match:
        return None

    minutes_total = int(match.group(1))
    unit = match.group(2)
    for unit_tokens, per_unit in _UNIT_MINUTES:
        if unit in unit_tokens:
            return _MINUTES_TO_CANONICAL.get(minutes_total * per_unit)
    return None


def oncalendar_for(canonical: str) -> str:
    """Returns the systemd ``OnCalendar`` expression for a canonical interval key."""
    return SUPPORTED_INTERVALS[canonical]


def canonical_for_oncalendar(oncalendar: str) -> str | None:
    """Returns the canonical interval key for a systemd ``OnCalendar`` expression.

    The inverse of :func:`oncalendar_for`, used to display an effective schedule as a
    key the user recognizes (e.g. ``"hourly"`` -> ``"1h"``). Returns ``None`` for an
    expression that is not one of the supported cadences (e.g. a plugin's custom
    default), so the caller can fall back to showing the raw expression.

    Args:
        oncalendar (str): A systemd ``OnCalendar`` expression.

    Returns:
        str | None: The canonical key, or ``None`` if not a supported cadence.
    """
    return _ONCALENDAR_TO_CANONICAL.get(oncalendar)
