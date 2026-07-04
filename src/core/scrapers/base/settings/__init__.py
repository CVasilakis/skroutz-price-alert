"""Scraper configuration settings: the user-tunable ``settings`` block.

Each scraper's JSON config file may carry a top-level ``settings`` object, a sibling of
its item list (e.g. ``products``)::

    {
      "settings": { "execution_interval": "1h" },
      "products": [ ... ]
    }

The settings *model* and resolve machinery are generic and live in :mod:`core.settings`;
this package holds the **scraper-specific** vocabulary and specs (the interval/OnCalendar
map and the built-in ``BASE_SETTING_SPECS``) and re-exports the engine so that
``from core.scrapers.base.settings import X`` keeps working unchanged for both the engine
names and the scraper names. It is imported by the plugin descriptors (and, transitively,
by the lightweight shell one-liners and ``--status``), so it stays **import-light** -
stdlib only, never a transport/parsing library - in line with the BasePlugin contract.

Layout (all submodules stdlib-only):
    * :mod:`core.settings` - the generic ``ResolvedSetting``/``SettingSpec``/resolver;
    * :mod:`~core.scrapers.base.settings.intervals` - interval vocabulary + OnCalendar map;
    * :mod:`~core.scrapers.base.settings.messages` - invalid-value messages;
    * :mod:`~core.scrapers.base.settings.specs` - the built-in ``BASE_SETTING_SPECS`` and
      the ``KEY_*`` constants.

A setting is one ``SettingSpec``:
    A setting is fully described by a single :class:`SettingSpec` (its JSON ``key``,
    normalizer, default, display formatter and warning). There is no parallel settings
    dataclass and no ``from_dict`` to override. Resolution reads the config's raw
    ``settings`` block by key, so adding a setting - built-in or per-scraper - is exactly
    one spec. A scraper exposes a store-specific setting by returning
    ``BASE_SETTING_SPECS + [its specs]`` from ``BasePlugin.get_setting_specs`` (the single
    extension point) and reads its effective value at runtime through the ``self.settings``
    accessor injected into its client and storage.

Read-only by design:
    The ``settings`` block is **authored by the user and never written back by the
    application**. Settings are read - at config/schedule time through ``resolve_one`` and
    at scrape/status time through the registry's ``resolve_all_settings`` - but there is
    deliberately no ``update_setting``/settings write path. Machine-owned runtime state
    (the latest price, a check timestamp) is persisted on *item rows* via ``update_item``,
    not in ``settings`` (see :class:`core.scrapers.base.model.BaseTrackedItem`). Keep new
    settings plain user inputs; do not introduce stateful settings.
"""

from core.settings import (
    ResolvedSetting,
    ResolvedSettings,
    SettingView,
    STATUS_OK,
    STATUS_DEFAULT,
    STATUS_INVALID,
    STATUS_NOCFG,
    fold_token,
    alias_form,
    normalize_retention_days,
    normalize_bool,
    DEFAULT_LOG_RETENTION_DAYS,
    MIN_LOG_RETENTION_DAYS,
    MAX_LOG_RETENTION_DAYS,
    unsupported_value_message,
    SettingSpec,
    load_settings_block,
    resolve_spec,
    resolve_one,
    resolve_all,
    setting_view,
)
from core.scrapers.base.settings.intervals import (
    SUPPORTED_INTERVALS,
    normalize_interval,
    oncalendar_for,
    canonical_for_oncalendar,
)
from core.scrapers.base.settings.messages import (
    interval_warning_message,
    retention_warning_message,
    notify_errors_warning_message,
)
from core.scrapers.base.settings.specs import (
    SPEC_INTERVAL,
    SPEC_RETENTION,
    SPEC_NOTIFY,
    BASE_SETTING_SPECS,
    KEY_INTERVAL,
    KEY_RETENTION,
    KEY_NOTIFY,
)

__all__ = [
    # engine (re-exported from core.settings)
    "ResolvedSetting", "ResolvedSettings", "SettingView",
    "STATUS_OK", "STATUS_DEFAULT", "STATUS_INVALID", "STATUS_NOCFG",
    "fold_token", "alias_form", "normalize_retention_days", "normalize_bool",
    "DEFAULT_LOG_RETENTION_DAYS", "MIN_LOG_RETENTION_DAYS", "MAX_LOG_RETENTION_DAYS",
    "unsupported_value_message",
    "SettingSpec", "load_settings_block", "resolve_spec", "resolve_one", "resolve_all",
    "setting_view",
    # intervals
    "SUPPORTED_INTERVALS", "normalize_interval", "oncalendar_for", "canonical_for_oncalendar",
    # messages
    "interval_warning_message", "retention_warning_message", "notify_errors_warning_message",
    # specs
    "SPEC_INTERVAL", "SPEC_RETENTION", "SPEC_NOTIFY", "BASE_SETTING_SPECS",
    "KEY_INTERVAL", "KEY_RETENTION", "KEY_NOTIFY",
]
