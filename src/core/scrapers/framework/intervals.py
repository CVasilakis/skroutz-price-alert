"""Canonical execution intervals and systemd ``OnCalendar`` translation."""

import re

from core.settings.normalizers import alias_form, fold_token

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
_MINUTES = {
    "15m": 15,
    "30m": 30,
    "1h": 60,
    "2h": 120,
    "4h": 240,
    "8h": 480,
    "12h": 720,
    "24h": 1440,
}
_BY_MINUTES = {minutes: key for key, minutes in _MINUTES.items()}
_ALIASES = {"hourly": 60, "daily": 1440, "halfhourly": 30, "halfhour": 30}
_UNITS = (
    (("", "m", "min", "mins", "minute", "minutes"), 1),
    (("h", "hr", "hrs", "hour", "hours"), 60),
    (("d", "day", "days"), 1440),
)


def normalize_interval(raw: object) -> str | None:
    """Fold a user's interval to one canonical key, or ``None`` if unsupported.

    Tolerant of how the value is written but not of which cadences exist. Word
    aliases (``"hourly"``, ``"daily"``), spacing, and case all normalize, and any
    unit that resolves to a supported number of minutes is accepted — ``"60m"`` and
    ``"1 hour"`` are both ``"1h"``. Anything landing between the supported cadences
    is rejected rather than rounded, because the result becomes a systemd
    ``OnCalendar`` expression that must divide the clock evenly.
    """
    token = fold_token(raw)
    if token is None:
        return None
    alias = alias_form(token)
    if alias in _ALIASES:
        return _BY_MINUTES.get(_ALIASES[alias])
    match = re.fullmatch(r"(\d+)([a-z]*)", token)
    if not match:
        return None
    quantity, unit = int(match.group(1)), match.group(2)
    for names, multiplier in _UNITS:
        if unit in names:
            return _BY_MINUTES.get(quantity * multiplier)
    return None


def oncalendar_for(canonical: str) -> str:
    """Translate one already-canonical interval into its systemd ``OnCalendar`` value.

    Raises:
        KeyError: The key is not canonical. Deliberately unguarded: callers pass a
            value that settings resolution already validated, so a miss is a
            framework bug rather than bad user input.
    """
    return SUPPORTED_INTERVALS[canonical]
