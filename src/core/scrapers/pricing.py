"""Reusable price parsing for plugin clients."""

from __future__ import annotations

import math
import re


def parse_price(raw_value: object) -> float | None:
    """Parse finite numeric prices with European or US separator conventions."""
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
        fraction = re.sub(r"[.,]", "", cleaned[decimal_pos + 1:])
        number = f"{integer}.{fraction}"
    try:
        value = float(f"{sign}{number}")
    except (ValueError, OverflowError):
        return None
    return value if math.isfinite(value) else None


__all__ = ["parse_price"]
