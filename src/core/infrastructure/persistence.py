"""Generic JSON and timestamp persistence primitives."""

from __future__ import annotations

import errno
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.exceptions import ConfigFileError


def storage_diagnostic(
    path: str | os.PathLike[str],
    error: BaseException,
    *,
    operation: str,
) -> str:
    """Build a complete, log-only diagnostic for one storage operation."""
    lines = [
        f"Storage operation: {operation}",
        f"Path: {Path(path).resolve()}",
        f"Exception: {type(error).__name__}",
    ]
    error_number = getattr(error, "errno", None)
    if error_number is not None:
        lines.append(f"Errno: {error_number} ({os.strerror(error_number)})")
    if isinstance(error, json.JSONDecodeError):
        lines.append(
            f"JSON location: line {error.lineno}, column {error.colno}, character {error.pos}"
        )
    lines.append(f"Detail: {error}")

    cause = error.__cause__ or error.__context__
    level = 1
    seen = {id(error)}
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        lines.append(f"Cause {level}: {type(cause).__name__}: {cause}")
        cause = cause.__cause__ or cause.__context__
        level += 1
    return "\n".join(lines)


def _display(path: str) -> str:
    return f"`{path}`"


def read_failure_message(display_path: str, error: BaseException) -> str:
    """Classify a low-level read failure into concise presentation wording."""
    if isinstance(error, json.JSONDecodeError):
        return (
            f"Fix JSON in {_display(display_path)} at "
            f"line {error.lineno}, column {error.colno}."
        )
    if isinstance(error, UnicodeError):
        return f"{_display(display_path)} is not valid UTF-8."
    if isinstance(error, OSError) and (
        isinstance(error, PermissionError)
        or error.errno in {errno.EACCES, errno.EPERM, errno.EROFS}
    ):
        return f"Cannot read {_display(display_path)}; check its permissions."
    return f"Cannot read {_display(display_path)}; check the error log."


def save_failure_message(display_path: str, error: BaseException) -> str:
    """Classify a low-level save failure into concise presentation wording."""
    if isinstance(error, OSError) and (
        isinstance(error, PermissionError)
        or error.errno in {errno.EACCES, errno.EPERM, errno.EROFS}
    ):
        return f"Cannot save {_display(display_path)}; check its permissions."
    return f"Cannot save {_display(display_path)}; check the error log."


def read_json_object(
    path: str | os.PathLike[str],
    *,
    required: bool = True,
    display_path: str | None = None,
) -> dict[str, Any] | None:
    """Read one UTF-8 JSON object, optionally treating absence as empty input."""
    source = Path(path)
    shown_path = display_path or os.fspath(path)
    try:
        with source.open(encoding="utf-8") as file:
            document = json.load(file)
    except FileNotFoundError as exc:
        if not required:
            return None
        raise ConfigFileError(
            f"Create missing `{shown_path}` from the plugin example.",
            storage_diagnostic(source, exc, operation="read configuration"),
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigFileError(
            read_failure_message(shown_path, exc),
            storage_diagnostic(source, exc, operation="read configuration"),
        ) from exc
    if not isinstance(document, dict):
        error = TypeError(
            f"top-level JSON value is {type(document).__name__}, expected object"
        )
        raise ConfigFileError(
            f"`{shown_path}` must contain a JSON object.",
            storage_diagnostic(source, error, operation="validate configuration"),
        )
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
