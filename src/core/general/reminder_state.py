"""Schema-v1 machine state for periodic reminders.

Remembers only when the last reminder was sent, which is enough to decide whether
one is due. Versioned independently of scraper state and target configuration, so
a change to the reminder's own format never forces a migration of anything else.

Malformed or unreadable existing state is preserved rather than replaced: the
stored timestamp is the only thing preventing a reminder from being re-sent, so
overwriting a file that could not be understood would spam the user. Such a run
declines to write and says so.
"""

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
    """Why an existing reminder state could not be used, and must be preserved."""

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
        """Whether this state may be rewritten.

        False whenever the existing file could not be understood, which is what
        keeps a bad read from destroying the timestamp it failed to parse.
        """
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
        """Read the stored timestamp, reporting problems instead of raising.

        A missing file is a normal first run and yields an empty, writable
        snapshot. Anything unreadable or malformed yields a non-writable snapshot
        carrying the reason, so the caller can report it without losing the file.
        """
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

        try:
            validate_reminder_state_document(loaded)
        except (TypeError, ValueError):
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
        """Persist the reminder slot atomically, refusing to clobber bad state.

        Takes the snapshot it is updating rather than re-reading, so the decision
        that the file was safe to replace is made once, from the same bytes being
        written over.

        Raises:
            ReminderStatePreservationError: The existing state was unreadable or
                malformed and must be kept.
            ReminderStateWriteError: The write itself failed.
        """
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


def validate_reminder_state_document(document: object) -> None:
    """Validate reminder-state structure independently of timestamp policy."""
    if not isinstance(document, dict):
        raise ValueError("top level must be an object")
    version = document.get("schema_version")
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    unknown = set(document) - STATE_KEYS
    if unknown:
        raise ValueError(f"unknown top-level keys: {', '.join(sorted(unknown))}")


__all__ = [
    "ReminderStatePreservationError",
    "ReminderStateProblem",
    "ReminderStateRepository",
    "ReminderStateSnapshot",
    "ReminderStateWriteError",
    "general_state_path",
    "validate_reminder_state_document",
]
