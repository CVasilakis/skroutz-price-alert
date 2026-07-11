"""The insomnia tracked-item model and the row-identity helpers it shares with storage.

An insomnia row is a *search*, not a single product: a classifieds listing URL
plus title filter terms. That shape has two consequences the helpers below
solve in one place:

* ``scrape_product(url)`` receives only a URL, so the filter terms travel to the
  client encoded as query parameters on the item's URL (the "virtual search
  URL" built by :func:`build_search_url` and decoded by :func:`split_search_url`).
* Several rows legitimately share one listing URL, differing only in their
  terms, so a row's identity for dedup and write-back merging is the listing
  URL *plus* its terms (:func:`search_row_key`) — computed identically from a
  stored row dict (storage side) and from a virtual URL (update side) so the
  two can never drift.
"""

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

from core.scrapers.base.model import BaseTrackedItem

# The query-parameter names carrying the filter terms on a virtual search URL.
# They mirror the config field names so a virtual URL is self-explanatory.
INCLUDE_PARAM = "title_include"
EXCLUDE_PARAM = "title_exclude"


def parse_terms(raw: object) -> list[str]:
    """Reads one filter-terms config field into a clean term list.

    Forgiving read-side parsing (validation lives in ``is_valid_terms_field``):
    a missing or non-list value yields no terms, and only non-empty string
    entries survive, stripped of surrounding whitespace.

    Args:
        raw: The raw config value of ``title_include``/``title_exclude``.

    Returns:
        list[str]: The usable filter terms, possibly empty.
    """
    if not isinstance(raw, list):
        return []
    return [term.strip() for term in raw if isinstance(term, str) and term.strip()]


def is_valid_terms_field(raw: object) -> bool:
    """Validates one filter-terms config field for the config-health check.

    Args:
        raw: The raw config value of ``title_include``/``title_exclude``.

    Returns:
        bool: True when the field is absent or a list of strings.
    """
    if raw is None:
        return True
    return isinstance(raw, list) and all(isinstance(term, str) for term in raw)


def build_search_url(url: str, include: list[str], exclude: list[str]) -> str:
    """Builds the virtual search URL carrying a row's filter terms.

    Appends the terms as query parameters so the single-URL scrape contract can
    transport them to the client, and so each row's item URL is unique among
    rows sharing one listing URL. The result still loads the listing page in a
    browser (the site ignores unknown parameters).

    Args:
        url (str): The row's listing URL.
        include (list[str]): The must-match title terms.
        exclude (list[str]): The must-not-match title terms.

    Returns:
        str: The virtual search URL (the input URL unchanged when there are no terms).
    """
    if not (include or exclude):
        return url
    params = [(INCLUDE_PARAM, term) for term in include] + [(EXCLUDE_PARAM, term) for term in exclude]
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}{urlencode(params)}"


def split_search_url(url: str) -> tuple[str, list[str], list[str]]:
    """Splits a virtual search URL back into the bare listing URL and its terms.

    The inverse of :func:`build_search_url`. Query parameters that are not
    filter terms are preserved on the returned listing URL.

    Args:
        url (str): The virtual search URL (an item's ``url``).

    Returns:
        tuple[str, list[str], list[str]]: ``(listing_url, include, exclude)``.
    """
    parts = urlsplit(url)
    include: list[str] = []
    exclude: list[str] = []
    remaining: list[tuple[str, str]] = []
    for key, value in parse_qsl(parts.query):
        if key == INCLUDE_PARAM:
            include.append(value)
        elif key == EXCLUDE_PARAM:
            exclude.append(value)
        else:
            remaining.append((key, value))
    listing = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(remaining), ""))
    return listing, include, exclude


def search_row_key(url: str, include: list[str], exclude: list[str]) -> str:
    """Returns the canonical identity of a search row.

    The clean listing URL (query and fragment stripped, mirroring the base
    manager's URL cleaning) plus the normalized terms — case-folded and sorted,
    so reordering or re-casing terms does not change a row's identity, while
    genuinely different searches on the same listing stay distinct.

    Args:
        url (str): The row's listing URL (extra query parameters are ignored).
        include (list[str]): The must-match title terms.
        exclude (list[str]): The must-not-match title terms.

    Returns:
        str: The canonical row key.
    """
    parts = urlsplit(url)
    base = f"{parts.scheme}://{parts.netloc}{parts.path}"

    def canonical(terms: list[str]) -> str:
        return "|".join(sorted(term.casefold().strip() for term in terms))

    return f"{base}::include={canonical(include)}::exclude={canonical(exclude)}"


@dataclass
class AdvertSearch(BaseTrackedItem):
    """One tracked insomnia classifieds search.

    On top of the base fields, a search carries the title filter terms: an
    advert matches when its title contains **all** ``title_include`` terms and
    **none** of the ``title_exclude`` terms (case-insensitive substring match).
    Both lists are optional — an empty search matches every advert on the page.

    Note:
        ``url`` holds the *virtual search URL* (listing URL + terms as query
        parameters, built in ``from_dict``), not the raw stored URL — see the
        module docstring for why.
    """
    title_include: list[str] = field(default_factory=list)
    title_exclude: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AdvertSearch":
        """Creates a search from a stored row, composing the base parsing.

        Args:
            data (dict[str, Any]): The raw config row.

        Returns:
            AdvertSearch: The parsed search, its ``url`` upgraded to the
            virtual search URL carrying the row's filter terms.
        """
        base = cls._base_field_kwargs(data)
        include = parse_terms(data.get(INCLUDE_PARAM))
        exclude = parse_terms(data.get(EXCLUDE_PARAM))
        base["url"] = build_search_url(base["url"], include, exclude)
        return cls(**base, title_include=include, title_exclude=exclude)
