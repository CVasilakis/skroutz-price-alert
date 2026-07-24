"""Single-read loading for project-wide configuration."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

from core.exceptions import ConfigFileError
from core.general.settings import general_config_path, resolve_general_settings
from core.infrastructure.persistence import read_json_object, storage_diagnostic
from core.notifications.configuration import NotificationConfig, resolve_notification_config
from core.settings import ResolvedSettings

GENERAL_DISPLAY_PATH = "config/general.json"
GENERAL_PERMISSION_WARNING = (
    "Protect notification URLs: `chmod 600 config/general.json`."
)


@dataclass(frozen=True)
class GeneralConfigLoad:
    """Immutable, section-isolated result of reading ``config/general.json`` once."""

    notifications: NotificationConfig
    settings: ResolvedSettings | None
    settings_error: str | None = None
    permission_warning: str | None = None
    diagnostic: str | None = None


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


def _notification_error(path: str, error: ValueError) -> tuple[str, str]:
    detail = str(error)
    if detail == "Notifications must be an object":
        message = f"`notifications` in `{GENERAL_DISPLAY_PATH}` must be a JSON object."
    elif detail.startswith("Unknown notification settings:"):
        message = (
            f"Remove unsupported notification settings from `{GENERAL_DISPLAY_PATH}`."
        )
    elif detail == 'Notification setting "urls" must be an array':
        message = f"`notifications.urls` in `{GENERAL_DISPLAY_PATH}` must be a JSON array."
    elif detail.startswith("Notification URL at JSON index"):
        message = f"A notification URL in `{GENERAL_DISPLAY_PATH}` must be a string."
    else:
        message = f"Fix notifications in `{GENERAL_DISPLAY_PATH}`."
    return message, _validation_diagnostic(path, "validate notifications", detail)


def _settings_error(path: str, error: ConfigFileError) -> tuple[str, str]:
    detail = str(error)
    if detail == "General settings must be an object":
        message = f"`settings` in `{GENERAL_DISPLAY_PATH}` must be a JSON object."
    elif detail.startswith("Unknown general settings:"):
        message = f"Remove unsupported settings from `{GENERAL_DISPLAY_PATH}`."
    else:
        message = f"Fix settings in `{GENERAL_DISPLAY_PATH}`."
    return message, _validation_diagnostic(path, "validate general settings", detail)


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

    unknown_top = set(document) - {"notifications", "settings"}
    if unknown_top:
        return _document_failure(
            f"Remove unsupported keys from `{GENERAL_DISPLAY_PATH}`.",
            permissions,
            _validation_diagnostic(
                path,
                "validate general configuration",
                f"unknown top-level keys: {', '.join(sorted(unknown_top))}",
            ),
        )

    diagnostics: list[str] = []
    try:
        notifications = resolve_notification_config(document.get("notifications"))
    except ValueError as exc:
        message, diagnostic = _notification_error(path, exc)
        notifications = NotificationConfig(error=message)
        diagnostics.append(diagnostic)

    try:
        settings = resolve_general_settings(document.get("settings", {}))
        settings_error = None
    except ConfigFileError as exc:
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
]
