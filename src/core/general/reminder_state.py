"""Schema-v1 machine state for periodic reminders."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

from core.infrastructure.persistence import format_utc, parse_utc, write_json_atomically

SCHEMA_VERSION = 1
LAST_REMINDER_FIELD = "last_reminder"
STATE_KEYS = frozenset({"schema_version", LAST_REMINDER_FIELD})


class ReminderStateProblem(str, Enum):
    UNREADABLE = "unreadable"
    MALFORMED = "malformed"
    INVALID_TIMESTAMP = "invalid_timestamp"


@dataclass(frozen=True)
class ReminderStateSnapshot:
    """One state read, including whether the source is safe to rewrite."""

    document: Mapping[str, object] | None
    last_slot: datetime | None
    problem: ReminderStateProblem | None = None

    @property
    def writable(self) -> bool:
        return self.document is not None


class ReminderStateWriteError(Exception):
    """Raised when a writable reminder state cannot be persisted."""


class ReminderStatePreservationError(ReminderStateWriteError):
    """Raised when existing malformed or unreadable state must be preserved."""


def general_state_path(config_dir: str) -> str:
    """Return the machine-owned reminder state beside the config directory."""
    return str(Path(config_dir).resolve().parent / "state" / "general.json")


class ReminderStateRepository:
    """Read and atomically persist the reminder's independently versioned state."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = str(path)

    def load(self) -> ReminderStateSnapshot:
        path = Path(self.path)
        if not path.exists():
            return ReminderStateSnapshot({}, None)
        try:
            with path.open(encoding="utf-8") as file:
                loaded = json.load(file)
        except OSError:
            return ReminderStateSnapshot(None, None, ReminderStateProblem.UNREADABLE)
        except (json.JSONDecodeError, UnicodeError):
            return ReminderStateSnapshot(None, None, ReminderStateProblem.MALFORMED)

        if (
            not isinstance(loaded, dict)
            or loaded.get("schema_version") != SCHEMA_VERSION
            or set(loaded) - STATE_KEYS
        ):
            return ReminderStateSnapshot(None, None, ReminderStateProblem.MALFORMED)

        raw = loaded.get(LAST_REMINDER_FIELD)
        if raw is None:
            return ReminderStateSnapshot(loaded, None)
        try:
            local_naive = parse_utc(raw).astimezone().replace(tzinfo=None)
        except (TypeError, ValueError):
            return ReminderStateSnapshot(
                loaded,
                None,
                ReminderStateProblem.INVALID_TIMESTAMP,
            )
        return ReminderStateSnapshot(loaded, local_naive)

    def save(self, snapshot: ReminderStateSnapshot, slot: datetime) -> None:
        if not snapshot.writable:
            raise ReminderStatePreservationError("existing reminder state is not safe to replace")
        document = dict(snapshot.document or {})
        document["schema_version"] = SCHEMA_VERSION
        document[LAST_REMINDER_FIELD] = format_utc(slot.astimezone())
        try:
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
            write_json_atomically(self.path, document)
        except (OSError, TypeError, ValueError) as exc:
            raise ReminderStateWriteError(str(exc)) from exc


__all__ = [
    "ReminderStatePreservationError",
    "ReminderStateProblem",
    "ReminderStateRepository",
    "ReminderStateSnapshot",
    "ReminderStateWriteError",
    "general_state_path",
]
