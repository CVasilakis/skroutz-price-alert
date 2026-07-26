"""Owned migration plans for general configuration and reminder state."""

from __future__ import annotations

from core.general.configuration import (
    SCHEMA_VERSION as GENERAL_CONFIG_SCHEMA_VERSION,
)
from core.general.configuration import (
    validate_general_document,
)
from core.general.reminder_state import (
    SCHEMA_VERSION as REMINDER_STATE_SCHEMA_VERSION,
)
from core.general.reminder_state import (
    validate_reminder_state_document,
)
from core.infrastructure.migration import MigrationPlan

GENERAL_CONFIG_MIGRATIONS = MigrationPlan(
    GENERAL_CONFIG_SCHEMA_VERSION,
    {},
    validate_general_document,
)
REMINDER_STATE_MIGRATIONS = MigrationPlan(
    REMINDER_STATE_SCHEMA_VERSION,
    {},
    validate_reminder_state_document,
)

__all__ = ["GENERAL_CONFIG_MIGRATIONS", "REMINDER_STATE_MIGRATIONS"]
