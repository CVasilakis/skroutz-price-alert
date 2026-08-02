import ast
import json
import logging
import shutil
import sys
from pathlib import Path
from unittest import mock

import pytest
from support import mock_notifier, mock_ui

from core.application.orchestrator import ScrapingOrchestrator
from core.application.preflight import load_target_configs
from core.scrapers.api import PriceResult, ScraperClient
from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.framework.clients import ClientLoader
from core.scrapers.tooling.check import check_plugin
from core.scrapers.tooling.scaffold.api import create_plugin
from core.scrapers.tooling.scaffold.contracts import ScaffoldRequest

CATALOG = PluginCatalog.discover()


@pytest.mark.parametrize("target", CATALOG.targets)
def test_every_plugin_passes_contributor_verifier(target):
    assert "state round-trip" in check_plugin(target, CATALOG)


def test_plugin_test_packages_never_outlive_their_source_plugin():
    test_targets = {
        path.name
        for path in Path("tests/plugins").iterdir()
        if path.is_dir() and not path.name.startswith("_")
    }
    assert test_targets <= set(CATALOG.targets)


def test_plugin_tests_use_only_the_contributor_test_seam():
    forbidden_imports = ("core.exceptions", "core.settings", "core.scrapers.framework")
    for path in Path("tests/plugins").glob("*/test_*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = [node.module]
            else:
                imported = []
            for module in imported:
                assert not module.startswith(forbidden_imports), (
                    f"{path} imports private framework module {module!r}; "
                    "use core.scrapers.api and tests/support.py"
                )
            if isinstance(node, ast.Call):
                assert all(keyword.arg != "_custom" for keyword in node.keywords), (
                    f"{path} constructs TrackedItem._custom directly; "
                    "use support.decode_test_config"
                )


@pytest.mark.parametrize("target", CATALOG.targets)
def test_client_binding_is_lazy_and_typed(target, tmp_path):
    plugin = CATALOG.get(target)
    loader = ClientLoader()
    from core.settings import resolve_settings

    client = loader.load(plugin, resolve_settings(plugin.setting_specs, {}))
    try:
        assert isinstance(client, ScraperClient)
    finally:
        client.close()


def test_scaffolded_plugin_runs_end_to_end_without_framework_edits(tmp_path):
    """The contributor scaffold reaches persistence through production bindings."""
    import core.scrapers.plugins as plugin_package

    (tmp_path / "src/core/scrapers/plugins").mkdir(parents=True)
    (tmp_path / "tests/plugins").mkdir(parents=True)
    scaffold = create_plugin(
        tmp_path,
        ScaffoldRequest("template_store", "Template Store", ("store.example",), "/items/"),
    )
    target_dir = scaffold.source
    discovery_root = target_dir.parent

    saved_path = list(plugin_package.__path__)
    plugin_package.__path__.append(str(discovery_root))
    try:
        catalog = PluginCatalog.discover(discovery_root, package="core.scrapers.plugins")
        checks = check_plugin("template_store", catalog, repo_root=tmp_path)
        assert "state round-trip" in checks
        assert "conventional lazy Client" in checks
        config_dir = tmp_path / "config"
        state_dir = tmp_path / "state"
        config_dir.mkdir()
        shutil.copy2(target_dir / "config.example.json", config_dir / "template_store.json")
        loads = load_target_configs([catalog.get("template_store")], str(config_dir))
        orchestrator = ScrapingOrchestrator(
            loads,
            ClientLoader(),
            mock_notifier(),
            quiet=True,
            reporter=mock_ui(),
            state_dir=str(state_dir),
        )
        with (
            mock.patch("core.application.pacing.Pacer.sleep"),
            mock.patch("core.application.orchestrator.signal.signal"),
            mock.patch(
                "core.scrapers.plugins.template_store.client.Client.scrape",
                return_value=PriceResult(price=1.0, currency="EUR"),
            ),
            mock.patch(
                "core.application.orchestrator.get_target_logger",
                return_value=logging.getLogger("template-e2e"),
            ),
        ):
            assert orchestrator.run() == 0
        state = json.loads((state_dir / "template_store.json").read_text())
        assert state["items"]["sample-item"]["last_price"] == 1.0
    finally:
        plugin_package.__path__[:] = saved_path
        for name in tuple(sys.modules):
            if name == "core.scrapers.plugins.template_store" or name.startswith(
                "core.scrapers.plugins.template_store."
            ):
                sys.modules.pop(name, None)
