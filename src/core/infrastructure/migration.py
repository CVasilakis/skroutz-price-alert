"""Pure, document-local JSON schema migration primitives."""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
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
    validate_input: Validator = lambda _document: None
    validate_output: Validator = lambda _document: None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("migration phase name must be nonblank")
        if not all(
            callable(value) for value in (self.transform, self.validate_input, self.validate_output)
        ):
            raise TypeError("migration phase callbacks must be callable")


@dataclass(frozen=True)
class MigrationTransition:
    """The ordered phases for exactly one vN to vN+1 transition."""

    from_version: int
    phases: tuple[MigrationPhase, ...] = ()

    def __post_init__(self) -> None:
        if (
            isinstance(self.from_version, bool)
            or not isinstance(self.from_version, int)
            or self.from_version < 1
        ):
            raise ValueError("migration source version must be a positive integer")
        if not isinstance(self.phases, tuple):
            raise TypeError("migration phases must be a tuple")


@dataclass(frozen=True)
class MigrationPlan:
    """A contiguous migration chain ending at one current schema version."""

    current_version: int
    transitions: Mapping[int, MigrationTransition]
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
        if set(copied) != expected:
            raise ValueError("migration transitions must cover every version before current")
        for version, transition in copied.items():
            if transition.from_version != version:
                raise ValueError("migration transition key does not match its source version")
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
        transition = plan.transitions[current]
        for phase in transition.phases:
            working = copy.deepcopy(migrated)
            before = copy.deepcopy(working)
            try:
                phase.validate_input(copy.deepcopy(working))
                candidate = phase.transform(working)
                if working != before:
                    raise MigrationError("migration engine input was mutated")
                if not isinstance(candidate, dict):
                    raise MigrationError("migration phase must return a JSON object")
                if schema_version(candidate) != current:
                    raise MigrationError("migration phases must not change schema_version")
                phase.validate_output(copy.deepcopy(candidate))
            except MigrationError:
                raise
            except (KeyError, TypeError, ValueError, OverflowError) as exc:
                raise MigrationError(
                    f"v{current} to v{current + 1} phase {phase.name!r} failed: {exc}"
                ) from exc
            migrated = copy.deepcopy(candidate)
        current += 1
        migrated["schema_version"] = current

    try:
        plan.validate_current(copy.deepcopy(migrated))
    except MigrationError:
        raise
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise MigrationError(f"current schema validation failed: {exc}") from exc
    return migrated


def compose_transitions(
    current_version: int,
    common: Sequence[MigrationTransition],
    private: Mapping[int, MigrationPhase] | None = None,
) -> Mapping[int, MigrationTransition]:
    """Compose framework-first and optional plugin-private target phases."""
    by_version = {transition.from_version: transition for transition in common}
    if set(by_version) != set(range(1, current_version)):
        raise ValueError("common target transitions must be contiguous")
    private = private or {}
    unknown = set(private) - set(by_version)
    if unknown:
        raise ValueError(f"plugin migrations target undeclared versions: {sorted(unknown)}")
    return {
        version: MigrationTransition(
            version,
            transition.phases + ((private[version],) if version in private else ()),
        )
        for version, transition in by_version.items()
    }


__all__ = [
    "compose_transitions",
    "JsonObject",
    "MigrationError",
    "MigrationPhase",
    "MigrationPlan",
    "MigrationTransition",
    "migrate_document",
    "schema_version",
    "Validator",
]
