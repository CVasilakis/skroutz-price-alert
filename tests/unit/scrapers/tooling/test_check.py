import json
import sys
from types import SimpleNamespace

import pytest

from core.scrapers.framework import migrations as framework_migrations
from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.framework.configuration import TargetConfigLoader
from core.scrapers.tooling.check import (
    _check_contributor_files,
    _check_declaration_imports,
    _check_migrations,
    _check_self_contained,
    check_plugin,
)
from core.scrapers.tooling.scaffold import ScaffoldRequest, create_plugin
from core.tooling.migration import STATUS_MIGRATED, MigrationRunner


def test_contributor_files_warn_when_target_owned_tests_are_missing(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("guide", encoding="utf-8")
    (source / "config.example.json").write_text("{}", encoding="utf-8")

    result = _check_contributor_files(source, tmp_path / "missing-tests", "acme", 1)

    assert not result.has_tests
    assert "behavior is unverified" in result.warnings[0]


def test_contributor_files_warn_when_migration_tests_are_missing(tmp_path):
    source = tmp_path / "source"
    tests = tmp_path / "tests"
    source.mkdir()
    tests.mkdir()
    (source / "README.md").write_text("guide", encoding="utf-8")
    (source / "config.example.json").write_text("{}", encoding="utf-8")
    (source / "migrations.py").write_text("CONFIG_MIGRATIONS = {}", encoding="utf-8")
    (tests / "test_client.py").write_text("def test_it(): pass", encoding="utf-8")

    with pytest.raises(RuntimeError, match="version 1 must not contain"):
        _check_contributor_files(source, tests, "acme", 1)

    result = _check_contributor_files(source, tests, "acme", 2)
    assert result.has_tests
    assert "migrations are unverified" in result.warnings[0]


@pytest.mark.parametrize(
    "export,expected",
    [
        (None, "must export CONFIG_MIGRATIONS as a dict"),
        ([], "must export CONFIG_MIGRATIONS as a dict"),
        ({True: object()}, "keys must be positive integer versions"),
        ({2: object()}, "exactly one transition"),
        ({1: object()}, "values must be callables"),
    ],
)
def test_migration_verifier_uses_shared_declaration_validation(monkeypatch, export, expected):
    module = SimpleNamespace()
    if export is not None:
        module.CONFIG_MIGRATIONS = export
    monkeypatch.setattr(framework_migrations.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(framework_migrations.importlib, "import_module", lambda name: module)
    with pytest.raises(RuntimeError, match=expected):
        _check_migrations(SimpleNamespace(package="plugins.acme", config_schema_version=2))


def test_migration_verifier_accepts_valid_phase_and_absent_module(monkeypatch):
    plugin = SimpleNamespace(package="plugins.acme", config_schema_version=1)
    _check_migrations(plugin)

    plugin = SimpleNamespace(package="plugins.acme", config_schema_version=2)
    module = SimpleNamespace(CONFIG_MIGRATIONS={1: lambda document: dict(document)})
    monkeypatch.setattr(framework_migrations.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(framework_migrations.importlib, "import_module", lambda name: module)
    _check_migrations(plugin)


def test_migration_verifier_translates_import_failure(monkeypatch):
    monkeypatch.setattr(framework_migrations.importlib.util, "find_spec", lambda name: object())

    def fail(name):
        raise ImportError("boom")

    monkeypatch.setattr(framework_migrations.importlib, "import_module", fail)
    with pytest.raises(RuntimeError, match="could not import.*boom"):
        _check_migrations(SimpleNamespace(package="plugins.acme", config_schema_version=2))


def test_self_contained_check_rejects_sibling_plugin_import(tmp_path):
    source = tmp_path / "acme"
    source.mkdir()
    (source / "client.py").write_text(
        "from core.scrapers.plugins.other_store.client import Client\n", encoding="utf-8"
    )

    with pytest.raises(RuntimeError, match="sibling plugin 'other_store'"):
        _check_self_contained(source, "acme", frozenset({"acme", "other_store"}))


def test_declaration_imports_allow_stdlib_public_api_and_package_local(tmp_path):
    source = tmp_path / "acme"
    source.mkdir()
    (source / "plugin.py").write_text(
        "import json\n"
        "from core.scrapers.api import ScraperPlugin\n"
        "from .helper import LOCAL\n"
        "from plugins.acme.helper import VALUE\n",
        encoding="utf-8",
    )

    _check_declaration_imports(source, "plugins.acme", "acme")


def test_declaration_imports_reject_other_core_internals(tmp_path):
    source = tmp_path / "acme"
    source.mkdir()
    (source / "migrations.py").write_text(
        "from core.schema_migrations.engine import MigrationPhase\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="imports disallowed module"):
        _check_declaration_imports(source, "plugins.acme", "acme")


@pytest.mark.parametrize(
    "statement",
    [
        "from ..other import VALUE\n",
        "from ....exceptions import ResourceNotFoundError\n",
    ],
)
def test_declaration_imports_reject_upward_relative_imports(tmp_path, statement):
    source = tmp_path / "acme"
    source.mkdir()
    (source / "plugin.py").write_text(statement, encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"descriptor plugin.py.*escaped relative import"):
        _check_declaration_imports(source, "plugins.acme", "acme")


def test_declaration_imports_reject_absolute_third_party_modules(tmp_path):
    source = tmp_path / "acme"
    source.mkdir()
    (source / "__init__.py").write_text("import requests\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match=r"descriptor __init__.py.*'import requests'"):
        _check_declaration_imports(source, "plugins.acme", "acme")


def test_verifier_rejects_an_empty_example_config(tmp_path):
    import core.scrapers.plugins as plugin_package

    target_dir = create_plugin(
        tmp_path,
        ScaffoldRequest("empty_store", "Empty Store", ("store.example",), "/items/"),
    ).source
    discovery_root = target_dir.parent
    example = target_dir / "config.example.json"
    example.write_text(
        (
            '{"schema_version":1,"plugin_schema_version":1,'
            '"settings":{"execution_interval":"1h"},"items":[]}'
        ),
        encoding="utf-8",
    )
    saved_path = list(plugin_package.__path__)
    plugin_package.__path__.append(str(discovery_root))
    try:
        catalog = PluginCatalog.discover(discovery_root, package="core.scrapers.plugins")
        with pytest.raises(RuntimeError, match="at least one valid item"):
            check_plugin("empty_store", catalog, repo_root=tmp_path)
    finally:
        plugin_package.__path__[:] = saved_path
        for name in tuple(sys.modules):
            if name == "core.scrapers.plugins.empty_store" or name.startswith(
                "core.scrapers.plugins.empty_store."
            ):
                sys.modules.pop(name, None)


def test_real_version_two_plugin_verifies_migrates_and_loads(tmp_path):
    import core.scrapers.plugins as plugin_package

    target = "versioned_store"
    discovery_root = tmp_path / "core" / "scrapers" / "plugins"
    source = discovery_root / target
    source.mkdir(parents=True)
    (source / "__init__.py").write_text('"""Import-light package marker."""\n', encoding="utf-8")
    (source / "plugin.py").write_text(
        """from core.scrapers.api import ItemField, ScraperPlugin, UrlField
TAG = ItemField("tag", str)
URL = UrlField(
    "url", domains=("store.example",),
    accepts_url=lambda url: url.path.startswith("/products/"),
)
PLUGIN = ScraperPlugin(
    display_name="Versioned Store",
    config_schema_version=2,
    item_fields=(URL, TAG),
    reference_url=URL,
)
""",
        encoding="utf-8",
    )
    (source / "migrations.py").write_text(
        """from core.scrapers.api import JsonObject

def migrate_v1_to_v2(document: JsonObject) -> JsonObject:
    items = []
    for row in document.get("items", []):
        if isinstance(row, dict) and "legacy_tag" in row:
            upgraded = {key: value for key, value in row.items() if key != "legacy_tag"}
            upgraded["tag"] = row["legacy_tag"]
            items.append(upgraded)
        else:
            items.append(row)
    return {**document, "items": items}

CONFIG_MIGRATIONS = {1: migrate_v1_to_v2}
""",
        encoding="utf-8",
    )
    (source / "client.py").write_text(
        """from core.scrapers.api import PriceResult, ScraperClient, TrackedItem
class Client(ScraperClient):
    def scrape(self, item: TrackedItem) -> PriceResult:
        return PriceResult(1, "EUR")
""",
        encoding="utf-8",
    )
    (source / "README.md").write_text(
        "Tracks product pages. Schema v2 renames legacy_tag to tag.",
        encoding="utf-8",
    )
    current = {
        "schema_version": 1,
        "plugin_schema_version": 2,
        "settings": {},
        "items": [
            {
                "id": "one",
                "name": "One",
                "url": "https://store.example/products/1",
                "tag": "featured",
                "target_price": 2,
            }
        ],
    }
    (source / "config.example.json").write_text(json.dumps(current), encoding="utf-8")
    tests = tmp_path / "tests" / "plugins" / target
    tests.mkdir(parents=True)
    (tests / "test_client.py").write_text("def test_placeholder(): pass\n", encoding="utf-8")
    (tests / "test_migrations.py").write_text("def test_placeholder(): pass\n", encoding="utf-8")

    saved_path = list(plugin_package.__path__)
    plugin_package.__path__.append(str(discovery_root))
    try:
        catalog = PluginCatalog.discover(discovery_root, package="core.scrapers.plugins")
        plugin = catalog.get(target)
        checks = check_plugin(target, catalog, repo_root=tmp_path)
        assert "isolated import-light descriptor" in checks
        assert "versioned pure migrations" in checks

        legacy = {
            **current,
            "plugin_schema_version": 1,
            "items": [
                {
                    "id": "one",
                    "name": "One",
                    "url": "https://store.example/products/1",
                    "legacy_tag": "featured",
                    "target_price": 2,
                }
            ],
        }
        config_path = tmp_path / "config" / plugin.config_filename
        config_path.parent.mkdir()
        config_path.write_text(json.dumps(legacy), encoding="utf-8")

        outcomes = MigrationRunner(tmp_path, catalog).run()
        target_outcome = next(outcome for outcome in outcomes if outcome.family == "target_config")
        assert target_outcome.status == STATUS_MIGRATED
        assert target_outcome.detail == "plugin v1 to v2"
        migrated = json.loads(config_path.read_text())
        assert migrated["schema_version"] == 1
        assert migrated["plugin_schema_version"] == 2
        loaded = TargetConfigLoader(plugin, str(config_path.parent)).load()
        assert loaded.items[0][plugin.item_fields[1]] == "featured"
    finally:
        plugin_package.__path__[:] = saved_path
        for name in tuple(sys.modules):
            if name == f"core.scrapers.plugins.{target}" or name.startswith(
                f"core.scrapers.plugins.{target}."
            ):
                sys.modules.pop(name, None)


@pytest.mark.parametrize(
    "settings,item,expected",
    [
        ({}, {"tag": "featured"}, "custom setting 'region'"),
        ({"region": "global"}, {}, "custom item field 'tag'"),
    ],
)
def test_verifier_requires_examples_to_demonstrate_custom_schema(
    tmp_path, settings, item, expected
):
    import core.scrapers.plugins as plugin_package

    discovery_root = tmp_path / "core" / "scrapers" / "plugins"
    source = discovery_root / "custom_store"
    source.mkdir(parents=True)
    (source / "__init__.py").write_text("", encoding="utf-8")
    (source / "plugin.py").write_text(
        """from core.scrapers.api import ItemField, ScraperPlugin, SettingSpec, UrlField
TAG = ItemField("tag", str, default="plain")
REGION = SettingSpec("region", str, default="global")
URL = UrlField(
    "url", domains=("store.example",),
    accepts_url=lambda url: url.path.startswith("/products/"),
)
PLUGIN = ScraperPlugin(
    display_name="Custom Store", item_fields=(URL, TAG), settings=(REGION,),
    reference_url=URL,
)
""",
        encoding="utf-8",
    )
    (source / "client.py").write_text(
        """from core.scrapers.api import PriceResult, ScraperClient, TrackedItem
class Client(ScraperClient):
    def scrape(self, item: TrackedItem) -> PriceResult:
        return PriceResult(1, "EUR")
""",
        encoding="utf-8",
    )
    (source / "README.md").write_text(
        "Accepts product URLs and returns PriceResult. Custom keys: tag, region.",
        encoding="utf-8",
    )
    document = {
        "schema_version": 1,
        "plugin_schema_version": 1,
        "settings": settings,
        "items": [
            {
                "id": "one",
                "name": "One",
                "url": "https://store.example/products/1",
                "target_price": 2,
                **item,
            }
        ],
    }
    import json

    (source / "config.example.json").write_text(json.dumps(document), encoding="utf-8")
    tests = tmp_path / "tests" / "plugins" / "custom_store"
    tests.mkdir(parents=True)
    (tests / "test_client.py").write_text("def test_placeholder(): pass\n", encoding="utf-8")

    saved_path = list(plugin_package.__path__)
    plugin_package.__path__.append(str(discovery_root))
    try:
        catalog = PluginCatalog.discover(discovery_root, package="core.scrapers.plugins")
        with pytest.raises(RuntimeError, match=expected):
            check_plugin("custom_store", catalog, repo_root=tmp_path)
    finally:
        plugin_package.__path__[:] = saved_path
        for name in tuple(sys.modules):
            if name == "core.scrapers.plugins.custom_store" or name.startswith(
                "core.scrapers.plugins.custom_store."
            ):
                sys.modules.pop(name, None)
