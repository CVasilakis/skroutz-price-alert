"""Single-read loading for project-wide configuration."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

from core import messages
from core.exceptions import ConfigFileError
from core.general.settings import (
    GeneralSettingsConfigError,
    general_config_path,
    resolve_general_settings,
)
from core.infrastructure.persistence import read_json_object, storage_diagnostic
from core.notifications.configuration import (
    NotificationConfig,
    NotificationValidationError,
    NotificationValidationProblem,
    resolve_notification_config,
)
from core.settings import ResolvedSettings, SettingsValidationProblem

GENERAL_DISPLAY_PATH = "config/general.json"
GENERAL_PERMISSION_WARNING = "Protect notification URLs: `chmod 600 config/general.json`."
SCHEMA_VERSION = 1


@dataclass(frozen=True)
class GeneralConfigLoad:
    """Immutable, section-isolated result of reading ``config/general.json`` once."""

    notifications: NotificationConfig
    settings: ResolvedSettings | None
    settings_error: str | None = None
    permission_warning: str | None = None
    diagnostic: str | None = None
    diagnostic_saved: bool | None = None


def _permission_warning(path: str) -> str | None:
    try:
        mode = stat.S_IMODE(Path(path).stat().st_mode)
    except OSError:
        return None
    return GENERAL_PERMISSION_WARNING if mode & 0o077 else None


def _document_failure(
    message: str,
    permission_warning: str | None,
    diagnostic: str | None = None,
) -> GeneralConfigLoad:
    return GeneralConfigLoad(
        notifications=NotificationConfig(error=message),
        settings=None,
        settings_error=message,
        permission_warning=permission_warning,
        diagnostic=diagnostic,
    )


def _validation_diagnostic(path: str, operation: str, detail: str) -> str:
    return storage_diagnostic(path, ValueError(detail), operation=operation)


def _notification_error(path: str, error: NotificationValidationError) -> tuple[str, str]:
    detail = str(error)
    if error.problem is NotificationValidationProblem.NOT_OBJECT:
        message = messages.notifications_object_required(GENERAL_DISPLAY_PATH)
    elif error.problem is NotificationValidationProblem.UNKNOWN:
        message = messages.unsupported_notification_settings(GENERAL_DISPLAY_PATH)
    elif error.problem is NotificationValidationProblem.URLS_NOT_ARRAY:
        message = messages.notification_urls_array_required(GENERAL_DISPLAY_PATH)
    elif error.problem is NotificationValidationProblem.URL_NOT_STRING:
        message = messages.notification_url_string_required(GENERAL_DISPLAY_PATH)
    else:
        message = messages.notifications_invalid(GENERAL_DISPLAY_PATH)
    return message, _validation_diagnostic(path, "validate notifications", detail)


def _settings_error(path: str, error: GeneralSettingsConfigError) -> tuple[str, str]:
    detail = str(error)
    if error.problem is SettingsValidationProblem.NOT_OBJECT:
        message = messages.settings_object_required(GENERAL_DISPLAY_PATH)
    elif error.problem is SettingsValidationProblem.UNKNOWN:
        message = messages.unsupported_settings(GENERAL_DISPLAY_PATH)
    else:
        message = messages.settings_invalid(GENERAL_DISPLAY_PATH)
    return message, _validation_diagnostic(path, "validate general settings", detail)


def validate_general_document(document: dict[str, object]) -> None:
    """Validate the complete current general-config schema without I/O."""
    unknown_top = set(document) - {"schema_version", "notifications", "settings"}
    if unknown_top:
        raise ValueError(f"unknown top-level keys: {', '.join(sorted(unknown_top))}")
    version = document.get("schema_version")
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        raise ValueError(f"schema_version must be {SCHEMA_VERSION}")
    resolve_notification_config(document.get("notifications"))
    resolve_general_settings(document.get("settings", {}))


def load_general_config(config_dir: str) -> GeneralConfigLoad:
    """Read and independently resolve the general notification and settings sections."""
    path = general_config_path(config_dir)
    permissions = _permission_warning(path)
    try:
        document = read_json_object(
            path,
            required=False,
            display_path=GENERAL_DISPLAY_PATH,
        )
    except ConfigFileError as exc:
        return _document_failure(str(exc), permissions, exc.diagnostic_detail)

    if document is None:
        return GeneralConfigLoad(
            notifications=NotificationConfig(),
            settings=resolve_general_settings(None),
        )

    unknown_top = set(document) - {"schema_version", "notifications", "settings"}
    if unknown_top:
        return _document_failure(
            messages.unsupported_config_keys(GENERAL_DISPLAY_PATH),
            permissions,
            _validation_diagnostic(
                path,
                "validate general configuration",
                f"unknown top-level keys: {', '.join(sorted(unknown_top))}",
            ),
        )

    version = document.get("schema_version")
    if isinstance(version, bool) or version != SCHEMA_VERSION:
        return _document_failure(
            messages.config_schema_version_invalid(GENERAL_DISPLAY_PATH, SCHEMA_VERSION),
            permissions,
            _validation_diagnostic(
                path,
                "validate general configuration",
                f"schema_version must be {SCHEMA_VERSION}",
            ),
        )

    diagnostics: list[str] = []
    try:
        notifications = resolve_notification_config(document.get("notifications"))
    except NotificationValidationError as exc:
        message, diagnostic = _notification_error(path, exc)
        notifications = NotificationConfig(error=message)
        diagnostics.append(diagnostic)

    try:
        settings = resolve_general_settings(document.get("settings", {}))
        settings_error = None
    except GeneralSettingsConfigError as exc:
        settings_error, diagnostic = _settings_error(path, exc)
        settings = None
        diagnostics.append(diagnostic)

    return GeneralConfigLoad(
        notifications=notifications,
        settings=settings,
        settings_error=settings_error,
        permission_warning=permissions,
        diagnostic="\n\n".join(diagnostics) or None,
    )


__all__ = [
    "GENERAL_DISPLAY_PATH",
    "GENERAL_PERMISSION_WARNING",
    "GeneralConfigLoad",
    "load_general_config",
    "validate_general_document",
]
