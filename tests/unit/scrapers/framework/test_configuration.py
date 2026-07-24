import errno
import json
from datetime import datetime, timezone
from unittest import mock

import pytest
from support import catalog_sandbox, fake_plugin

from core.exceptions import ConfigFileError, StateFileError
from core.infrastructure.persistence import format_utc, parse_utc
from core.scrapers.api import ItemField, ScraperPlugin, SettingSpec, UrlField
from core.scrapers.framework.compiler import compile_plugin
from core.scrapers.framework.configuration import TargetConfigLoader
from core.scrapers.framework.state import JsonStateRepository, StateEntry
from core.settings import SettingsValidationError, SettingsValidationProblem


@pytest.fixture
def plugin():
    definition = fake_plugin(
        accepts_url=lambda url: url.path.startswith("/products/"),
    )
    with catalog_sandbox(definition) as catalog:
        yield catalog.get("fakestore")


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _target_document(items):
    return {"settings": {}, "items": items}


def _row(**updates):
    row = {
        "id": "phone",
        "name": "Phone",
        "url": "https://fake-store.example/products/phone?variant=blue#reviews",
        "target_price": 0,
        "skip": False,
    }
    row.update(updates)
    return row


def test_loader_keeps_query_removes_fragment_and_reports_bad_rows(tmp_path, plugin):
    _write(
        tmp_path / "config" / "fakestore.json",
        _target_document(
            [
                _row(),
                _row(id="bad", mystery=True),
                _row(id="phone"),
            ]
        ),
    )
    loaded = TargetConfigLoader(plugin, str(tmp_path / "config")).load()
    assert [item.id for item in loaded.items] == ["phone"]
    assert loaded.items[0][plugin.reference_url].endswith("?variant=blue")
    assert [issue.index for issue in loaded.row_issues] == [2, 3]


def test_invalid_row_still_reserves_its_explicit_id(tmp_path, plugin):
    _write(
        tmp_path / "config" / "fakestore.json",
        _target_document(
            [
                _row(name=" "),
                _row(name="Valid duplicate"),
            ]
        ),
    )
    loaded = TargetConfigLoader(plugin, str(tmp_path / "config")).load()
    assert loaded.items == ()
    assert [issue.index for issue in loaded.row_issues] == [1, 2]
    assert "duplicate item id" in loaded.row_issues[1].message


@pytest.mark.parametrize(
    "extra",
    [{"future": 1}, {"products": []}, {"schema_version": 1}],
)
def test_unknown_top_level_keys_fail_closed(tmp_path, extra, plugin):
    document = _target_document([]) | extra
    _write(tmp_path / "config" / "fakestore.json", document)
    with pytest.raises(ConfigFileError):
        TargetConfigLoader(plugin, str(tmp_path / "config")).load()


def test_state_missing_is_healthy_and_round_trips_aware_utc(tmp_path, plugin):
    repo = JsonStateRepository(tmp_path / "state" / "x.json")
    repo.load()
    _write(tmp_path / "config" / "fakestore.json", _target_document([_row()]))
    item = TargetConfigLoader(plugin, str(tmp_path / "config")).load().items[0]
    now = datetime(2026, 7, 18, 18, 30, tzinfo=timezone.utc)
    repo.record_priced_check(item.id, 190, now)
    repo.save()
    second = JsonStateRepository(tmp_path / "state" / "x.json")
    second.load()
    assert second.get("phone") == StateEntry(190.0, now)
    assert format_utc(now) == "2026-07-18T18:30:00Z"
    assert parse_utc(format_utc(now)) == now


def test_schema_v2_no_price_check_preserves_historical_price_and_clears_alerts(tmp_path):
    path = tmp_path / "state" / "x.json"
    _write(
        path,
        {
            "schema_version": 2,
            "items": {
                "phone": {
                    "last_price": 190.0,
                    "last_checked": "2026-07-17T18:30:00Z",
                    "notified_offer_urls": ["https://example.com/offer"],
                }
            },
        },
    )
    repo = JsonStateRepository(path)
    repo.load()
    checked = datetime(2026, 7, 18, 18, 30, tzinfo=timezone.utc)
    repo.record_no_price_check("phone", checked)
    repo.save()
    reloaded = JsonStateRepository(path)
    reloaded.load()
    assert reloaded.get("phone") == StateEntry(190.0, checked)


