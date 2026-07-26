import json
from pathlib import Path

from core.infrastructure.migration import (
    MigrationPhase,
    MigrationPlan,
    MigrationTransition,
)
from core.scrapers.framework.catalog import PluginCatalog
from core.tooling import migration as migration_module
from core.tooling.migration import STATUS_FAILED, STATUS_MIGRATED, MigrationRunner


def _write(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def _v2_plan():
    def upgrade(document):
        return {**document, "added": True}

    return MigrationPlan(
        2,
        {
            1: MigrationTransition(
                1,
                (MigrationPhase("add field", upgrade),),
            )
        },
        lambda document: None,
    )


def test_partial_failure_retains_mirrored_original_without_reverting_success(tmp_path, monkeypatch):
    general = tmp_path / "config" / "general.json"
    reminder = tmp_path / "state" / "general.json"
    _write(general, {"schema_version": 1})
    original = general.read_bytes()
    reminder.parent.mkdir(parents=True, exist_ok=True)
    reminder.write_text("{bad", encoding="utf-8")
    monkeypatch.setattr(migration_module, "GENERAL_CONFIG_MIGRATIONS", _v2_plan())

    runner = MigrationRunner(tmp_path, PluginCatalog(()))
    outcomes = runner.run()

    assert any(
        outcome.family == "general_config" and outcome.status == STATUS_MIGRATED
        for outcome in outcomes
    )
    assert any(
        outcome.family == "reminder_state" and outcome.status == STATUS_FAILED
        for outcome in outcomes
    )
    assert json.loads(general.read_text()) == {"schema_version": 2, "added": True}
    assert runner.recovery_path is not None
    assert (runner.recovery_path / "config" / "general.json").read_bytes() == original


def test_complete_success_discards_recovery_and_check_mode_is_read_only(tmp_path, monkeypatch):
    general = tmp_path / "config" / "general.json"
    _write(general, {"schema_version": 1})
    original = general.read_bytes()
    monkeypatch.setattr(migration_module, "GENERAL_CONFIG_MIGRATIONS", _v2_plan())

    check_runner = MigrationRunner(tmp_path, PluginCatalog(()))
    checked = check_runner.run(check=True)
    assert any(outcome.status == STATUS_MIGRATED for outcome in checked)
    assert general.read_bytes() == original
    assert check_runner.recovery_path is None

    runner = MigrationRunner(tmp_path, PluginCatalog(()))
    runner.run()
    assert runner.recovery_path is None
    assert not list((tmp_path / "state").glob(".migration-recovery.*"))
