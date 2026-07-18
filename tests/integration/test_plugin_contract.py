import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core.scrapers.api import ScraperClient
from core.scrapers.check import check_plugin
from core.scrapers.registry import ClientFactory, PluginCatalog

CATALOG = PluginCatalog.discover()


@pytest.mark.parametrize("target", CATALOG.targets)
def test_every_plugin_passes_contributor_verifier(target):
    result = subprocess.run(
        ["./scripts/plugin-check.sh", f"--{target}"], text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ok\tstate round-trip" in result.stdout


@pytest.mark.parametrize("target", CATALOG.targets)
def test_client_binding_is_lazy_and_typed(target, tmp_path):
    plugin = CATALOG.get(target)
    factory = ClientFactory()
    from core.settings import resolve_settings
    client = factory.create(plugin, resolve_settings(plugin.setting_specs, {}))
    try:
        assert isinstance(client, ScraperClient)
    finally:
        factory.close()


def test_copyable_template_discovers_without_framework_edits(tmp_path, monkeypatch):
    """A copied package is discoverable and passes the same contributor verifier."""
    import core.scrapers as scraper_package

    discovery_root = tmp_path / "core" / "scrapers"
    target_dir = discovery_root / "template_store"
    shutil.copytree(Path("src/core/scrapers/_example"), target_dir)

    saved_path = list(scraper_package.__path__)
    scraper_package.__path__.append(str(discovery_root))
    try:
        catalog = PluginCatalog.discover(discovery_root, package="core.scrapers")
        checks = check_plugin("template_store", catalog)
        assert "state round-trip" in checks
        assert "conventional lazy Client" in checks
    finally:
        scraper_package.__path__[:] = saved_path
        for name in tuple(sys.modules):
            if name == "core.scrapers.template_store" or name.startswith(
                "core.scrapers.template_store."
            ):
                sys.modules.pop(name, None)
