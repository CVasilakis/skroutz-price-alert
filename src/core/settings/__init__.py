"""Canonical home of the generic settings engine."""

from core.settings.messages import unsupported_value_message
from core.settings.model import (
    MISSING,
    ResolvedSetting,
    ResolvedSettings,
    SettingSpec,
    SettingStatus,
)
from core.settings.normalizers import (
    DEFAULT_LOG_RETENTION_DAYS,
    MAX_LOG_RETENTION_DAYS,
    MIN_LOG_RETENTION_DAYS,
    alias_form,
    fold_token,
    normalize_bool,
    normalize_retention_days,
)
from core.settings.resolve import (
    SettingsValidationError,
    SettingsValidationProblem,
    resolve_settings,
    resolve_spec,
    validate_settings_block,
)

__all__ = [
    "MISSING",
    "SettingSpec",
    "ResolvedSetting",
    "ResolvedSettings",
    "SettingStatus",
    "SettingsValidationError",
    "SettingsValidationProblem",
    "fold_token",
    "alias_form",
    "normalize_retention_days",
    "normalize_bool",
    "DEFAULT_LOG_RETENTION_DAYS",
    "MIN_LOG_RETENTION_DAYS",
    "MAX_LOG_RETENTION_DAYS",
    "unsupported_value_message",
    "resolve_spec",
    "resolve_settings",
    "validate_settings_block",
]
