from types import SimpleNamespace

import pytest

from core.scrapers.framework import migrations
from core.scrapers.framework.migrations import (
    PluginMigrationDeclarationError,
    load_plugin_config_migration_plan,
)


def _plugin(version=2):
    return SimpleNamespace(
        package="plugins.acme",
        config_schema_version=version,
    )


def test_version_one_plan_does_not_inspect_or_import_a_module(monkeypatch):
    def unexpected(_name):
        raise AssertionError("version-one migrations must not be inspected")

    monkeypatch.setattr(migrations.importlib.util, "find_spec", unexpected)
    plan = load_plugin_config_migration_plan(_plugin(1))

    assert plan.version_key == "plugin_schema_version"
    assert plan.current_version == 1
    assert dict(plan.transitions) == {}


def test_versioned_plugin_requires_importable_module(monkeypatch):
    monkeypatch.setattr(migrations.importlib.util, "find_spec", lambda _name: None)

    with pytest.raises(PluginMigrationDeclarationError, match="migrations.py is required"):
        load_plugin_config_migration_plan(_plugin())


@pytest.mark.parametrize(
    "export, expected",
    [
        (None, "must export CONFIG_MIGRATIONS as a dict"),
        ([], "must export CONFIG_MIGRATIONS as a dict"),
        ({}, "exactly one transition"),
        ({2: lambda document: document}, "exactly one transition"),
        ({1: object()}, "values must be callables"),
        ({True: lambda document: document}, "keys must be positive integer"),
    ],
)
def test_plugin_declaration_requires_exact_callable_coverage(monkeypatch, export, expected):
    module = SimpleNamespace()
    if export is not None:
        module.CONFIG_MIGRATIONS = export
    monkeypatch.setattr(migrations.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(migrations.importlib, "import_module", lambda _name: module)

    with pytest.raises(PluginMigrationDeclarationError, match=expected):
        load_plugin_config_migration_plan(_plugin())


def test_plain_callables_are_wrapped_with_framework_owned_phase_names(monkeypatch):
    def transform(document):
        return {**document, "changed": True}

    module = SimpleNamespace(CONFIG_MIGRATIONS={1: transform})
    monkeypatch.setattr(migrations.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(migrations.importlib, "import_module", lambda _name: module)

    plan = load_plugin_config_migration_plan(_plugin())

    phase = plan.transitions[1][0]
    assert phase.name == "plugin config v1 to v2"
    assert phase.transform is transform


def test_plugin_import_failure_is_translated(monkeypatch):
    monkeypatch.setattr(migrations.importlib.util, "find_spec", lambda _name: object())

    def fail(_name):
        raise ImportError("boom")

    monkeypatch.setattr(migrations.importlib, "import_module", fail)
    with pytest.raises(PluginMigrationDeclarationError, match="could not import.*boom"):
        load_plugin_config_migration_plan(_plugin())
