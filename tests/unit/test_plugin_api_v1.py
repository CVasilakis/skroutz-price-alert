from datetime import datetime, timezone

import pytest

from core.exceptions import InvalidScrapeResultError
from core.scrapers.api import (
    ItemField, ListingResult, Offer, PriceResult, SettingSpec, TrackedItem,
    validate_scrape_result,
)
from core.settings import SettingStatus, resolve_settings


def test_typed_item_and_setting_lookup_uses_declaration_objects():
    field = ItemField("tags", lambda raw: tuple(raw), ())
    other = ItemField("tags", lambda raw: tuple(raw), ())
    item = TrackedItem("one", "One", "https://example.com/p?q=1", 0, _custom={field: ("x",)})
    assert item[field] == ("x",)
    with pytest.raises(KeyError):
        _ = item[other]

    spec = SettingSpec("limit", "Limit", lambda raw: int(raw), str, "bad", 2)
    resolved = resolve_settings((spec,), {"limit": "4"})
    assert resolved[spec] == 4
    assert resolved.status(spec) is SettingStatus.OK


@pytest.mark.parametrize("price", [True, -1, float("nan"), float("inf")])
def test_result_construction_rejects_invalid_prices(price):
    with pytest.raises(InvalidScrapeResultError):
        PriceResult(price, "EUR")


def test_listing_snapshots_iterable_and_normalizes_offer_url():
    offers = (Offer(" Deal ", 1, "https://example.com/a?q=1#fragment") for _ in range(1))
    result = ListingResult(" EUR ", offers)
    assert isinstance(result.offers, tuple)
    assert tuple(result.offers)[0].url == "https://example.com/a?q=1"
    assert result.currency == "EUR"
    assert validate_scrape_result(result) is result


def test_result_boundary_rejects_untyped_plugin_return():
    with pytest.raises(InvalidScrapeResultError):
        validate_scrape_result({"price": 1})
