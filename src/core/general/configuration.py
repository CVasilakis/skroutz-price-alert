"""Single-read loading for project-wide configuration."""

from __future__ import annotations

import stat
from dataclasses import dataclass
from pathlib import Path

from core.exceptions import ConfigFileError
from core.general.notifications import NotificationConfig, resolve_notification_config
from core.general.settings import general_config_path, resolve_general_settings
from core.infrastructure.persistence import read_json_object
from core.settings import ResolvedSettings

GENERAL_PERMISSION_WARNING = (
    "Notification URLs may contain credentials and config/general.json is accessible "
    "to group or other users. Run `chmod 600 config/general.json`."
)


@dataclass(frozen=True)
class GeneralConfigLoad:
    """Immutable, section-isolated result of reading ``config/general.json`` once."""

    notifications: NotificationConfig
    settings: ResolvedSettings | None
    settings_error: str | None = None
    permission_warning: str | None = None


def _permission_warning(path: str) -> str | None:
    try:
        mode = stat.S_IMODE(Path(path).stat().st_mode)
    except OSError:
        return None
    return GENERAL_PERMISSION_WARNING if mode & 0o077 else None


def _document_failure(message: str, permission_warning: str | None) -> GeneralConfigLoad:
    return GeneralConfigLoad(
        notifications=NotificationConfig(error=message),
        settings=None,
        settings_error=message,
        permission_warning=permission_warning,
    )


def load_general_config(config_dir: str) -> GeneralConfigLoad:
    """Read and independently resolve the general notification and settings sections."""
    path = general_config_path(config_dir)
    permissions = _permission_warning(path)
    try:
        document = read_json_object(path, required=False)
    except ConfigFileError as exc:
        return _document_failure(str(exc), permissions)

    if document is None:
        return GeneralConfigLoad(
            notifications=NotificationConfig(),
            settings=resolve_general_settings(None),
        )

    unknown_top = set(document) - {"notifications", "settings"}
    if unknown_top:
        return _document_failure(
            f"Unknown general config keys: {', '.join(sorted(unknown_top))}",
            permissions,
        )

    try:
        notifications = resolve_notification_config(document.get("notifications"))
    except ValueError as exc:
        notifications = NotificationConfig(error=str(exc))

    try:
        settings = resolve_general_settings(document.get("settings", {}))
        settings_error = None
    except ConfigFileError as exc:
        settings = None
        settings_error = str(exc)

    return GeneralConfigLoad(
        notifications=notifications,
        settings=settings,
        settings_error=settings_error,
        permission_warning=permissions,
    )


__all__ = ["GENERAL_PERMISSION_WARNING", "GeneralConfigLoad", "load_general_config"]
