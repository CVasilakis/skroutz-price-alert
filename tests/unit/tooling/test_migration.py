import json
import os
from pathlib import Path

import pytest
from filelock import FileLock

from core.infrastructure.migration import MigrationPhase, MigrationPlan
from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.framework.migrations import PluginMigrationDeclarationError
from core.tooling import migration as migration_module
from core.tooling.migration import (
    STATUS_CURRENT,
    STATUS_FAILED,
    STATUS_MIGRATED,
    STATUS_MISSING,
    MigrationRunner,
)


def _write(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")


def _v2_plan(validator=lambda document: None):
    def upgrade(document):
        return {**document, "added": True}

    return MigrationPlan(
        2,
        {1: (MigrationPhase("add field", upgrade),)},
        validator,
    )


def _one_plugin_catalog(target="insomnia"):
    plugin = PluginCatalog.discover().get(target)
    return plugin, PluginCatalog((plugin,))


def _outcome(outcomes, family):
    return next(outcome for outcome in outcomes if outcome.family == family)


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

    assert _outcome(outcomes, "general_config").status == STATUS_MIGRATED
    assert _outcome(outcomes, "reminder_state").status == STATUS_FAILED
    assert json.loads(general.read_text()) == {"schema_version": 2, "added": True}
    assert runner.recovery_path is not None
    assert (runner.recovery_path / "config" / "general.json").read_bytes() == original


def test_complete_success_discards_recovery_and_check_mode_only_preserves_managed_json(
    tmp_path, monkeypatch
):
    general = tmp_path / "config" / "general.json"
    _write(general, {"schema_version": 1})
    original = general.read_bytes()
    monkeypatch.setattr(migration_module, "GENERAL_CONFIG_MIGRATIONS", _v2_plan())

    check_runner = MigrationRunner(tmp_path, PluginCatalog(()))
    checked = check_runner.run(check=True)

    assert _outcome(checked, "general_config").status == STATUS_MIGRATED
    assert general.read_bytes() == original
    assert check_runner.recovery_path is None
    assert (tmp_path / "state" / ".migration.lock").exists()
    assert not list((tmp_path / "config").glob(".*.migration.*"))
    assert not list((tmp_path / "state").glob(".general.json.migration.*"))
    assert not list((tmp_path / "state").glob(".migration-recovery.*"))

    runner = MigrationRunner(tmp_path, PluginCatalog(()))
    runner.run()
    assert runner.recovery_path is None
    assert not list((tmp_path / "state").glob(".migration-recovery.*"))


def test_missing_target_config_does_not_discover_plugin_migrations_and_state_runs(
    tmp_path, monkeypatch
):
    plugin, catalog = _one_plugin_catalog()
    _write(tmp_path / "state" / f"{plugin.target}.json", {"schema_version": 1, "items": {}})

    def unexpected(_plugin):
        raise AssertionError("plugin migration module must not be inspected")

    monkeypatch.setattr(migration_module, "load_plugin_config_migrations", unexpected)
    outcomes = MigrationRunner(tmp_path, catalog).run()

    assert _outcome(outcomes, "target_config").status == STATUS_MISSING
    assert _outcome(outcomes, "scraper_state").status == STATUS_CURRENT


def test_plugin_declaration_failure_affects_config_only_and_state_continues(tmp_path, monkeypatch):
    plugin, catalog = _one_plugin_catalog()
    _write(
        tmp_path / "config" / plugin.config_filename,
        {"schema_version": 1, "settings": {}, "items": []},
    )
    _write(tmp_path / "state" / f"{plugin.target}.json", {"schema_version": 1, "items": {}})

    def invalid(_plugin):
        raise PluginMigrationDeclarationError("broken CONFIG_MIGRATIONS")

    monkeypatch.setattr(migration_module, "load_plugin_config_migrations", invalid)
    outcomes = MigrationRunner(tmp_path, catalog).run()

    assert _outcome(outcomes, "target_config").status == STATUS_FAILED
    assert "broken CONFIG_MIGRATIONS" in _outcome(outcomes, "target_config").detail
    assert _outcome(outcomes, "scraper_state").status == STATUS_CURRENT


def test_current_schema_config_error_isolated_from_state(tmp_path):
    plugin, catalog = _one_plugin_catalog()
    config = tmp_path / "config" / plugin.config_filename
    _write(config, {"schema_version": 1, "settings": {}, "items": [], "unknown": True})
    original = config.read_bytes()
    _write(tmp_path / "state" / f"{plugin.target}.json", {"schema_version": 1, "items": {}})

    outcomes = MigrationRunner(tmp_path, catalog).run()

    config_outcome = _outcome(outcomes, "target_config")
    assert config_outcome.status == STATUS_FAILED
    assert "current-schema validation at v1 failed" in config_outcome.detail
    assert _outcome(outcomes, "scraper_state").status == STATUS_CURRENT
    assert config.read_bytes() == original


def test_target_lock_contention_marks_both_target_documents_failed(tmp_path):
    plugin, catalog = _one_plugin_catalog()
    lock_path = tmp_path / "logs" / plugin.target / f"{plugin.target}_scraper_running.lock"
    lock_path.parent.mkdir(parents=True)
    with FileLock(lock_path):
        outcomes = MigrationRunner(tmp_path, catalog).run()

    assert _outcome(outcomes, "target_config").status == STATUS_FAILED
    assert _outcome(outcomes, "scraper_state").status == STATUS_FAILED
    assert "another process holds lock" in _outcome(outcomes, "target_config").detail


def test_state_failure_recovery_guidance_is_family_specific():
    scraper = MigrationRunner._failed_outcome(
        "scraper_state",
        "insomnia",
        "state/insomnia.json",
        RuntimeError("invalid legacy state"),
    )
    reminder = MigrationRunner._failed_outcome(
        "reminder_state",
        "general",
        "state/general.json",
        RuntimeError("invalid legacy state"),
    )

    assert "stored check and alert history will be lost" in scraper.detail
    assert "reminder" not in scraper.detail
    assert "stored reminder timestamp and scheduling history will be lost" in reminder.detail
    assert "a reminder may be sent again" in reminder.detail
    assert "check and alert history" not in reminder.detail


@pytest.mark.parametrize("family_path", ["config/general.json", "state/general.json"])
def test_symlink_documents_fail_without_following_target(tmp_path, family_path):
    outside = tmp_path / "outside.json"
    _write(outside, {"schema_version": 1})
    path = tmp_path / family_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.symlink_to(outside)
    original = outside.read_bytes()

    outcomes = MigrationRunner(tmp_path, PluginCatalog(())).run()
    family = "general_config" if family_path.startswith("config") else "reminder_state"

    assert _outcome(outcomes, family).status == STATUS_FAILED
    assert "symlink or special file" in _outcome(outcomes, family).detail
    assert outside.read_bytes() == original


def test_special_file_document_is_rejected_without_reading_it(tmp_path):
    general = tmp_path / "config" / "general.json"
    general.parent.mkdir(parents=True)
    os.mkfifo(general)

    outcomes = MigrationRunner(tmp_path, PluginCatalog(())).run()

    assert _outcome(outcomes, "general_config").status == STATUS_FAILED
    assert "symlink or special file" in _outcome(outcomes, "general_config").detail


def test_atomic_replacement_preserves_file_mode(tmp_path, monkeypatch):
    general = tmp_path / "config" / "general.json"
    _write(general, {"schema_version": 1})
    general.chmod(0o640)
    monkeypatch.setattr(migration_module, "GENERAL_CONFIG_MIGRATIONS", _v2_plan())

    outcomes = MigrationRunner(tmp_path, PluginCatalog(())).run()

    assert _outcome(outcomes, "general_config").status == STATUS_MIGRATED
    assert os.stat(general).st_mode & 0o777 == 0o640


def test_concurrent_change_fails_without_backup_or_replacement(tmp_path, monkeypatch):
    general = tmp_path / "config" / "general.json"
    _write(general, {"schema_version": 1})
    original = general.read_bytes()
    monkeypatch.setattr(migration_module, "GENERAL_CONFIG_MIGRATIONS", _v2_plan())
    monkeypatch.setattr(migration_module, "_unchanged", lambda path, snapshot: False)

    runner = MigrationRunner(tmp_path, PluginCatalog(()))
    outcomes = runner.run()

    assert _outcome(outcomes, "general_config").status == STATUS_FAILED
    assert "file changed" in _outcome(outcomes, "general_config").detail
    assert general.read_bytes() == original
    assert runner.recovery_path is None


def test_replacement_failure_retains_exact_recovery_copy(tmp_path, monkeypatch):
    general = tmp_path / "config" / "general.json"
    _write(general, {"schema_version": 1})
    original = general.read_bytes()
    monkeypatch.setattr(migration_module, "GENERAL_CONFIG_MIGRATIONS", _v2_plan())

    def fail_replace(path, data, mode):
        raise RuntimeError("replacement unavailable")

    monkeypatch.setattr(migration_module, "_replace_atomically", fail_replace)
    runner = MigrationRunner(tmp_path, PluginCatalog(()))
    outcomes = runner.run()

    assert _outcome(outcomes, "general_config").status == STATUS_FAILED
    assert general.read_bytes() == original
    assert runner.recovery_path is not None
    assert (runner.recovery_path / "config" / "general.json").read_bytes() == original


def test_unsupported_pre_major_documents_fail_closed_and_preserve_exact_bytes(tmp_path):
    plugin, catalog = _one_plugin_catalog()
    config = tmp_path / "config" / plugin.config_filename
    state = tmp_path / "state" / f"{plugin.target}.json"
    config.parent.mkdir(parents=True)
    state.parent.mkdir(parents=True)
    config.write_bytes(b'{\n  "settings": {},\n  "items": []\n}\n')
    state.write_bytes(b'{"schema_version":2,"items":{}}\n')
    originals = (config.read_bytes(), state.read_bytes())

    outcomes = MigrationRunner(tmp_path, catalog).run()

    assert _outcome(outcomes, "target_config").status == STATUS_FAILED
    assert "schema_version must be a positive integer" in _outcome(outcomes, "target_config").detail
    assert _outcome(outcomes, "scraper_state").status == STATUS_FAILED
    assert "newer than supported" in _outcome(outcomes, "scraper_state").detail
    assert (config.read_bytes(), state.read_bytes()) == originals
