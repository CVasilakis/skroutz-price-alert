"""Owned migration plans for general configuration and reminder state."""

from __future__ import annotations

from core.general.configuration import (
    SCHEMA_VERSION as GENERAL_CONFIG_SCHEMA_VERSION,
)
from core.general.reminder_state import (
    SCHEMA_VERSION as REMINDER_STATE_SCHEMA_VERSION,
)
from core.schema_migrations.engine import MigrationPlan

GENERAL_CONFIG_MIGRATIONS = MigrationPlan(
    "schema_version",
    GENERAL_CONFIG_SCHEMA_VERSION,
    {},
)
REMINDER_STATE_MIGRATIONS = MigrationPlan(
    "schema_version",
    REMINDER_STATE_SCHEMA_VERSION,
    {},
)

__all__ = ["GENERAL_CONFIG_MIGRATIONS", "REMINDER_STATE_MIGRATIONS"]
