"""Reusable price parsing for plugin clients.

Opt-in and store-independent, like the rest of ``core.scrapers.support``. It
imports only the standard library, so a plugin may use it without declaring any
private dependency.
"""

from __future__ import annotations

import math
import re


def parse_price(raw_value: object) -> float | None:
    """Parse finite numeric prices with European or US separator conventions.

    Accepts what a scraped payload actually contains: a JSON number, or a string
    with a currency symbol, spaces, and grouping separators (``"1.234,56 €"``,
    ``"$1,234.56"``). Every character other than digits, ``.``, ``,``, and ``-``
    is discarded first.

    Both conventions are supported by one rule: the **last** ``.`` or ``,`` is
    the decimal separator and all earlier ones are grouping. This is exact for
    values that carry a decimal part, and it is the deliberate tradeoff for those
    that do not -- an unsuffixed ``"1,234"`` is genuinely ambiguous and is read
    as ``1.234``. Prefer a numeric field from the payload when one exists.

    Args:
        raw_value: The scraped value, of any type.

    Returns:
        The parsed price, or ``None`` when the value is missing, of an
        unsupported type, contains no digits, or is not finite. ``None`` is a
        normal outcome, not an error: the caller decides whether it means
        :class:`~core.scrapers.api.PriceUnavailableError` (no price offered) or
        :class:`~core.scrapers.api.ScraperParseError` (a price was present but
        unreadable).

    Note:
        A leading sign is preserved, so a negative input parses to a negative
        number. Result types reject negative prices, so screen for that before
        constructing one.
    """
    if raw_value is None or isinstance(raw_value, bool):
        return None
    if isinstance(raw_value, (int, float)):
        try:
            value = float(raw_value)
        except OverflowError:
            return None
        return value if math.isfinite(value) else None
    if not isinstance(raw_value, str):
        return None
    cleaned = re.sub(r"[^\d.,-]", "", raw_value)
    sign = "-" if cleaned.startswith("-") else ""
    cleaned = cleaned.replace("-", "")
    if not cleaned:
        return None
    decimal_pos = max(cleaned.rfind("."), cleaned.rfind(","))
    if decimal_pos == -1:
        number = cleaned
    else:
        integer = re.sub(r"[.,]", "", cleaned[:decimal_pos])
        fraction = re.sub(r"[.,]", "", cleaned[decimal_pos + 1 :])
        number = f"{integer}.{fraction}"
    try:
        value = float(f"{sign}{number}")
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


__all__ = ["parse_price"]
