"""Contributor-facing verification for one production plugin package."""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core.exceptions import PluginDependencyError
from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.framework.clients import ClientLoader
from core.scrapers.framework.configuration import TargetConfigLoader
from core.scrapers.framework.migrations import (
    PluginMigrationDeclarationError,
    load_plugin_config_migration_plan,
)
from core.scrapers.framework.model import RegisteredPlugin
from core.scrapers.framework.settings import framework_setting_specs
from core.scrapers.framework.state import JsonStateRepository, StateEntry
from core.scrapers.tooling import SCAFFOLD_TEST_TODO
from core.settings import MISSING

_IMPORT_PROBE = r"""
import importlib
import importlib.util
import json
import pathlib
import sys
import sysconfig

src_root, package, plugin_source, include_migrations = sys.argv[1:]
sys.path.insert(0, src_root)
import core.scrapers.api
parent_name = package.rpartition(".")[0]
parent = importlib.import_module(parent_name)
parent_path = getattr(parent, "__path__", None)
if parent_path is not None and str(pathlib.Path(plugin_source).parent) not in parent_path:
    parent_path.append(str(pathlib.Path(plugin_source).parent))
before = set(sys.modules)
try:
    importlib.import_module(package)
    importlib.import_module(package + ".plugin")
    if include_migrations == "yes":
        importlib.import_module(package + ".migrations")
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


@dataclass(frozen=True)
class ContributorFiles:
    """The contributor-owned files found beside one plugin package.

    Their absence is graded, not uniform: a missing README or example config blocks
    a contribution, while missing tests only warn, so an incomplete package stays
    visible in review without being rejected outright.
    """

    readme: str
    """The package README's text, checked for the documented required sections."""

    warnings: tuple[str, ...]
    """Non-blocking findings, such as a scaffold placeholder left in place."""

    has_tests: bool
    """Whether a target-owned test package exists; recommended, never required."""


@dataclass(frozen=True)
class PluginCheckReport:
    """What the focused verifier checked and what it wants the contributor to see.

    Names the checks that *passed* as well as the warnings, so a contributor can
    confirm a contract was actually exercised rather than silently skipped.
    """

    checks: tuple[str, ...]
    """Labels of the contracts verified, in the order they ran."""

    warnings: tuple[str, ...]
    """Non-blocking findings; a report with warnings still passes."""

    has_tests: bool
    """Whether target-owned tests were found and run."""

    def __contains__(self, label: object) -> bool:
        """Support ``label in report`` so tests assert on a check by name."""
        return label in self.checks


def _check_import_light(package: str, source: Path, *, include_migrations: bool) -> None:
    src_root = Path(__file__).resolve().parents[3]
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
                "yes" if include_migrations else "no",
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
        raise RuntimeError("descriptor discovery imported non-stdlib modules: " + ", ".join(unsafe))


def _check_contributor_files(
    source: Path,
    tests: Path,
    target: str,
    config_schema_version: int,
) -> ContributorFiles:
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
    warnings: list[str] = []
    if not test_modules:
        warnings.append(
            f"plugin {target!r} has no tests/plugins/{target}/test_*.py; "
            "submission is allowed, but scraper behavior is unverified"
        )
    else:
        for test_module in test_modules:
            try:
                contents = test_module.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise RuntimeError(
                    f"plugin {target!r} test module {test_module.name} is unreadable: {exc}"
                ) from exc
            if SCAFFOLD_TEST_TODO in contents:
                warnings.append(
                    f"plugin {target!r} still contains the generated skipped behavior TODO; "
                    "replace it with mocked scraper coverage before submission when practical"
                )
                break
    migrations = source / "migrations.py"
    migration_tests = tests / "test_migrations.py"
    if config_schema_version == 1 and migrations.exists():
        raise RuntimeError(
            f"plugin {target!r} at config schema version 1 must not contain migrations.py"
        )
    if config_schema_version > 1 and not migrations.is_file():
        raise RuntimeError(
            f"plugin {target!r} config schema version {config_schema_version} "
            "requires migrations.py"
        )
    if config_schema_version > 1 and not migration_tests.is_file():
        warnings.append(
            f"plugin {target!r} config schema version {config_schema_version} "
            f"has no tests/plugins/{target}/test_migrations.py; migrations are unverified"
        )
    return ContributorFiles(
        (source / "README.md").read_text(encoding="utf-8"),
        tuple(warnings),
        bool(test_modules),
    )


def _check_migrations(plugin: RegisteredPlugin) -> None:
    try:
        load_plugin_config_migration_plan(plugin)
    except PluginMigrationDeclarationError as exc:
        raise RuntimeError(str(exc)) from exc


