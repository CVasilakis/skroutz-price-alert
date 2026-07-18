"""Contributor-facing verification for one scraper plugin package."""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from core.exceptions import PluginDependencyError
from core.scrapers.configuration import TargetConfigLoader
from core.scrapers.registry import ScraperRegistry
from core.scrapers.state import JsonStateRepository

HEAVY_IMPORT_ROOTS = frozenset({"tls_client", "bs4", "selenium", "playwright"})


def check_plugin(target: str) -> list[str]:
    """Return successful check labels or raise with an actionable failure."""
    modules_before_discovery = set(sys.modules)
    plugin = ScraperRegistry.get_plugin(target)
    imported = HEAVY_IMPORT_ROOTS.intersection(
        name.split(".", 1)[0] for name in set(sys.modules) - modules_before_discovery
    )
    if imported:
        raise RuntimeError(
            f"descriptor discovery imported optional dependencies: {', '.join(sorted(imported))}"
        )
    checks = ["fresh discovery", "import-light descriptor", "metadata"]
    example = Path(plugin.example_config_path)
    readme = Path(plugin.source_dir) / "README.md"
    if not example.is_file():
        raise RuntimeError(f"missing example config: {example}")
    if not readme.is_file() or not readme.read_text(encoding="utf-8").strip():
        raise RuntimeError(f"missing or empty README: {readme}")
    checks.append("README")
    for field in plugin.item_fields:
        field.decode(field.default)
    for spec in plugin.setting_specs:
        spec.decode(spec.default)
        if not isinstance(spec.display(spec.default), str):
            raise RuntimeError(f"setting {spec.key!r} display did not return str")
    checks.append("field and setting codecs")

    with tempfile.TemporaryDirectory() as root:
        config_dir = Path(root) / "config"
        state_dir = Path(root) / "state"
        config_dir.mkdir()
        shutil.copy2(example, config_dir / plugin.config_filename)
        loaded = TargetConfigLoader(plugin, str(config_dir), str(state_dir)).load()
        example_settings = loaded.settings
        if loaded.row_issues:
            detail = "; ".join(f"item {issue.index}: {issue.message}" for issue in loaded.row_issues)
            raise RuntimeError(f"example config has invalid rows: {detail}")
        for item in loaded.items:
            if ScraperRegistry.plugin_for_url(item.url) != plugin:
                raise RuntimeError(f"example URL did not route back to {target}: {item.url}")
        checks.extend(["example config", "URL routing"])
        if loaded.items:
            item = loaded.items[0]
            now = datetime.now(timezone.utc).replace(microsecond=0)
            loaded.state.update_item(item, last_price=1.0, last_checked=now)
            loaded.state.save()
            reloaded = JsonStateRepository(state_dir / f"{target}.json")
            reloaded.load()
            if reloaded.state_for(item.id) != (1.0, now):
                raise RuntimeError("state round-trip changed values")
        checks.append("state round-trip")

    registry = ScraperRegistry(str(example.parent))
    registry.prime_settings(target, example_settings)
    try:
        client = registry.get_client(target)
    except PluginDependencyError as exc:
        raise RuntimeError(f"dependency guidance: {exc}") from exc
    finally:
        registry.close_all()
    checks.append("lazy client binding")
    return checks
