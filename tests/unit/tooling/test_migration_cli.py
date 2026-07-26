from pathlib import Path

import pytest

from core.constants import (
    EXIT_CODE_NOTIFICATION_CONFIG_ERROR,
    EXIT_CODE_PRODUCTS_ERROR,
    EXIT_CODE_STORAGE_ERROR,
)
from core.tooling import migration_cli
from core.tooling.migration import (
    STATUS_CURRENT,
    STATUS_FAILED,
    STATUS_MIGRATED,
    MigrationOutcome,
)


def _outcome(family, status, detail=""):
    target = "general" if family in {"general_config", "reminder_state"} else "store"
    directory = "config" if family.endswith("config") else "state"
    return MigrationOutcome(family, target, f"{directory}/{target}.json", status, detail)


@pytest.mark.parametrize(
    "family,expected",
    [
        ("target_config", EXIT_CODE_PRODUCTS_ERROR),
        ("general_config", EXIT_CODE_NOTIFICATION_CONFIG_ERROR),
        ("scraper_state", EXIT_CODE_STORAGE_ERROR),
        ("reminder_state", EXIT_CODE_STORAGE_ERROR),
    ],
)
def test_exit_codes_preserve_document_family_classes(family, expected):
    assert migration_cli._exit_code((_outcome(family, STATUS_FAILED),), check=False) == expected


def test_products_failure_has_precedence_over_other_document_failures():
    outcomes = (
        _outcome("general_config", STATUS_FAILED),
        _outcome("scraper_state", STATUS_FAILED),
        _outcome("target_config", STATUS_FAILED),
    )
    assert migration_cli._exit_code(outcomes, check=False) == EXIT_CODE_PRODUCTS_ERROR


def test_check_mode_returns_one_for_pending_migration_only():
    assert migration_cli._exit_code((_outcome("general_config", STATUS_MIGRATED),), check=True) == 1
    assert migration_cli._exit_code((_outcome("general_config", STATUS_CURRENT),), check=True) == 0


def test_machine_output_keeps_field_order_and_sanitizes_detail(capsys):
    outcome = MigrationOutcome(
        "target_config",
        "store",
        "config/store.json",
        STATUS_FAILED,
        "one\ttwo\nthree\rfour",
    )

    migration_cli._print_machine((outcome,), Path("/tmp/recovery"))

    assert capsys.readouterr().out.splitlines() == [
        "target_config\tstore\tfailed\tconfig/store.json\tone two three four",
        "recovery\tgeneral\tretained\t/tmp/recovery\t",
    ]


def test_human_output_hides_missing_and_reports_recovery(capsys):
    outcomes = (
        _outcome("general_config", STATUS_CURRENT),
        _outcome("target_config", "missing"),
    )

    migration_cli._print_human(outcomes, Path("/tmp/recovery"))

    captured = capsys.readouterr()
    assert captured.out == "config/general.json: current\n"
    assert captured.err == "Recovery copies retained at: /tmp/recovery\n"


def test_startup_failure_is_concise_without_traceback(monkeypatch, capsys):
    def fail():
        raise RuntimeError("catalog unavailable")

    monkeypatch.setattr(migration_cli.PluginCatalog, "discover", fail)

    assert migration_cli.main(["--root", "/tmp/unused"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "Migration could not start: catalog unavailable\n"
    assert "Traceback" not in captured.err


def test_main_passes_check_and_machine_contract_through(monkeypatch, capsys):
    pending = _outcome("general_config", STATUS_MIGRATED, "pending v1 to v2")

    class Runner:
        recovery_path = None

        def __init__(self, root, catalog):
            assert root == "/project"
            assert catalog == "catalog"

        def run(self, *, check=False):
            assert check is True
            return (pending,)

    monkeypatch.setattr(migration_cli.PluginCatalog, "discover", lambda: "catalog")
    monkeypatch.setattr(migration_cli, "MigrationRunner", Runner)

    assert migration_cli.main(["--root", "/project", "--check", "--machine"]) == 1
    assert capsys.readouterr().out == (
        "general_config\tgeneral\tmigrated\tconfig/general.json\tpending v1 to v2\n"
    )
