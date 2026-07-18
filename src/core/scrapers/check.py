"""Contributor-facing verification for one production plugin package."""

from __future__ import annotations

import ast
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from core.exceptions import PluginDependencyError
from core.scrapers.configuration import TargetConfigLoader
from core.scrapers.registry import ClientFactory, PluginCatalog
from core.scrapers.state import JsonStateRepository, StateEntry

HEAVY_IMPORT_ROOTS = frozenset({"tls_client", "bs4", "selenium", "playwright"})


def _imports(path: Path) -> tuple[tuple[str, int], ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend((alias.name, 0) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            found.append((node.module or "", node.level))
    return tuple(found)


def _check_descriptor_imports(path: Path) -> None:
    for module, level in _imports(path):
        root = module.split(".", 1)[0]
        allowed = level == 0 and (
            module == "core.scrapers.api" or root in sys.stdlib_module_names
        )
        if not allowed:
            raise RuntimeError(
                f"descriptor import {module or '<relative>'!r} is not stdlib or core.scrapers.api"
            )


def _check_package_init(path: Path) -> None:
    imports = _imports(path)
    if imports:
        names = ", ".join(module or "<relative>" for module, _level in imports)
        raise RuntimeError(f"package __init__.py must be import-light; found: {names}")


def check_plugin(target: str, catalog: PluginCatalog | None = None) -> list[str]:
    """Return successful check labels or raise with actionable guidance."""
    modules_before_discovery = set(sys.modules)
    catalog = catalog or PluginCatalog.discover()
    plugin = catalog.get(target)
    imported = HEAVY_IMPORT_ROOTS.intersection(
        name.split(".", 1)[0] for name in set(sys.modules) - modules_before_discovery
    )
    if imported:
        raise RuntimeError(
            f"descriptor discovery imported optional dependencies: {', '.join(sorted(imported))}"
        )
    source = Path(plugin.source_dir)
    _check_descriptor_imports(source / "plugin.py")
    _check_package_init(source / "__init__.py")
    checks = ["atomic discovery", "import-light package and descriptor", "metadata"]

    for field in plugin.item_fields:
        if field.decode(field.default) != field.default:
            raise RuntimeError(f"item field {field.key!r} default is not canonical")
    for spec in plugin.setting_specs:
        if spec.decode(spec.default) != spec.default:
            raise RuntimeError(f"setting {spec.key!r} default is not canonical")
        if not isinstance(spec.display(spec.default), str):
            raise RuntimeError(f"setting {spec.key!r} display did not return str")
    checks.append("field and setting codecs")

    example = Path(plugin.example_config_path)
    with tempfile.TemporaryDirectory() as root:
        config_dir = Path(root) / "config"
        state_dir = Path(root) / "state"
        config_dir.mkdir()
        shutil.copy2(example, config_dir / plugin.config_filename)
        loaded = TargetConfigLoader(plugin, str(config_dir)).load()
        if loaded.row_issues:
            detail = "; ".join(f"item {issue.index}: {issue.message}" for issue in loaded.row_issues)
            raise RuntimeError(f"example config has invalid rows: {detail}")
        for item in loaded.items:
            if not plugin.accepts(item.url):
                raise RuntimeError(f"example URL is not accepted by {target}: {item.url}")
        checks.extend(["strict example config", "URL acceptance"])

        state = JsonStateRepository(state_dir / f"{target}.json")
        state.load()
        if loaded.items:
            item = loaded.items[0]
            now = datetime.now(timezone.utc).replace(microsecond=0)
            state.record_priced_check(item.id, 1.0, now)
            state.save()
            reloaded = JsonStateRepository(state_dir / f"{target}.json")
            reloaded.load()
            if reloaded.get(item.id) != StateEntry(1.0, now):
                raise RuntimeError("state round-trip changed values")
        checks.append("state round-trip")

    factory = ClientFactory()
    try:
        factory.create(plugin, loaded.settings)
    except PluginDependencyError as exc:
        raise RuntimeError(f"dependency guidance: {exc}") from exc
    finally:
        factory.close()
    checks.extend(["conventional lazy Client", "clean client shutdown"])
    if plugin.requirements_path:
        checks.append("private dependency guidance")
    return checks
