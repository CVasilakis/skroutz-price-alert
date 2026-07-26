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
    LoadFailure,
    LoadFailureKind,
    TargetLoad,
    load_targets,
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
    load = load_targets([plugin], str(tmp_path / "config"), str(tmp_path / "state"))[0]
    assert load.count == 1 and load.failure is None


def test_preflight_distinguishes_config_and_state_failures(tmp_path, plugin):
    config_failure = load_targets([plugin], str(tmp_path / "config"), str(tmp_path / "state"))[0]
    assert config_failure.failure is not None
    assert config_failure.failure.kind is LoadFailureKind.CONFIG
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
    _write(tmp_path / "state" / "fakestore.json", {"schema_version": 1, "items": []})
    state_failure = load_targets([plugin], str(tmp_path / "config"), str(tmp_path / "state"))[0]
    assert state_failure.failure is not None
    assert state_failure.failure.kind is LoadFailureKind.STATE
    assert state_failure.failure.detail == (
        "Fix invalid state in `state/fakestore.json`; details are logged."
    )
    assert state_failure.failure.diagnostic is not None
    assert str((tmp_path / "state" / "fakestore.json").resolve()) in (
        state_failure.failure.diagnostic
    )
    assert state_failure.count == 1
    assert state_failure.faulty_indices == [2]
    assert state_failure.row_diagnostic is not None
    assert "JSON item 2:" in state_failure.row_diagnostic
    record_target_load_diagnostic(state_failure)
    diagnostic_log = (
        Path(core.infrastructure.logging.LOGS_DIR) / "fakestore" / "errors.txt"
    ).read_text()
    assert "items must be an object" in diagnostic_log
    assert "JSON item 2:" in diagnostic_log
    assert "name must be a nonblank string" in diagnostic_log


def test_target_load_rejects_inconsistent_failure_combinations(plugin):
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
