"""Immutable, atomic discovery of checked-in scraper plugins.

Discovery is deliberately all-or-nothing: one malformed descriptor raises and no
scraper runs at all. That is the opposite of how a malformed *config* is handled,
where only its own target is skipped, and the asymmetry is intentional for two
reasons.

The catalog defines identity. CLI flags, systemd unit names, config/state/log
stems, and the TSV bridge the shell scripts read are all derived from it, so a
partially built catalog would silently drop a scraper the user installed and
expects to be running — the one failure mode a price monitor must never have.

A broken descriptor is also a different kind of problem from a broken config.
Descriptors are checked-in code, validated by CI and by the contributor verifier;
one that fails to import or compile means the build is broken, and stopping is
correct. A config is one user's editable file, so a mistake there must cost that
user only that target.
"""

from __future__ import annotations

import importlib
import os
import pkgutil
from collections.abc import Iterable
from pathlib import Path
from types import MappingProxyType

from core.exceptions import PluginDiscoveryError, PluginValidationError
from core.scrapers.framework.compiler import compile_plugin
from core.scrapers.framework.model import RegisteredPlugin


class PluginCatalog:
    """An immutable, atomically constructed collection of checked-in plugins.

    Built once per command and passed down explicitly. Nothing mutates it and no
    module-level registry shadows it, so tests inject a synthetic catalog instead
    of monkeypatching discovery, and two commands in one process can never see
    different plugin sets.
    """

    def __init__(self, plugins: Iterable[RegisteredPlugin]) -> None:
        records = tuple(plugins)
        by_target: dict[str, RegisteredPlugin] = {}
        for plugin in records:
            if plugin.target in by_target:
                raise PluginValidationError(f"Duplicate plugin target '{plugin.target}'.")
            by_target[plugin.target] = plugin
        self._plugins = records
        self._by_target = MappingProxyType(by_target)

    @classmethod
    def discover(
        cls,
        package_dir: str | os.PathLike[str] | None = None,
        *,
        package: str = "core.scrapers.plugins",
    ) -> "PluginCatalog":
        """Import and compile every plugin package under one root, or raise.

        Args:
            package_dir: Filesystem root to scan; defaults to the checked-in
                ``plugins/`` directory beside this framework package.
            package: Import path matching ``package_dir``, used to build each
                descriptor's module name.

        Returns:
            A catalog holding every discovered plugin, compiled and validated.

        Raises:
            PluginDiscoveryError: The root is unusable, a descriptor could not be
                imported, one does not export ``PLUGIN``, or nothing was found. An
                empty root is a failure rather than an empty catalog, because a
                checkout always ships plugins and finding none means the source
                tree is broken.
            PluginValidationError: A descriptor imported but violates its contract.
        """
        root = (
            Path(package_dir)
            if package_dir is not None
            else Path(__file__).resolve().parents[1] / "plugins"
        )
        records: list[RegisteredPlugin] = []
        try:
            if not root.exists():
                raise PluginDiscoveryError(f"Scraper plugin root '{root}' does not exist.")
            if not root.is_dir():
                raise PluginDiscoveryError(f"Scraper plugin root '{root}' is not a directory.")
            if root.stat().st_mode & 0o444 == 0 or not os.access(root, os.R_OK | os.X_OK):
                raise PluginDiscoveryError(f"Scraper plugin root '{root}' is not readable.")
            # Sorted so the catalog order is filesystem-independent: it decides the
            # order of CLI flags, status panels, and the shell TSV rows, all of which
            # are snapshot-tested and would otherwise drift between machines.
            candidates = sorted(pkgutil.iter_modules([str(root)]), key=lambda item: item.name)
            for candidate in candidates:
                target = candidate.name
                if not candidate.ispkg or target.startswith("_"):
                    continue
                module_name = f"{package}.{target}.plugin"
                try:
                    module = importlib.import_module(module_name)
                except Exception as exc:
                    raise PluginDiscoveryError(
                        f"Failed to import scraper descriptor '{module_name}': {exc}"
                    ) from exc
                try:
                    definition = module.PLUGIN
                except AttributeError as exc:
                    raise PluginDiscoveryError(
                        f"Scraper descriptor '{module_name}' does not export PLUGIN."
                    ) from exc
                records.append(
                    compile_plugin(
                        definition,
                        target=target,
                        package=f"{package}.{target}",
                        source_dir=root / target,
                        where=module_name,
                    )
                )
            if not records:
                raise PluginDiscoveryError(
                    f"Scraper plugin root '{root}' contains no production plugins."
                )
        except (PluginDiscoveryError, PluginValidationError):
            raise
        except Exception as exc:
            raise PluginDiscoveryError(
                f"Failed to discover scraper plugins in '{root}': {exc}"
            ) from exc
        return cls(records)

    @property
    def plugins(self) -> tuple[RegisteredPlugin, ...]:
        """Every compiled plugin, in discovery (alphabetical) order."""
        return self._plugins

    @property
    def targets(self) -> tuple[str, ...]:
        """Every plugin's target name, in the same order as :attr:`plugins`."""
        return tuple(self._by_target)

    def get(self, target: str) -> RegisteredPlugin:
        """Return one compiled plugin by target name.

        Raises:
            ValueError: No plugin owns that name. Callers surface this as an
                unsupported-scraper message rather than a lookup failure.
        """
        try:
            return self._by_target[target]
        except KeyError as exc:
            raise ValueError(f"Unsupported plugin: {target}") from exc


__all__ = ["PluginCatalog"]
