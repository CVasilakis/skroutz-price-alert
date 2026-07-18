"""Tracked search model and canonical identity for Insomnia listings."""

from dataclasses import dataclass, field
from typing import Any

from core.scrapers.base.model import BaseTrackedItem
from core.scrapers.base.url import clean_url

INCLUDE_PARAM = "title_include"
EXCLUDE_PARAM = "title_exclude"


def parse_terms(raw: object) -> list[str]:
    """Return stripped, nonblank filter terms from a stored field."""
    if not isinstance(raw, list):
        return []
    return [term.strip() for term in raw if isinstance(term, str) and term.strip()]


def is_valid_terms_field(raw: object) -> bool:
    """Whether an optional filter field is a list containing only strings."""
    return raw is None or isinstance(raw, list) and all(isinstance(term, str) for term in raw)


def search_row_key(url: str, include: list[str], exclude: list[str]) -> str:
    """Return a case/order-insensitive identity for one listing search."""
    def canonical(terms: list[str]) -> str:
        return "|".join(sorted(term.casefold().strip() for term in terms))

    return (
        f"{clean_url(url)}::include={canonical(include)}"
        f"::exclude={canonical(exclude)}"
    )


@dataclass
class AdvertSearch(BaseTrackedItem):
    """One listing URL plus title filters used to select independent offers."""

    title_include: list[str] = field(default_factory=list)
    title_exclude: list[str] = field(default_factory=list)

    @classmethod
    def parse_extra_fields(cls, data: dict[str, Any]) -> dict[str, Any]:
        return {
            "title_include": parse_terms(data.get(INCLUDE_PARAM)),
            "title_exclude": parse_terms(data.get(EXCLUDE_PARAM)),
        }

    def identity_key(self) -> str:
        return search_row_key(self.url, self.title_include, self.title_exclude)
