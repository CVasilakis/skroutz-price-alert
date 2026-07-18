"""The built-in scraper settings, as :class:`SettingSpec` objects.

These are the settings every scraper shares (``execution_interval``, ``log_retention_days``,
``notify_scraping_errors``). They are declared here as specs over the generic engine in
:mod:`core.settings`; a plugin declares only its store-specific specs and the registry
prepends these framework specs. No parallel ``Resolved*`` type or config-check branch
is required.

Import-light: builds on the stdlib-only engine and interval vocabulary, so it stays safe
to call from the shell one-liners and ``--status``.
"""

from typing import Any

from core.settings import SettingSpec, DEFAULT_LOG_RETENTION_DAYS, normalize_retention_days, normalize_bool
from core.scrapers.base.settings.intervals import normalize_interval
from core.scrapers.base.settings.messages import (
    interval_warning_message, retention_warning_message, notify_errors_warning_message,
)


# Built-in setting keys (the JSON keys in a config's ``settings`` block). Exported so
# framework code consuming its own built-in settings references them by name instead of
# a string literal; a plugin's custom setting never needs these.
KEY_INTERVAL = "execution_interval"
KEY_RETENTION = "log_retention_days"
KEY_NOTIFY = "notify_scraping_errors"


def _interval_default(plugin: Any) -> str:
    """The display default for ``execution_interval``: the plugin's cadence as a key.

    Reads the plugin's validated canonical ``default_interval`` value. Translation
    to framework-owned systemd syntax happens only at the timer boundary.
    """
    return plugin.default_interval if plugin is not None else "1h"


# The built-in settings shared by every scraper, in display order. The registry prepends
# them to the plugin's custom specs, so a per-scraper setting needs no framework change.
SPEC_INTERVAL = SettingSpec(
    key=KEY_INTERVAL,
    label="Execution Interval",
    # The settings layer speaks the user's vocabulary: the effective value is the
    # canonical interval key (e.g. "1h"). Translation to a systemd OnCalendar happens at
    # the timer boundary (registry.resolve_schedule), not here.
    normalize=normalize_interval,
    display=lambda canonical: canonical,
    warning=interval_warning_message(),
    default_factory=_interval_default,
    is_unset=lambda raw: not raw,  # an empty/blank interval is unset, not invalid
)

SPEC_RETENTION = SettingSpec(
    key=KEY_RETENTION,
    label="Log Retention",
    normalize=normalize_retention_days,
    display=lambda days: f"{days} day{'s' if days != 1 else ''}",
    warning=retention_warning_message(),
    default=DEFAULT_LOG_RETENTION_DAYS,
)

SPEC_NOTIFY = SettingSpec(
    key=KEY_NOTIFY,
    label="Notify On Errors",
    normalize=normalize_bool,
    display=lambda value: "true" if value else "false",
    warning=notify_errors_warning_message(),
    default=True,  # default ON: notifications enabled unless explicitly disabled
)

BASE_SETTING_SPECS: tuple[SettingSpec, ...] = (SPEC_INTERVAL, SPEC_RETENTION, SPEC_NOTIFY)
