import json
import logging
import shutil
import sys
from pathlib import Path
from unittest import mock

import pytest
from support import mock_notifier, mock_ui

from core.orchestrator import ScrapingOrchestrator
from core.preflight import load_targets
from core.scrapers.api import ScraperClient
from core.scrapers.check import check_plugin
from core.scrapers.registry import ClientLoader, PluginCatalog

CATALOG = PluginCatalog.discover()


@pytest.mark.parametrize("target", CATALOG.targets)
def test_every_plugin_passes_contributor_verifier(target):
    assert "state round-trip" in check_plugin(target, CATALOG)


def test_plugin_source_and_test_packages_are_one_to_one():
    assert "_example" not in CATALOG.targets
    test_targets = {
        path.name
        for path in Path("tests/plugins").iterdir()
        if path.is_dir() and not path.name.startswith("_")
    }
    assert test_targets == set(CATALOG.targets)


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


def test_copyable_template_runs_end_to_end_without_framework_edits(tmp_path):
    """A copied package reaches state persistence through the production bindings."""
    import core.scrapers as scraper_package

    discovery_root = tmp_path / "core" / "scrapers"
    target_dir = discovery_root / "template_store"
    shutil.copytree(Path("src/core/scrapers/_example"), target_dir)
    test_dir = tmp_path / "tests" / "plugins" / "template_store"
    test_dir.mkdir(parents=True)
    (test_dir / "test_client.py").write_text("def test_placeholder(): pass\n")

    saved_path = list(scraper_package.__path__)
    scraper_package.__path__.append(str(discovery_root))
    try:
        catalog = PluginCatalog.discover(discovery_root, package="core.scrapers")
        checks = check_plugin("template_store", catalog, repo_root=tmp_path)
        assert "state round-trip" in checks
        assert "conventional lazy Client" in checks
        config_dir = tmp_path / "config"
        state_dir = tmp_path / "state"
        config_dir.mkdir()
        shutil.copy2(target_dir / "config.example.json", config_dir / "template_store.json")
        loads = load_targets([catalog.get("template_store")], str(config_dir), str(state_dir))
        orchestrator = ScrapingOrchestrator(
            loads,
            ClientLoader(),
            mock_notifier(),
            quiet=True,
            reporter=mock_ui(),
        )
        with (
            mock.patch("core.execution.ItemExecutor.sleep_with_jitter"),
            mock.patch("core.orchestrator.signal.signal"),
            mock.patch(
                "core.orchestrator.get_target_logger",
                return_value=logging.getLogger("template-e2e"),
            ),
        ):
            assert orchestrator.run() == 0
        state = json.loads((state_dir / "template_store.json").read_text())
        assert state["items"]["sample-widget"]["last_price"] == 1.0
    finally:
        scraper_package.__path__[:] = saved_path
        for name in tuple(sys.modules):
            if name == "core.scrapers.template_store" or name.startswith(
                "core.scrapers.template_store."
            ):
                sys.modules.pop(name, None)
