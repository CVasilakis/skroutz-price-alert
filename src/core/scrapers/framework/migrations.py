"""Framework-owned target-configuration and scraper-state migration plans."""

from __future__ import annotations

from collections.abc import Mapping

from core.infrastructure.migration import (
    MigrationPhase,
    MigrationPlan,
    MigrationTransition,
    Validator,
    compose_transitions,
)
from core.scrapers.framework.configuration import SCHEMA_VERSION as CONFIG_SCHEMA_VERSION
from core.scrapers.framework.state import SCHEMA_VERSION as STATE_SCHEMA_VERSION

TARGET_CONFIG_TRANSITIONS: tuple[MigrationTransition, ...] = ()
SCRAPER_STATE_TRANSITIONS: tuple[MigrationTransition, ...] = ()


def target_config_plan(
    validate_current: Validator,
    private: Mapping[int, MigrationPhase] | None = None,
) -> MigrationPlan:
    if not callable(validate_current):
        raise TypeError("target current-schema validator must be callable")
    return MigrationPlan(
        CONFIG_SCHEMA_VERSION,
        compose_transitions(CONFIG_SCHEMA_VERSION, TARGET_CONFIG_TRANSITIONS, private),
        validate_current,
    )


def _state_current(document: dict[str, object]) -> None:
    from core.scrapers.framework.state import JsonStateRepository

    JsonStateRepository.validate_document(document)


SCRAPER_STATE_MIGRATIONS = MigrationPlan(
    STATE_SCHEMA_VERSION,
    {step.from_version: step for step in SCRAPER_STATE_TRANSITIONS},
    _state_current,
)

__all__ = [
    "SCRAPER_STATE_MIGRATIONS",
    "TARGET_CONFIG_TRANSITIONS",
    "target_config_plan",
]