def test_malformed_existing_state_is_not_overwritten(tmp_path):
    path = tmp_path / "state" / "x.json"
    _write(path, {"schema_version": 1, "items": []})
    original = path.read_bytes()
    repo = JsonStateRepository(path, display_path="state/x.json")
    with pytest.raises(StateFileError) as caught:
        repo.load()
    assert str(caught.value) == ("Fix invalid state in `state/x.json`; details are logged.")
    assert "schema_version must be 2" in (caught.value.diagnostic_detail or "")
    assert str(path.resolve()) in (caught.value.diagnostic_detail or "")
    assert path.read_bytes() == original


def test_state_read_and_save_permission_failures_are_concise(tmp_path):
    path = tmp_path / "state" / "x.json"
    repo = JsonStateRepository(path, display_path="state/x.json")
    with mock.patch.object(
        path.__class__,
        "open",
        side_effect=PermissionError(errno.EACCES, "denied"),
    ):
        with pytest.raises(StateFileError) as read_failure:
            repo.load()
    assert str(read_failure.value) == ("Cannot read `state/x.json`; check its permissions.")
    assert "Errno: 13" in (read_failure.value.diagnostic_detail or "")

    repo.load()
    repo.record_priced_check("x", 1, datetime.now(timezone.utc))
    with mock.patch(
        "core.scrapers.framework.state.write_json_atomically",
        side_effect=PermissionError(errno.EACCES, "denied"),
    ):
        with pytest.raises(StateFileError) as save_failure:
            repo.save()
    assert str(save_failure.value) == ("Cannot save `state/x.json`; check its permissions.")
    assert "Errno: 13" in (save_failure.value.diagnostic_detail or "")


@pytest.mark.parametrize(
    "document, display_message, diagnostic_message",
    [
        ([], "must contain a JSON object", "expected object"),
        (
            {"schema_version": 1, "settings": {}, "items": []},
            "Remove unsupported keys",
            "unknown top-level keys: schema_version",
        ),
        ({"settings": [], "items": []}, "`settings`", "settings must be an object"),
        ({"settings": {}, "items": {}}, "`items`", "items is dict"),
        (
            {"settings": {}, "items": [], "metadata": {}},
            "Remove unsupported keys",
            "unknown top-level keys: metadata",
        ),
        (
            {"settings": {"typo": 1}, "items": []},
            "Remove unsupported settings",
            "unknown settings: typo",
        ),
    ],
)
def test_strict_document_shapes(tmp_path, document, display_message, diagnostic_message, plugin):
    _write(tmp_path / "config" / "fakestore.json", document)
    with pytest.raises(ConfigFileError) as caught:
        TargetConfigLoader(plugin, str(tmp_path / "config")).load()
    assert display_message in str(caught.value)
    assert str(caught.value).count("config/fakestore.json") == 1
    assert diagnostic_message in (caught.value.diagnostic_detail or "")
    assert str((tmp_path / "config" / "fakestore.json").resolve()) in (
        caught.value.diagnostic_detail or ""
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"name": " "},
        {"url": "relative"},
        {"url": "https://example.com/s/1/x"},
        {"url": "https://fake-store.example/search?q=x"},
        {"target_price": True},
        {"target_price": -1},
        {"skip": "no"},
        {"metadata": {}},
    ],
)
def test_invalid_rows_are_structured_and_never_loaded(tmp_path, changes, plugin):
    _write(tmp_path / "config" / "fakestore.json", _target_document([_row(**changes)]))
    loaded = TargetConfigLoader(plugin, str(tmp_path / "config")).load()
    assert loaded.items == ()
    assert loaded.row_issues[0].index == 1


@pytest.mark.parametrize(
    "document",
    [
        [],
        {"schema_version": 1, "items": {}},
        {"schema_version": 2, "items": {}, "extra": 1},
        {"schema_version": 2, "items": []},
        {"schema_version": 2, "items": {"x": []}},
        {"schema_version": 2, "items": {"x": {"last_price": True}}},
        {"schema_version": 2, "items": {"x": {"last_checked": "yesterday"}}},
        {"schema_version": 2, "items": {"x": {"price_alert_delivered": 1}}},
        {"schema_version": 2, "items": {"x": {"notified_offer_urls": "bad"}}},
        {
            "schema_version": 2,
            "items": {"x": {"notified_offer_urls": ["https://example.com/a#fragment"]}},
        },
        {
            "schema_version": 2,
            "items": {
                "x": {
                    "price_alert_delivered": True,
                    "notified_offer_urls": ["https://example.com/a"],
                }
            },
        },
    ],
)
def test_state_rejects_every_malformed_shape(tmp_path, document):
    path = tmp_path / "state" / "x.json"
    _write(path, document)
    with pytest.raises(StateFileError):
        JsonStateRepository(path).load()


