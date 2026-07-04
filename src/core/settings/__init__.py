"""The generic settings engine: model, resolver, and shared normalizers.

This package is the neutral home of the settings *machinery* that both the per-scraper
settings (:mod:`core.scrapers.base.settings`) and the project-wide general settings
(:mod:`core.general.settings`) build on. Keeping it out of the ``scrapers`` subtree lets
non-scraper code (the reminder service, the logger, the shared UI) depend on the engine
without reaching into a scraper package.

A setting is fully described by a single :class:`SettingSpec` (its JSON ``key``,
normalizer, default, display formatter and warning). Resolution reads the config's raw
``settings`` block by key, so adding a setting anywhere is exactly one spec.

Import-light: stdlib only, never a transport/parsing library or the storage stack, so it
is safe to import from ``--status``, the shell one-liners and the config panel.

Layout (all submodules stdlib-only):
    * :mod:`~core.settings.model` - ``ResolvedSetting``, ``ResolvedSettings``,
      ``SettingView`` and the ``STATUS_*`` codes;
    * :mod:`~core.settings.normalizers` - token-folding helpers + retention/bool normalizers;
    * :mod:`~core.settings.messages` - the shared invalid-value message helper;
    * :mod:`~core.settings.resolve` - the ``SettingSpec``, the resolver, and the views.
"""

from core.settings.model import (
    ResolvedSetting,
    ResolvedSettings,
    SettingView,
    STATUS_OK,
    STATUS_DEFAULT,
    STATUS_INVALID,
    STATUS_NOCFG,
)
from core.settings.normalizers import (
    fold_token,
    alias_form,
    normalize_retention_days,
    normalize_bool,
    DEFAULT_LOG_RETENTION_DAYS,
    MIN_LOG_RETENTION_DAYS,
    MAX_LOG_RETENTION_DAYS,
)
from core.settings.messages import unsupported_value_message
from core.settings.resolve import (
    SettingSpec,
    load_settings_block,
    resolve_spec,
    resolve_one,
    resolve_all,
    setting_view,
)

__all__ = [
    # model
    "ResolvedSetting", "ResolvedSettings", "SettingView",
    "STATUS_OK", "STATUS_DEFAULT", "STATUS_INVALID", "STATUS_NOCFG",
    # normalizers
    "fold_token", "alias_form", "normalize_retention_days", "normalize_bool",
    "DEFAULT_LOG_RETENTION_DAYS", "MIN_LOG_RETENTION_DAYS", "MAX_LOG_RETENTION_DAYS",
    # messages
    "unsupported_value_message",
    # resolve
    "SettingSpec", "load_settings_block", "resolve_spec", "resolve_one", "resolve_all",
    "setting_view",
]
