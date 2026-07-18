import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from core.scrapers.api import ScraperClient
from core.scrapers.check import check_plugin
from core.scrapers.registry import ScraperRegistry


@pytest.mark.parametrize("target", ScraperRegistry.registered_targets())
def test_every_plugin_passes_contributor_verifier(target):
    result = subprocess.run(
        ["./scripts/plugin-check.sh", f"--{target}"], text=True, capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "ok\tstate round-trip" in result.stdout


@pytest.mark.parametrize("target", ScraperRegistry.registered_targets())
def test_client_binding_is_lazy_and_typed(target, tmp_path):
    plugin = ScraperRegistry.get_plugin(target)
    registry = ScraperRegistry(str(tmp_path))
    from core.settings import resolve_settings
    registry.prime_settings(target, resolve_settings(plugin.setting_specs, {}))
    client = registry.get_client(target)
    try:
        assert isinstance(client, ScraperClient)
    finally:
        registry.close_all()


def test_copyable_template_discovers_without_framework_edits(tmp_path, monkeypatch):
    """A copied package is discoverable and passes the same contributor verifier."""
    import core.scrapers as scraper_package
    import core.scrapers.registry as registry_module

    discovery_root = tmp_path / "core" / "scrapers"
    target_dir = discovery_root / "template_store"
    shutil.copytree(Path("src/core/scrapers/_example"), target_dir)

    saved_plugins = dict(ScraperRegistry._plugins)
    saved_discovered = ScraperRegistry._discovered
    saved_path = list(scraper_package.__path__)
    monkeypatch.setattr(registry_module, "__file__", str(discovery_root / "registry.py"))
    scraper_package.__path__.append(str(discovery_root))
    ScraperRegistry._reset()
    try:
        checks = check_plugin("template_store")
        assert "state round-trip" in checks
        assert "lazy client binding" in checks
    finally:
        ScraperRegistry._plugins = saved_plugins
        ScraperRegistry._discovered = saved_discovered
        scraper_package.__path__[:] = saved_path
        for name in tuple(sys.modules):
            if name == "core.scrapers.template_store" or name.startswith(
                "core.scrapers.template_store."
            ):
                sys.modules.pop(name, None)
