"""Import-light descriptor, item fields, and setting for Insomnia listings."""

import math
from urllib.parse import SplitResult

from core.scrapers.api import ItemField, ScraperPlugin, SettingSpec, UrlField


def decode_string_tuple(raw: object) -> tuple[str, ...]:
    """Decode a JSON array of strings into a canonical tuple, dropping blanks.

    A worked example of the item-field codec contract: raise ``ValueError`` for
    anything unusable, and return a value already in canonical form — immutable and
    stripped — so the declared default ``()`` satisfies ``decode(default) ==
    default``.
    """
    if not isinstance(raw, (list, tuple)):
        raise ValueError("must be an array of strings")
    if any(not isinstance(value, str) for value in raw):
        raise ValueError("must contain only strings")
    return tuple(value.strip() for value in raw if value.strip())


TITLE_INCLUDE = ItemField[tuple[str, ...]](
    key="title_include",
    decode=decode_string_tuple,
    default=(),
)
TITLE_EXCLUDE = ItemField[tuple[str, ...]](
    key="title_exclude",
    decode=decode_string_tuple,
    default=(),
)


def decode_min_advert_price(raw: object) -> float:
    """Decode a non-negative price floor from a number or a euro-suffixed string.

    Tolerant about how the value is written because it is typed by hand into a
    config, and strict about what it means: negative, infinite, and boolean values
    are rejected rather than coerced.
    """
    if isinstance(raw, bool):
        raise ValueError("must be a non-negative number")
    if isinstance(raw, str):
        try:
            value = float(raw.replace("€", "").strip())
        except ValueError as exc:
            raise ValueError("must be a non-negative number") from exc
    elif isinstance(raw, (int, float)):
        value = float(raw)
    else:
        raise ValueError("must be a non-negative number")
    if not math.isfinite(value) or value < 0:
        raise ValueError("must be a finite non-negative number")
    return value


MIN_ADVERT_PRICE = SettingSpec[float](
    key="min_advert_price",
    default=0.0,
    decode=decode_min_advert_price,
    display=lambda value: f"{value:g} €" if value else "disabled",
)


def is_classifieds_url(url: SplitResult) -> bool:
    """Accept any Insomnia classifieds listing page.

    Broader than the Skroutz predicate because this plugin scrapes whole listing
    pages rather than one identified resource, so every path below
    ``/classifieds/`` is usable.
    """
    return url.path.startswith("/classifieds/")


URL = UrlField(
    key="url",
    domains=("insomnia.gr",),
    accepts_url=is_classifieds_url,
)


PLUGIN = ScraperPlugin(
    display_name="Insomnia",
    item_fields=(URL, TITLE_INCLUDE, TITLE_EXCLUDE),
    settings=(MIN_ADVERT_PRICE,),
    default_interval="1h",
    reference_url=URL,
)
