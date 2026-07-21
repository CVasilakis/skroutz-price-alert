"""Immutable, atomic discovery of checked-in scraper plugins."""

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
    """An immutable, atomically constructed collection of checked-in plugins."""

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
        root = (
            Path(package_dir)
            if package_dir is not None
            else Path(__file__).resolve().parents[1] / "plugins"
        )
        records: list[RegisteredPlugin] = []
        try:
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
        except (PluginDiscoveryError, PluginValidationError):
            raise
        except Exception as exc:
            raise PluginDiscoveryError(
                f"Failed to discover scraper plugins in '{root}': {exc}"
            ) from exc
        return cls(records)

    @property
    def plugins(self) -> tuple[RegisteredPlugin, ...]:
        return self._plugins

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(self._by_target)

    def get(self, target: str) -> RegisteredPlugin:
        try:
            return self._by_target[target]
        except KeyError as exc:
            raise ValueError(f"Unsupported plugin: {target}") from exc


__all__ = ["PluginCatalog"]
