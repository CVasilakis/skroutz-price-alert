"""Contributor-facing verification for one production plugin package."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from core.exceptions import PluginDependencyError
from core.scrapers.configuration import TargetConfigLoader
from core.scrapers.registry import ClientLoader, PluginCatalog
from core.scrapers.settings import framework_setting_specs
from core.scrapers.state import JsonStateRepository, StateEntry

_IMPORT_PROBE = r"""
import importlib
import json
import pathlib
import sys
import sysconfig

src_root, package, plugin_source = sys.argv[1:]
sys.path.insert(0, src_root)
import core.scrapers.api
before = set(sys.modules)
try:
    parent_name = package.rpartition(".")[0]
    parent = importlib.import_module(parent_name)
    parent_path = getattr(parent, "__path__", None)
    if parent_path is not None and str(pathlib.Path(plugin_source).parent) not in parent_path:
        parent_path.append(str(pathlib.Path(plugin_source).parent))
    importlib.import_module(package)
    importlib.import_module(package + ".plugin")
except Exception as exc:
    print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}))
    raise SystemExit(0)

stdlib = pathlib.Path(sysconfig.get_paths()["stdlib"]).resolve()
site_roots = {
    pathlib.Path(value).resolve()
    for key, value in sysconfig.get_paths().items()
    if key in {"purelib", "platlib"}
}
plugin_root = pathlib.Path(plugin_source).resolve()
unsafe = []
for name in sorted(set(sys.modules) - before):
    module = sys.modules.get(name)
    origin = getattr(module, "__file__", None)
    if origin is None:
        continue
    path = pathlib.Path(origin).resolve()
    if path == plugin_root or plugin_root in path.parents:
        continue
    in_stdlib = path == stdlib or stdlib in path.parents
    in_site = any(path == root or root in path.parents for root in site_roots)
    if not in_stdlib or in_site:
        unsafe.append(f"{name} ({path})")
