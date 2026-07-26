"""Pure, document-local JSON schema migration primitives."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, TypeAlias

JsonObject: TypeAlias = dict[str, Any]
Validator: TypeAlias = Callable[[JsonObject], None]
Transform: TypeAlias = Callable[[JsonObject], JsonObject]


class MigrationError(ValueError):
    """A safe migration failure annotated with its transition and phase."""


@dataclass(frozen=True)
class MigrationPhase:
    """One pure transformation inside a consecutive schema transition."""

    name: str
    transform: Transform

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("migration phase name must be nonblank")
        if not callable(self.transform):
            raise TypeError("migration phase transform must be callable")


@dataclass(frozen=True)
class MigrationPlan:
    """A contiguous migration chain ending at one current schema version."""

    current_version: int
    transitions: Mapping[int, tuple[MigrationPhase, ...]]
    validate_current: Validator

    def __post_init__(self) -> None:
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
        if not callable(self.validate_current):
            raise TypeError("current-schema validator must be callable")
        object.__setattr__(self, "transitions", MappingProxyType(copied))


def schema_version(document: JsonObject) -> int:
    """Return a strict positive integer document-local schema version."""
    value = document.get("schema_version")
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise MigrationError("schema_version must be a positive integer")
    return value


def migrate_document(document: JsonObject, plan: MigrationPlan) -> JsonObject:
    """Migrate a defensive copy through every consecutive transition."""
    if not isinstance(document, dict):
        raise MigrationError("top-level JSON value must be an object")
    current = schema_version(document)
    if current > plan.current_version:
        raise MigrationError(
            f"schema version {current} is newer than supported version {plan.current_version}"
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
                if schema_version(candidate) != current:
                    raise MigrationError("migration phases must not change schema_version")
            except Exception as exc:
                raise MigrationError(
                    f"v{current} to v{current + 1} phase {phase.name!r} failed: {exc}"
                ) from exc
            migrated = copy.deepcopy(candidate)
        current += 1
        migrated["schema_version"] = current

    try:
        plan.validate_current(copy.deepcopy(migrated))
    except Exception as exc:
        raise MigrationError(
            f"current-schema validation at v{plan.current_version} failed: {exc}"
        ) from exc
    return migrated


__all__ = [
    "JsonObject",
    "MigrationError",
    "MigrationPhase",
    "MigrationPlan",
    "migrate_document",
    "schema_version",
    "Validator",
]
