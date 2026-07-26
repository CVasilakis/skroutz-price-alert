import pytest

from core.exceptions import InvalidScrapeResultError
from core.presentation import resolved_setting_views
from core.scrapers.api import (
    ItemField,
    ListingResult,
    Offer,
    PriceResult,
    SettingSpec,
    TrackedItem,
    validate_scrape_result,
)
from core.settings import SettingStatus, resolve_settings


def test_typed_item_and_setting_lookup_uses_declaration_objects():
    field = ItemField("tags", lambda raw: tuple(raw), default=())
    other = ItemField("tags", lambda raw: tuple(raw), default=())
    item = TrackedItem("one", "One", 0, _custom={field: ("x",)})
    assert item[field] == ("x",)
    with pytest.raises(KeyError):
        _ = item[other]

    spec = SettingSpec("limit", lambda raw: int(raw), default=2)
    resolved = resolve_settings((spec,), {"limit": "4"})
    assert resolved[spec] == 4
    assert resolved.status(spec) is SettingStatus.OK


def test_missing_and_present_empty_settings_have_distinct_statuses():
    spec = SettingSpec("limit", int, default=2)
    assert resolve_settings((spec,), None).status(spec) is SettingStatus.NO_CONFIG
    assert resolve_settings((spec,), {}).status(spec) is SettingStatus.DEFAULT


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


def test_required_and_sensitive_setting_never_exposes_raw_value():
    token = SettingSpec("api_token", str, sensitive=True)
    missing = resolve_settings((token,), {})
    assert missing.status(token) is SettingStatus.MISSING
    assert resolved_setting_views(missing)[0].display_value == "not configured"
    with pytest.raises(RuntimeError, match="was not resolved"):
        _ = missing[token]

    configured = resolve_settings((token,), {"api_token": "super-secret"})
    assert configured[token] == "super-secret"
    view = resolved_setting_views(configured)[0]
    assert view.display_value == "configured"
    assert "super-secret" not in repr(view)


def test_price_result_normalizes_optional_url_and_rejects_unsafe_url():
    result = PriceResult(1, "EUR", "https://example.com/p#fragment")
    assert result.url == "https://example.com/p"
    with pytest.raises(InvalidScrapeResultError, match="result URL"):
        PriceResult(1, "EUR", "relative")
