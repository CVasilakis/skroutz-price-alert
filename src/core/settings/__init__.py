"""Canonical home of the generic settings engine."""

from core.settings.messages import unsupported_value_message
from core.settings.model import (
    ResolvedSetting, ResolvedSettings, SettingSpec, SettingStatus, SettingView,
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
from core.settings.resolve import resolve_settings, resolve_spec, setting_view

__all__ = [
    "SettingSpec", "ResolvedSetting", "ResolvedSettings", "SettingStatus", "SettingView",
    "fold_token", "alias_form", "normalize_retention_days", "normalize_bool",
    "DEFAULT_LOG_RETENTION_DAYS", "MIN_LOG_RETENTION_DAYS", "MAX_LOG_RETENTION_DAYS",
    "unsupported_value_message",
    "resolve_spec", "resolve_settings", "setting_view",
]
