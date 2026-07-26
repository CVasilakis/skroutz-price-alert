"""Pure, document-local JSON schema migration machinery."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from core.schema_migrations.contracts import ConfigMigration, JsonObject


class MigrationError(ValueError):
    """A safe migration failure annotated with its transition and phase."""


@dataclass(frozen=True)
class MigrationPhase:
    """One framework-owned transformation inside a consecutive transition."""

    name: str
    transform: ConfigMigration

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("migration phase name must be nonblank")
        if not callable(self.transform):
            raise TypeError("migration phase transform must be callable")


@dataclass(frozen=True)
class MigrationPlan:
    """A contiguous migration chain for one version key."""

    version_key: str
    current_version: int
    transitions: Mapping[int, tuple[MigrationPhase, ...]]

    def __post_init__(self) -> None:
        if not isinstance(self.version_key, str) or not self.version_key.strip():
            raise ValueError("migration version key must be nonblank")
        if (
            isinstance(self.current_version, bool)
            or not isinstance(self.current_version, int)
            or self.current_version < 1
        ):
            raise ValueError("current schema version must be a positive integer")
        copied = dict(self.transitions)
        expected = set(range(1, self.current_version))
        if any(isinstance(version, bool) or not isinstance(version, int) for version in copied):
            raise ValueError("migration transition keys must be integer source versions")
        if set(copied) != expected:
            raise ValueError("migration transitions must cover every version before current")
        for phases in copied.values():
            if not isinstance(phases, tuple):
                raise TypeError("migration transition phases must be a tuple")
            if not all(isinstance(phase, MigrationPhase) for phase in phases):
                raise TypeError("migration transition members must be MigrationPhase instances")
        object.__setattr__(self, "transitions", MappingProxyType(copied))


def document_version(document: JsonObject, version_key: str) -> int:
    """Return a strict positive integer document-local version."""
    value = document.get(version_key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MigrationError(f"{version_key} must be a positive integer")
    return value


def migrate_document(document: JsonObject, plan: MigrationPlan) -> JsonObject:
    """Migrate a defensive copy along exactly one version axis."""
    if not isinstance(document, dict):
        raise MigrationError("top-level JSON value must be an object")
    current = document_version(document, plan.version_key)
    if current > plan.current_version:
        raise MigrationError(
            f"{plan.version_key} {current} is newer than supported version {plan.current_version}"
        )

    migrated = copy.deepcopy(document)
    while current < plan.current_version:
        for phase in plan.transitions[current]:
            working = copy.deepcopy(migrated)
            before = copy.deepcopy(working)
            try:
                candidate = phase.transform(working)
                if working != before:
                    raise MigrationError("migration engine input was mutated")
                if not isinstance(candidate, dict):
                    raise MigrationError("migration phase must return a JSON object")
                if document_version(candidate, plan.version_key) != current:
                    raise MigrationError(f"migration phases must not change {plan.version_key}")
            except Exception as exc:
                raise MigrationError(
                    f"{plan.version_key} v{current} to v{current + 1} "
                    f"phase {phase.name!r} failed: {exc}"
                ) from exc
            migrated = copy.deepcopy(candidate)
        current += 1
        migrated[plan.version_key] = current
    return migrated


__all__ = [
    "MigrationError",
    "MigrationPhase",
    "MigrationPlan",
    "document_version",
    "migrate_document",
]
