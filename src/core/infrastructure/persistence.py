"""Generic JSON and timestamp persistence primitives."""

from __future__ import annotations

import errno
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import messages
from core.exceptions import ConfigFileError


class AtomicReplacementError(OSError):
    """A durable replacement failure that records whether replacement occurred."""

    def __init__(
        self,
        detail: str,
        *,
        destination_replaced: bool,
        error_number: int | None,
    ) -> None:
        self.destination_replaced = destination_replaced
        super().__init__(detail)
        self.errno = error_number


def _safe_absolute_path(path: str | os.PathLike[str]) -> str:
    """Return an absolute diagnostic path without masking an active failure."""
    try:
        return str(Path(path).resolve())
    except (OSError, RuntimeError):
        return os.path.abspath(os.fspath(path))


def storage_diagnostic(
    path: str | os.PathLike[str],
    error: BaseException,
    *,
    operation: str,
) -> str:
    """Build a complete, log-only diagnostic for one storage operation."""
    lines = [
        f"Storage operation: {operation}",
        f"Path: {_safe_absolute_path(path)}",
        f"Exception: {type(error).__name__}",
    ]
    error_number = getattr(error, "errno", None)
    if isinstance(error_number, int):
        try:
            error_name = os.strerror(error_number)
        except (OverflowError, ValueError):
            error_name = "unknown error"
        lines.append(f"Errno: {error_number} ({error_name})")
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


def read_failure_message(display_path: str, error: BaseException) -> str:
    """Classify a low-level read failure into concise presentation wording."""
    if isinstance(error, json.JSONDecodeError):
        return messages.malformed_json(display_path, error.lineno, error.colno)
    if isinstance(error, UnicodeError):
        return messages.invalid_utf8(display_path)
    if isinstance(error, OSError) and (
        isinstance(error, PermissionError)
        or error.errno in {errno.EACCES, errno.EPERM, errno.EROFS}
    ):
        return messages.storage_read_permission(display_path)
    return messages.storage_read_failed(display_path)


def save_failure_message(display_path: str, error: BaseException) -> str:
    """Classify a low-level save failure into concise presentation wording."""
    if isinstance(error, OSError) and (
        isinstance(error, PermissionError)
        or error.errno in {errno.EACCES, errno.EPERM, errno.EROFS}
    ):
        return messages.storage_save_permission(display_path)
    return messages.storage_save_failed(display_path)


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
            messages.missing_config(shown_path),
            storage_diagnostic(source, exc, operation="read configuration"),
        ) from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigFileError(
            read_failure_message(shown_path, exc),
            storage_diagnostic(source, exc, operation="read configuration"),
        ) from exc
    if not isinstance(document, dict):
        error = TypeError(f"top-level JSON value is {type(document).__name__}, expected object")
        raise ConfigFileError(
            messages.json_object_required(shown_path),
            storage_diagnostic(source, error, operation="validate configuration"),
        )
    return document


def fsync_directory(path: str | os.PathLike[str]) -> None:
    """Persist directory entries and metadata for one existing directory."""
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def commit_atomic_replacement(
    destination: str | os.PathLike[str],
    temporary: str | os.PathLike[str],
) -> None:
    """Durably commit one already-written sibling temporary file."""
    destination_path = Path(destination)
    temporary_path = Path(temporary)
    replaced = False
    try:
        descriptor = os.open(temporary_path, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary_path, destination_path)
        replaced = True
        fsync_directory(destination_path.parent)
    except OSError as exc:
        if not replaced:
            try:
                temporary_path.unlink()
            except OSError:
                pass
        stage = (
            "destination was replaced but its directory could not be made durable"
            if replaced
            else "destination was not replaced"
        )
        raise AtomicReplacementError(
            f"atomic replacement failed ({stage}): {exc}",
            destination_replaced=replaced,
            error_number=exc.errno,
        ) from exc
    except BaseException:
        if not replaced:
            try:
                temporary_path.unlink()
            except OSError:
                pass
        raise


def write_json_atomically(path: str | os.PathLike[str], data: object) -> None:
    """Serialize JSON through a sibling temporary file and atomic replace."""
    destination = os.fspath(path)
    temporary = destination + ".tmp"
    try:
        with open(temporary, mode="w", encoding="utf-8") as file:
            json.dump(data, file, indent=2)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    commit_atomic_replacement(destination, temporary)


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
