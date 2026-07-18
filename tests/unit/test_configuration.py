import json
from datetime import datetime, timezone
from unittest import mock

import pytest

from core.exceptions import ConfigFileError, StateFileError
from core.persistence import format_utc, parse_utc
from core.scrapers.configuration import TargetConfigLoader
from core.scrapers.registry import PluginCatalog
from core.scrapers.state import JsonStateRepository, StateEntry

CATALOG = PluginCatalog.discover()


def _write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _skroutz_document(items):
    return {"settings": {}, "items": items}


def _row(**updates):
    row = {
        "id": "phone", "name": "Phone",
        "url": "https://www.skroutz.gr/s/1/Phone.html?variant=blue#reviews",
        "target_price": 0, "skip": False,
    }
    row.update(updates)
    return row


def test_loader_keeps_query_removes_fragment_and_reports_bad_rows(tmp_path):
    plugin = CATALOG.get("skroutz")
    _write(tmp_path / "config" / "skroutz.json", _skroutz_document([
        _row(), _row(id="bad", mystery=True), _row(id="phone"),
    ]))
    loaded = TargetConfigLoader(plugin, str(tmp_path / "config")).load()
    assert [item.id for item in loaded.items] == ["phone"]
    assert loaded.items[0].url.endswith("?variant=blue")
    assert [issue.index for issue in loaded.row_issues] == [2, 3]


def test_invalid_row_still_reserves_its_explicit_id(tmp_path):
    plugin = CATALOG.get("skroutz")
    _write(tmp_path / "config" / "skroutz.json", _skroutz_document([
        _row(name=" "), _row(name="Valid duplicate"),
    ]))
    loaded = TargetConfigLoader(plugin, str(tmp_path / "config")).load()
    assert loaded.items == ()
    assert [issue.index for issue in loaded.row_issues] == [1, 2]
    assert "duplicate item id" in loaded.row_issues[1].message


@pytest.mark.parametrize(
    "extra", [{"future": 1}, {"products": []}, {"schema_version": 1}],
)
def test_unknown_top_level_keys_fail_closed(tmp_path, extra):
    plugin = CATALOG.get("skroutz")
    document = _skroutz_document([]) | extra
    _write(tmp_path / "config" / "skroutz.json", document)
    with pytest.raises(ConfigFileError):
        TargetConfigLoader(plugin, str(tmp_path / "config")).load()


def test_state_missing_is_healthy_and_round_trips_aware_utc(tmp_path):
    repo = JsonStateRepository(tmp_path / "state" / "x.json")
    repo.load()
    plugin = CATALOG.get("skroutz")
    _write(tmp_path / "config" / "skroutz.json", _skroutz_document([_row()]))
    item = TargetConfigLoader(plugin, str(tmp_path / "config")).load().items[0]
    now = datetime(2026, 7, 18, 18, 30, tzinfo=timezone.utc)
    repo.record_priced_check(item.id, 190, now)
    repo.save()
    second = JsonStateRepository(tmp_path / "state" / "x.json")
    second.load()
    assert second.get("phone") == StateEntry(190.0, now)
    assert format_utc(now) == "2026-07-18T18:30:00Z"
    assert parse_utc(format_utc(now)) == now


def test_schema_v1_no_price_check_preserves_historical_price(tmp_path):
    path = tmp_path / "state" / "x.json"
    _write(path, {
        "schema_version": 1,
        "items": {"phone": {
            "last_price": 190.0,
            "last_checked": "2026-07-17T18:30:00Z",
        }},
    })
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
    repo = JsonStateRepository(path)
    with pytest.raises(StateFileError):
        repo.load()
    assert path.read_bytes() == original


@pytest.mark.parametrize("document, message", [
    ([], "contain an object"),
    ({"schema_version": 1, "settings": {}, "items": []}, "unknown top-level"),
    ({"settings": [], "items": []}, "settings"),
    ({"settings": {}, "items": {}}, "items"),
    ({"settings": {}, "items": [], "metadata": []}, "metadata"),
    ({"settings": {"typo": 1}, "items": []}, "unknown settings"),
])
def test_strict_document_shapes(tmp_path, document, message):
    plugin = CATALOG.get("skroutz")
    _write(tmp_path / "config" / "skroutz.json", document)
    with pytest.raises(ConfigFileError, match=message):
        TargetConfigLoader(plugin, str(tmp_path / "config")).load()


@pytest.mark.parametrize("changes", [
    {"name": " "}, {"url": "relative"},
    {"url": "https://example.com/s/1/x"},
    {"url": "https://www.skroutz.gr/search?q=x"},
    {"target_price": True}, {"target_price": -1}, {"skip": "no"}, {"metadata": []},
])
def test_invalid_rows_are_structured_and_never_loaded(tmp_path, changes):
    plugin = CATALOG.get("skroutz")
    _write(tmp_path / "config" / "skroutz.json", _skroutz_document([_row(**changes)]))
    loaded = TargetConfigLoader(plugin, str(tmp_path / "config")).load()
    assert loaded.items == ()
    assert loaded.row_issues[0].index == 1


@pytest.mark.parametrize("document", [
    [], {"schema_version": 2, "items": {}},
    {"schema_version": 1, "items": {}, "extra": 1},
    {"schema_version": 1, "items": []},
    {"schema_version": 1, "items": {"x": []}},
    {"schema_version": 1, "items": {"x": {"last_price": True}}},
    {"schema_version": 1, "items": {"x": {"last_checked": "yesterday"}}},
])
def test_state_rejects_every_malformed_shape(tmp_path, document):
    path = tmp_path / "state" / "x.json"
    _write(path, document)
    with pytest.raises(StateFileError):
        JsonStateRepository(path).load()


def test_state_noop_and_save_failure_are_explicit(tmp_path):
    repo = JsonStateRepository(tmp_path / "state" / "x.json")
    repo.load()
    assert not repo.has_pending
    repo.record_priced_check("x", 1, datetime.now(timezone.utc))
    with mock.patch("core.scrapers.state.write_json_atomically", side_effect=OSError("disk full")):
        with pytest.raises(StateFileError, match="disk full"):
            repo.save()