print(json.dumps({"unsafe": unsafe}))
"""


def _check_import_light(package: str, source: Path) -> None:
    src_root = Path(__file__).resolve().parents[2]
    try:
        completed = subprocess.run(
            [
                sys.executable,
                "-I",
                "-c",
                _IMPORT_PROBE,
                str(src_root),
                package,
                str(source),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
        result = json.loads(completed.stdout)
    except (OSError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"isolated descriptor import probe failed: {exc}") from exc
    if result.get("error"):
        raise RuntimeError(f"descriptor import failed: {result['error']}")
    unsafe = result.get("unsafe", [])
    if unsafe:
        raise RuntimeError(
            "descriptor discovery imported non-stdlib modules: " + ", ".join(unsafe)
        )


def _check_contributor_files(source: Path, tests: Path, target: str) -> str:
    for filename in ("README.md", "config.example.json"):
        path = source / filename
        if not path.is_file():
            raise RuntimeError(f"plugin {target!r} is missing contributor file {filename}")
        try:
            if not path.read_text(encoding="utf-8").strip():
                raise RuntimeError(f"plugin {target!r} {filename} must not be empty")
        except (OSError, UnicodeError) as exc:
            raise RuntimeError(f"plugin {target!r} {filename} is unreadable: {exc}") from exc
    test_modules = tuple(tests.glob("test_*.py")) if tests.is_dir() else ()
    if not test_modules:
        raise RuntimeError(
            f"plugin {target!r} requires tests/plugins/{target}/test_*.py"
        )
    return (source / "README.md").read_text(encoding="utf-8")


def _check_self_contained(
    source: Path, target: str, registered_targets: frozenset[str]
) -> None:
    """Reject direct imports from another production plugin package."""
    for path in source.rglob("*.py"):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise RuntimeError(f"plugin source {path.name!r} is unreadable: {exc}") from exc
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.level >= 2 and node.module:
                    sibling = node.module.split(".", 1)[0]
                    if sibling in registered_targets and sibling != target:
                        raise RuntimeError(
                            f"plugin {target!r} imports sibling plugin {sibling!r} in {path.name}"
                        )
                if node.module:
                    modules.append(node.module)
            for module in modules:
                prefix = "core.scrapers."
                if not module.startswith(prefix):
                    continue
                sibling = module[len(prefix):].split(".", 1)[0]
                if sibling in registered_targets and sibling != target:
                    raise RuntimeError(
                        f"plugin {target!r} imports sibling plugin {sibling!r} in {path.name}"
                    )


def check_plugin(
    target: str,
    catalog: PluginCatalog | None = None,
    *,
    repo_root: str | Path | None = None,
) -> list[str]:
    """Return successful check labels or raise with actionable guidance."""
    catalog = catalog or PluginCatalog.discover()
    plugin = catalog.get(target)
    source = Path(plugin.source_dir)
    root = Path(repo_root) if repo_root is not None else source.parents[3]
    tests = root / "tests" / "plugins" / target
    _check_import_light(plugin.package, source)
    readme = _check_contributor_files(source, tests, target)
    _check_self_contained(source, target, frozenset(catalog.targets))
    if "URL" not in readme and "url" not in readme:
        raise RuntimeError("README must document the accepted URL shape")
    if "PriceResult" not in readme and "ListingResult" not in readme:
        raise RuntimeError("README must document its PriceResult or ListingResult contract")
    if plugin.requirements_path and "requirements.txt" not in readme:
        raise RuntimeError("README must document its private requirements.txt")
    checks = [
        "atomic discovery",
        "isolated import-light descriptor",
        "contributor files and tests",
        "self-contained package",
    ]

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
    try:
        example_document = json.loads(example.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"example config is unreadable: {exc}") from exc
    with tempfile.TemporaryDirectory() as root:
        config_dir = Path(root) / "config"
        state_dir = Path(root) / "state"
        config_dir.mkdir()
        shutil.copy2(example, config_dir / plugin.config_filename)
        loaded = TargetConfigLoader(plugin, str(config_dir)).load()
        if loaded.row_issues:
            detail = "; ".join(f"item {issue.index}: {issue.message}" for issue in loaded.row_issues)
            raise RuntimeError(f"example config has invalid rows: {detail}")
        if not loaded.items:
            raise RuntimeError("example config must contain at least one valid item")
        if not isinstance(example_document, dict):
            raise RuntimeError("example config top level must be an object")
        raw_settings = example_document.get("settings", {})
        raw_items = example_document.get("items", [])
        framework_keys = {
            spec.key for spec in framework_setting_specs(plugin.default_interval)
        }
        for spec in plugin.setting_specs:
            if spec.key not in framework_keys and (
                not isinstance(raw_settings, dict) or spec.key not in raw_settings
            ):
                raise RuntimeError(
                    f"example config must demonstrate custom setting {spec.key!r}"
                )
        for field in plugin.item_fields:
            if not isinstance(raw_items, list) or not any(
                isinstance(row, dict) and field.key in row for row in raw_items
            ):
                raise RuntimeError(
                    f"example config must demonstrate custom item field {field.key!r}"
                )
        documented_keys = [
            *(field.key for field in plugin.item_fields),
            *(spec.key for spec in plugin.setting_specs if spec.key not in framework_keys),
        ]
        for key in documented_keys:
            if key not in readme:
                raise RuntimeError(f"README must document custom key {key!r}")
        for item in loaded.items:
            try:
                plugin.canonicalize_url(item.url)
            except ValueError as exc:
                raise RuntimeError(
                    f"example URL is not accepted by {target}: {item.url}: {exc}"
                ) from exc
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

    loader = ClientLoader()
    client = None
    try:
        client = loader.load(plugin, loaded.settings)
    except PluginDependencyError as exc:
        raise RuntimeError(f"dependency guidance: {exc}") from exc
    finally:
        if client is not None:
            client.close()
    checks.extend(["conventional lazy Client", "clean client shutdown"])
    if plugin.requirements_path:
        checks.append("private dependency guidance")
    return checks
