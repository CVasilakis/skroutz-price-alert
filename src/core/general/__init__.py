"""Project-wide settings and services: ``config/general.json``.

The file mirrors the per-scraper config shape - a user-authored ``settings`` object -
plus machine-written top-level state (``last_reminder``), the same user-input/state
split the scraper configs use (the app never writes into ``settings``)::

    {
      "settings": { "reminder": "1 month" },
      "last_reminder": "04-07-2026 13:00:00"
    }

The file is optional: every general setting degrades to its default when the file or
key is missing, and the app creates/patches the file only when it persists state.

Layout:
    * :mod:`~core.general.settings` - the general :class:`SettingSpec` declarations and
      their resolution (via the shared settings machinery, ``plugin=None``). Adding a
      project-wide setting is exactly one spec appended to ``GENERAL_SETTING_SPECS``.
    * :mod:`~core.general.reminder` - the periodic liveness reminder: the Saturday-13:00
      slot grid, the persisted state, and the dispatch service.

The public names below are re-exported here, so consumers import from ``core.general``.
"""

from core.general.settings import (
    GENERAL_CONFIG_FILENAME,
    KEY_REMINDER,
    KEY_REMINDER_DAY,
    KEY_REMINDER_TIME,
    SUPPORTED_REMINDERS,
    DEFAULT_REMINDER,
    DEFAULT_REMINDER_DAY,
    DEFAULT_REMINDER_TIME,
    normalize_reminder,
    normalize_reminder_day,
    normalize_reminder_time,
    weekday_index,
    time_parts,
    weeks_for,
    display_reminder,
    display_reminder_row,
    reminder_warning_message,
    reminder_day_warning_message,
    reminder_time_warning_message,
    general_config_path,
    resolve_general_settings,
    SPEC_REMINDER,
    SPEC_REMINDER_DAY,
    SPEC_REMINDER_TIME,
    GENERAL_SETTING_SPECS,
)
from core.general.reminder import (
    REMINDER_TARGET,
    LAST_REMINDER_FIELD,
    most_recent_slot,
    next_due_slot,
    ReminderService,
)

__all__ = [
    # settings
    "GENERAL_CONFIG_FILENAME", "KEY_REMINDER", "KEY_REMINDER_DAY", "KEY_REMINDER_TIME",
    "SUPPORTED_REMINDERS", "DEFAULT_REMINDER", "DEFAULT_REMINDER_DAY", "DEFAULT_REMINDER_TIME",
    "normalize_reminder", "normalize_reminder_day", "normalize_reminder_time",
    "weekday_index", "time_parts", "weeks_for", "display_reminder", "display_reminder_row",
    "reminder_warning_message", "reminder_day_warning_message", "reminder_time_warning_message",
    "general_config_path", "resolve_general_settings",
    "SPEC_REMINDER", "SPEC_REMINDER_DAY", "SPEC_REMINDER_TIME", "GENERAL_SETTING_SPECS",
    # reminder
    "REMINDER_TARGET", "LAST_REMINDER_FIELD", "most_recent_slot", "next_due_slot",
    "ReminderService",
]
