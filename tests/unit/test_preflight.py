import json

import pytest

from core.preflight import LoadFailure, LoadFailureKind, TargetLoad, load_targets
from core.settings import resolve_settings
from core.scrapers.registry import PluginCatalog

CATALOG = PluginCatalog.discover()


def _write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))


def test_preflight_loads_settings_and_items_once(tmp_path):
    _write(tmp_path / "config" / "skroutz.json", {
        "settings": {"log_retention_days": 3},
        "items": [{
            "id": "one", "name": "One", "url": "https://skroutz.gr/s/1/One.html",
            "target_price": 1,
        }],
    })
    load = load_targets(
        [CATALOG.get("skroutz")], str(tmp_path / "config"), str(tmp_path / "state")
    )[0]
    assert load.count == 1 and load.failure is None


def test_preflight_distinguishes_config_and_state_failures(tmp_path):
    plugin = CATALOG.get("skroutz")
    config_failure = load_targets(
        [plugin], str(tmp_path / "config"), str(tmp_path / "state")
    )[0]
    assert config_failure.failure is not None
    assert config_failure.failure.kind is LoadFailureKind.CONFIG
    _write(tmp_path / "config" / "skroutz.json", {
        "settings": {"log_retention_days": 3},
        "items": [{
            "id": "one", "name": "One",
            "url": "https://skroutz.gr/s/1/One.html", "target_price": 1,
        }, {"id": "bad", "name": " ", "url": "bad", "target_price": 1}],
    })
    _write(tmp_path / "state" / "skroutz.json", {"schema_version": 1, "items": []})
    state_failure = load_targets(
        [plugin], str(tmp_path / "config"), str(tmp_path / "state")
    )[0]
    assert state_failure.failure is not None
    assert state_failure.failure.kind is LoadFailureKind.STATE
    assert state_failure.count == 1
    assert state_failure.faulty_indices == [2]


def test_target_load_rejects_inconsistent_failure_combinations():
    plugin = CATALOG.get("skroutz")
    settings = resolve_settings(plugin.setting_specs, {})
    with pytest.raises(ValueError, match="requires state"):
        TargetLoad(plugin, settings)
    with pytest.raises(ValueError, match="cannot contain state"):
        TargetLoad(
            plugin,
            settings,
            state=object(),
            failure=LoadFailure(LoadFailureKind.STATE, "broken"),
        )
    with pytest.raises(ValueError, match="detail"):
        LoadFailure(LoadFailureKind.CONFIG, " ")