def _check_self_contained(source: Path, target: str, registered_targets: frozenset[str]) -> None:
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
                prefix = "core.scrapers.plugins."
                if not module.startswith(prefix):
                    continue
                sibling = module[len(prefix) :].split(".", 1)[0]
                if sibling in registered_targets and sibling != target:
                    raise RuntimeError(
                        f"plugin {target!r} imports sibling plugin {sibling!r} in {path.name}"
                    )


def _check_declaration_imports(source: Path, package: str, target: str) -> None:
    """Keep import-light declarations on the public API and package-local code."""
    for filename in ("__init__.py", "plugin.py", "migrations.py"):
        path = source / filename
        if not path.is_file():
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, UnicodeError, SyntaxError) as exc:
            raise RuntimeError(f"plugin source {filename!r} is unreadable: {exc}") from exc
        for node in ast.walk(tree):
            imports: list[tuple[str, str]] = []
            if isinstance(node, ast.Import):
                imports.extend((alias.name, ast.unparse(node)) for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                rendered = ast.unparse(node)
                if node.level >= 2:
                    raise RuntimeError(
                        f"plugin {target!r} descriptor {filename} uses escaped relative "
                        f"import {rendered!r}; only level-1 plugin-local imports are allowed"
                    )
                if node.level == 1:
                    continue
                if node.module:
                    imports.append((node.module, rendered))
            for module, rendered in imports:
                top_level = module.split(".", 1)[0]
                if (
                    module == "core.scrapers.api"
                    or module == package
                    or module.startswith(package + ".")
                    or top_level in sys.stdlib_module_names
                ):
                    continue
                raise RuntimeError(
                    f"plugin {target!r} descriptor {filename} imports disallowed module "
                    f"{module!r} via {rendered!r}; use the standard library, "
                    "core.scrapers.api, or level-1/package-local helpers"
                )


def check_plugin(
    target: str,
    catalog: PluginCatalog | None = None,
    *,
    repo_root: str | Path | None = None,
) -> PluginCheckReport:
    """Return successful check labels or raise with actionable guidance."""
    catalog = catalog or PluginCatalog.discover()
    plugin = catalog.get(target)
    source = Path(plugin.source_dir)
    root = Path(repo_root) if repo_root is not None else Path(__file__).resolve().parents[4]
    tests = root / "tests" / "plugins" / target
    _check_import_light(
        plugin.package,
        source,
        include_migrations=plugin.config_schema_version > 1,
    )
    contributor_files = _check_contributor_files(
        source, tests, target, plugin.config_schema_version
    )
    _check_self_contained(source, target, frozenset(catalog.targets))
    _check_declaration_imports(source, plugin.package, target)
    _check_migrations(plugin)
    checks = [
        "atomic discovery",
        "isolated import-light descriptor",
        "contributor files",
        "self-contained package",
        "versioned pure migrations",
    ]

    for field in plugin.item_fields:
        if (
            field.default is not MISSING
            and plugin.decode_field(field, field.default) != field.default
        ):
            raise RuntimeError(f"item field {field.key!r} default is not canonical")
    for spec in plugin.setting_specs:
        if spec.default is MISSING:
            continue
        if spec.decode(spec.default) != spec.default:
            raise RuntimeError(f"setting {spec.key!r} default is not canonical")
        if not spec.sensitive and not isinstance(spec.display(spec.default), str):
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
            detail = "; ".join(
                f"item {issue.index}: {issue.message}" for issue in loaded.row_issues
            )
            raise RuntimeError(f"example config has invalid rows: {detail}")
        if not loaded.items:
            raise RuntimeError("example config must contain at least one valid item")
        if not isinstance(example_document, dict):
            raise RuntimeError("example config top level must be an object")
        raw_settings = example_document.get("settings", {})
        raw_items = example_document.get("items", [])
        framework_keys = {spec.key for spec in framework_setting_specs(plugin.default_interval)}
        for spec in plugin.setting_specs:
            if spec.key not in framework_keys and (
                not isinstance(raw_settings, dict) or spec.key not in raw_settings
            ):
                raise RuntimeError(f"example config must demonstrate custom setting {spec.key!r}")
        for field in plugin.item_fields:
            if not isinstance(raw_items, list) or not any(
                isinstance(row, dict) and field.key in row for row in raw_items
            ):
                raise RuntimeError(
                    f"example config must demonstrate custom item field {field.key!r}"
                )
        for item in loaded.items:
            for field in plugin.url_fields:
                try:
                    plugin.canonicalize_url(field, item[field])
                except ValueError as exc:
                    raise RuntimeError(
                        f"example URL field {field.key!r} is not accepted by "
                        f"{target}: {item[field]}: {exc}"
                    ) from exc
        checks.extend(["strict example config", "declared input contracts"])

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
    return PluginCheckReport(
        checks=tuple(checks),
        warnings=contributor_files.warnings,
        has_tests=contributor_files.has_tests,
    )
