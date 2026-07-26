"""Framework-owned target-configuration and scraper-state migration plans."""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Mapping

from core.infrastructure.migration import (
    MigrationPhase,
    MigrationPlan,
    Validator,
)
from core.scrapers.framework.configuration import SCHEMA_VERSION as CONFIG_SCHEMA_VERSION
from core.scrapers.framework.model import RegisteredPlugin
from core.scrapers.framework.state import SCHEMA_VERSION as STATE_SCHEMA_VERSION

TARGET_CONFIG_TRANSITIONS: Mapping[int, tuple[MigrationPhase, ...]] = {}
SCRAPER_STATE_TRANSITIONS: Mapping[int, tuple[MigrationPhase, ...]] = {}


class PluginMigrationDeclarationError(ValueError):
    """An invalid or unreadable optional plugin migration declaration."""


def load_plugin_config_migrations(
    plugin: RegisteredPlugin,
) -> dict[int, MigrationPhase]:
    """Discover and validate one plugin's optional config migration phases."""
    module_name = f"{plugin.package}.migrations"
    try:
        spec = importlib.util.find_spec(module_name)
        if spec is None:
            return {}
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise PluginMigrationDeclarationError(
            f"could not import optional plugin migrations: {exc}"
        ) from exc

    raw = getattr(module, "CONFIG_MIGRATIONS", None)
    if not isinstance(raw, dict):
        raise PluginMigrationDeclarationError(
            "migrations.py must export CONFIG_MIGRATIONS as a dict"
        )
    declared = set(TARGET_CONFIG_TRANSITIONS)
    phases: dict[int, MigrationPhase] = {}
    for version, phase in raw.items():
        if isinstance(version, bool) or not isinstance(version, int) or version < 1:
            raise PluginMigrationDeclarationError(
                "CONFIG_MIGRATIONS keys must be positive integer versions"
            )
        if version not in declared:
            raise PluginMigrationDeclarationError(
                f"plugin migration v{version} targets an undeclared target-config transition"
            )
        if not isinstance(phase, MigrationPhase):
            raise PluginMigrationDeclarationError(
                "CONFIG_MIGRATIONS values must be MigrationPhase instances"
            )
        phases[version] = phase
    return phases


def _combine_target_config_phases(
    private: Mapping[int, MigrationPhase] | None = None,
) -> Mapping[int, tuple[MigrationPhase, ...]]:
    """Append at most one plugin-private phase to each framework transition."""
    private = private or {}
    unknown = set(private) - set(TARGET_CONFIG_TRANSITIONS)
    if unknown:
        raise ValueError(f"plugin migrations target undeclared versions: {sorted(unknown)}")
    return {
        version: phases + ((private[version],) if version in private else ())
        for version, phases in TARGET_CONFIG_TRANSITIONS.items()
    }


def target_config_plan(
    validate_current: Validator,
    private: Mapping[int, MigrationPhase] | None = None,
) -> MigrationPlan:
    if not callable(validate_current):
        raise TypeError("target current-schema validator must be callable")
    return MigrationPlan(
        CONFIG_SCHEMA_VERSION,
        _combine_target_config_phases(private),
        validate_current,
    )


def _state_current(document: dict[str, object]) -> None:
    from core.scrapers.framework.state import JsonStateRepository

    JsonStateRepository.validate_document(document)


SCRAPER_STATE_MIGRATIONS = MigrationPlan(
    STATE_SCHEMA_VERSION,
    SCRAPER_STATE_TRANSITIONS,
    _state_current,
)

__all__ = [
    "load_plugin_config_migrations",
    "PluginMigrationDeclarationError",
    "SCRAPER_STATE_MIGRATIONS",
    "TARGET_CONFIG_TRANSITIONS",
    "target_config_plan",
]