def test_state_noop_and_save_failure_are_explicit(tmp_path):
    repo = JsonStateRepository(
        tmp_path / "state" / "x.json",
        display_path="state/x.json",
    )
    repo.load()
    assert not repo.has_pending
    repo.record_priced_check("x", 1, datetime.now(timezone.utc))
    with mock.patch(
        "core.scrapers.framework.state.write_json_atomically", side_effect=OSError("disk full")
    ):
        with pytest.raises(StateFileError) as caught:
            repo.save()
    assert str(caught.value) == "Cannot save `state/x.json`; check the error log."
    assert "OSError" in (caught.value.diagnostic_detail or "")
    assert "disk full" in (caught.value.diagnostic_detail or "")


def test_schema_v2_round_trips_alert_delivery_history(tmp_path):
    path = tmp_path / "state" / "x.json"
    now = datetime(2026, 7, 18, 18, 30, tzinfo=timezone.utc)
    repo = JsonStateRepository(path)
    repo.load()
    repo.record_priced_check("product", 5, now, price_alert_delivered=True)
    repo.record_priced_check(
        "listing",
        6,
        now,
        notified_offer_urls=("https://example.com/a", "https://example.com/b?q=1"),
    )
    repo.save()

    saved = json.loads(path.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2
    reloaded = JsonStateRepository(path)
    reloaded.load()
    assert reloaded.get("product").price_alert_delivered is True
    assert reloaded.get("listing").notified_offer_urls == (
        "https://example.com/a",
        "https://example.com/b?q=1",
    )


def test_url_free_required_fields_and_required_settings(tmp_path):
    sku = ItemField("sku", lambda raw: str(raw).strip())
    token = SettingSpec("api_token", lambda raw: str(raw).strip(), sensitive=True)
    plugin = compile_plugin(
        ScraperPlugin(
            display_name="Identifier Store",
            item_fields=(sku,),
            settings=(token,),
        ),
        target="identifier_store",
        package="tests.identifier_store",
    )
    path = tmp_path / "config" / "identifier_store.json"
    _write(
        path,
        {
            "settings": {},
            "items": [{"id": "one", "name": "One", "target_price": 1, "sku": "A"}],
        },
    )
    with pytest.raises(ConfigFileError) as caught:
        TargetConfigLoader(plugin, str(path.parent)).load()
    assert str(caught.value) == ("Fix required settings in `config/identifier_store.json`.")
    assert "api_token" in (caught.value.diagnostic_detail or "")
    assert isinstance(caught.value.__cause__, SettingsValidationError)
    assert caught.value.__cause__.problem is SettingsValidationProblem.REQUIRED

    _write(
        path,
        {
            "settings": {"api_token": "secret"},
            "items": [
                {"id": "one", "name": "One", "target_price": 1, "sku": " A "},
                {"id": "two", "name": "Two", "target_price": 1},
            ],
        },
    )
    loaded = TargetConfigLoader(plugin, str(path.parent)).load()
    assert loaded.items[0][sku] == "A"
    assert loaded.row_issues[0].message == "sku is required"
    assert loaded.settings[token] == "secret"
    assert loaded.settings.views()[-1].display_value == "configured"


def test_multiple_url_fields_are_validated_independently(tmp_path):
    product = UrlField(
        "product_url",
        domains=("products.example",),
        accepts_url=lambda url: url.path.startswith("/p/"),
    )
    seller = UrlField(
        "seller_url",
        domains=("sellers.example",),
        accepts_url=lambda url: url.path.startswith("/shop/"),
    )
    plugin = compile_plugin(
        ScraperPlugin(
            display_name="Marketplace",
            item_fields=(product, seller),
            reference_url=product,
        ),
        target="marketplace",
        package="tests.marketplace",
    )
    path = tmp_path / "config" / "marketplace.json"
    _write(
        path,
        {
            "settings": {},
            "items": [
                {
                    "id": "one",
                    "name": "One",
                    "target_price": 1,
                    "product_url": "https://products.example/p/1#details",
                    "seller_url": "https://sellers.example/shop/acme?q=1",
                }
            ],
        },
    )
    item = TargetConfigLoader(plugin, str(path.parent)).load().items[0]
    assert item[product] == "https://products.example/p/1"
    assert item[seller].endswith("/shop/acme?q=1")
