"""Tolerant normalizers for the retention and boolean settings (stdlib only)."""

import re


# Log retention: how many daily log files each scraper keeps (the rotating file
# handler's backupCount). A user value is an integer day-count or a day-duration
# string ("4", "4d", "4 days"); only days are supported (no hours/weeks/months).
DEFAULT_LOG_RETENTION_DAYS = 7
MIN_LOG_RETENTION_DAYS = 1
MAX_LOG_RETENTION_DAYS = 30


def normalize_retention_days(raw: object) -> int | None:
    """Validates a log-retention value to a day-count in 1-30, or ``None``.

    Accepts a JSON integer (``7``) or a day-duration string - ``"4"``, ``"4d"``,
    ``"4 d"``, ``"4day"``, ``"4 days"`` (case-insensitive, whitespace-tolerant); a
    bare number is read as days. Only days are supported. Returns the day count when
    it is within 1-30, otherwise ``None`` - so ``0``, an out-of-range number, a
    non-day unit (``"4h"``, ``"4 months"``), a float, a bool, or junk are rejected.

    Args:
        raw: The user's raw ``log_retention_days`` value (any type).

    Returns:
        int | None: The day count in 1-30, or ``None`` if unsupported.
    """
    # bool is a subclass of int; reject it explicitly (True/False are not day counts).
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        value = raw
    elif isinstance(raw, str):
        token = re.sub(r"\s+", "", raw).lower()
        match = re.fullmatch(r"(\d+)(d|day|days)?", token)
        if not match:
            return None
        value = int(match.group(1))
    else:
        return None
    if MIN_LOG_RETENTION_DAYS <= value <= MAX_LOG_RETENTION_DAYS:
        return value
    return None


# Boolean spellings accepted for a flag setting (e.g. ``notify_scraping_errors``).
# Tokens are whitespace-free and lowercase; a raw value is folded to that form first.
_TRUE_TOKENS = frozenset({"true", "yes", "on", "1"})
_FALSE_TOKENS = frozenset({"false", "no", "off", "0"})


def normalize_bool(raw: object) -> bool | None:
    """Normalizes a boolean setting value to ``True``/``False``, or ``None``.

    Tolerant like the other normalizers, and deliberately *not* a bare ``bool(...)``
    cast - ``bool("false")`` is ``True``, which would be a footgun. Accepts a real JSON
    boolean, the ints ``1``/``0``, and the string spellings ``true/yes/on/1`` and
    ``false/no/off/0`` (case- and whitespace-insensitive). Anything else - a typo, an
    unsupported word, a float - returns ``None`` so the caller can default + flag it.

    Args:
        raw: The user's raw flag value (any type).

    Returns:
        bool | None: ``True``/``False`` for a recognized value, or ``None``.
    """
    # bool is a subclass of int, so handle it before the int branch.
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int):
        if raw == 1:
            return True
        if raw == 0:
            return False
        return None
    if isinstance(raw, str):
        token = raw.strip().lower()
        if token in _TRUE_TOKENS:
            return True
        if token in _FALSE_TOKENS:
            return False
    return None
