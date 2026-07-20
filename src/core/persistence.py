"""Generic JSON and timestamp persistence primitives."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.exceptions import ConfigFileError


def read_json_object(
    path: str | os.PathLike[str], *, required: bool = True
) -> dict[str, Any] | None:
    """Read one UTF-8 JSON object, optionally treating absence as empty input."""
    source = Path(path)
    if not source.exists() and not required:
        return None
    try:
        with source.open(encoding="utf-8") as file:
            document = json.load(file)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigFileError(f"Config file '{source}' is invalid or unreadable: {exc}") from exc
    if not isinstance(document, dict):
        raise ConfigFileError(f"Config file '{source}' must contain an object")
    return document


def write_json_atomically(path: str | os.PathLike[str], data: object) -> None:
    """Serialize JSON through a sibling temporary file and atomic replace."""
    destination = os.fspath(path)
    temporary = destination + ".tmp"
    with open(temporary, mode="w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
    os.replace(temporary, destination)


def format_utc(value: datetime) -> str:
    """Serialize an aware datetime as RFC 3339 UTC with a ``Z`` suffix."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_utc(value: object) -> datetime:
    """Parse the persisted RFC 3339 UTC timestamp representation."""
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be RFC 3339 UTC ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ValueError("timestamp must be valid RFC 3339 UTC") from exc
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)
