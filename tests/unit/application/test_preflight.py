import json
from pathlib import Path

import pytest
from support import catalog_sandbox, fake_plugin

import core.infrastructure.logging
from core.application.diagnostics import (
    record_general_diagnostic,
    record_target_load_diagnostic,
)
from core.application.preflight import (
    TargetConfigFailure,
    TargetConfigLoad,
    load_target_configs,
)
from core.general.configuration import GeneralConfigLoad
from core.notifications.configuration import NotificationConfig
from core.settings import resolve_settings


@pytest.fixture
def plugin():
    with catalog_sandbox(fake_plugin()) as catalog:
        yield catalog.get("fakestore")


def _write(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))


def test_preflight_loads_settings_and_items_once(tmp_path, plugin):
    _write(
        tmp_path / "config" / "fakestore.json",
        {
            "schema_version": 1,
            "plugin_schema_version": 1,
            "settings": {"log_retention_days": 3},
            "items": [
                {
                    "id": "one",
                    "name": "One",
                    "url": "https://fake-store.example/products/1",
                    "target_price": 1,
                }
            ],
        },
    )
    load = load_target_configs([plugin], str(tmp_path / "config"))[0]
    assert load.count == 1 and load.failure is None


def test_preflight_reports_config_failures_and_row_diagnostics(tmp_path, plugin):
    config_failure = load_target_configs([plugin], str(tmp_path / "config"))[0]
    assert config_failure.failure is not None
    assert config_failure.failure.detail == (
        "Create missing `config/fakestore.json` from the plugin example."
    )
    assert config_failure.failure.diagnostic is not None
    assert str((tmp_path / "config" / "fakestore.json").resolve()) in (
        config_failure.failure.diagnostic
    )
    _write(
        tmp_path / "config" / "fakestore.json",
        {
            "schema_version": 1,
            "plugin_schema_version": 1,
            "settings": {"log_retention_days": 3},
            "items": [
                {
                    "id": "one",
                    "name": "One",
                    "url": "https://fake-store.example/products/1",
                    "target_price": 1,
                },
                {"id": "bad", "name": " ", "url": "bad", "target_price": 1},
            ],
        },
    )
    loaded = load_target_configs([plugin], str(tmp_path / "config"))[0]
    assert loaded.failure is None
    assert loaded.count == 1
    assert loaded.faulty_indices == [2]
    assert loaded.row_diagnostic is not None
    assert "JSON item 2:" in loaded.row_diagnostic
    record_target_load_diagnostic(loaded)
    diagnostic_log = (
        Path(core.infrastructure.logging.LOGS_DIR) / "fakestore" / "errors.txt"
    ).read_text()
    assert "JSON item 2:" in diagnostic_log
    assert "name must be a nonblank string" in diagnostic_log


def test_target_config_load_rejects_inconsistent_failure_combinations(plugin):
    settings = resolve_settings(plugin.setting_specs, {})
    TargetConfigLoad(plugin, settings)
    with pytest.raises(ValueError, match="cannot contain decoded items"):
        TargetConfigLoad(
            plugin,
            settings,
            items=(object(),),
            failure=TargetConfigFailure("broken"),
        )
    with pytest.raises(ValueError, match="detail"):
        TargetConfigFailure(" ")


def test_general_diagnostic_write_status_is_propagated(monkeypatch):
    load = GeneralConfigLoad(
        NotificationConfig(error="broken"),
        None,
        diagnostic="Path: /absolute/config/general.json",
    )
    monkeypatch.setattr(
        "core.application.diagnostics.try_save_diagnostic",
        lambda _detail: False,
    )

    recorded = record_general_diagnostic(load)

    assert recorded is not load
    assert recorded.diagnostic_saved is False
