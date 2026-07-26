"""Framework-owned plans and plugin-private config migration loading."""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Mapping
from typing import cast

from core.schema_migrations.contracts import ConfigMigration
from core.schema_migrations.engine import (
    MigrationPhase,
    MigrationPlan,
)
from core.scrapers.framework.configuration import SCHEMA_VERSION as CONFIG_SCHEMA_VERSION
from core.scrapers.framework.model import RegisteredPlugin
from core.scrapers.framework.state import SCHEMA_VERSION as STATE_SCHEMA_VERSION

TARGET_CONFIG_TRANSITIONS: Mapping[int, tuple[MigrationPhase, ...]] = {}
SCRAPER_STATE_TRANSITIONS: Mapping[int, tuple[MigrationPhase, ...]] = {}


class PluginMigrationDeclarationError(ValueError):
    """An invalid or unreadable optional plugin migration declaration."""


def load_plugin_config_migration_plan(plugin: RegisteredPlugin) -> MigrationPlan:
    """Load one plugin's exact callable chain without exposing engine metadata."""
    if plugin.config_schema_version == 1:
        return MigrationPlan("plugin_schema_version", 1, {})

    module_name = f"{plugin.package}.migrations"
    try:
        spec = importlib.util.find_spec(module_name)
    except Exception as exc:
        raise PluginMigrationDeclarationError(
            f"could not inspect required plugin migrations: {exc}"
        ) from exc
    if spec is None:
        raise PluginMigrationDeclarationError(
            "migrations.py is required when config_schema_version exceeds 1"
        )
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise PluginMigrationDeclarationError(
            f"could not import required plugin migrations: {exc}"
        ) from exc

    raw = getattr(module, "CONFIG_MIGRATIONS", None)
    if not isinstance(raw, dict):
        raise PluginMigrationDeclarationError(
            "migrations.py must export CONFIG_MIGRATIONS as a dict"
        )
    expected = set(range(1, plugin.config_schema_version))
    if set(raw) != expected:
        raise PluginMigrationDeclarationError(
            "CONFIG_MIGRATIONS must contain exactly one transition for every "
            f"source version 1 through {plugin.config_schema_version - 1}"
        )
    phases: dict[int, tuple[MigrationPhase, ...]] = {}
    for version, transform in raw.items():
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise PluginMigrationDeclarationError(
                "CONFIG_MIGRATIONS keys must be positive integer versions"
            )
        if not callable(transform):
            raise PluginMigrationDeclarationError("CONFIG_MIGRATIONS values must be callables")
        phases[version] = (
            MigrationPhase(
                f"plugin config v{version} to v{version + 1}",
                cast(ConfigMigration, transform),
            ),
        )
    return MigrationPlan(
        "plugin_schema_version",
        plugin.config_schema_version,
        phases,
    )


TARGET_CONFIG_MIGRATIONS = MigrationPlan(
    "schema_version",
    CONFIG_SCHEMA_VERSION,
    TARGET_CONFIG_TRANSITIONS,
)


SCRAPER_STATE_MIGRATIONS = MigrationPlan(
    "schema_version",
    STATE_SCHEMA_VERSION,
    SCRAPER_STATE_TRANSITIONS,
)

__all__ = [
    "load_plugin_config_migration_plan",
    "PluginMigrationDeclarationError",
    "SCRAPER_STATE_MIGRATIONS",
    "TARGET_CONFIG_MIGRATIONS",
    "TARGET_CONFIG_TRANSITIONS",
]
