"""Project-wide settings: the ``settings`` block of ``config/general.json``.

Declares the general (non-scraper) settings as :class:`SettingSpec` objects and resolves
them with the exact machinery the per-scraper configs use (:func:`resolve_all` with
``plugin=None``), so statuses, defaults and invalid-value handling (warn + default)
behave identically everywhere. Adding a project-wide setting is exactly one new spec
appended to :data:`GENERAL_SETTING_SPECS` - its vocabulary lives in
:mod:`core.general.vocab`, and resolution, defaulting and the panel row need no new code.

Import-light: builds on the stdlib-only :mod:`core.settings` engine and the general
vocabulary, so it is safe to import from ``--status`` and the config panel.
"""

import os

from core.config_constants import GENERAL_CONFIG_FILENAME
from core.settings import ResolvedSettings, SettingSpec, resolve_all, unsupported_value_message
from core.general.vocab import (
    DEFAULT_REMINDER, DEFAULT_REMINDER_DAY, DEFAULT_REMINDER_TIME,
    normalize_reminder, normalize_reminder_day, normalize_reminder_time,
    display_reminder, display_reminder_row,
)



# The JSON keys of the general settings in general.json's ``settings`` block. Exported so
# framework code references them by name instead of a string literal.
KEY_REMINDER = "reminder"
KEY_REMINDER_DAY = "reminder_day"
KEY_REMINDER_TIME = "reminder_time"


def general_config_path(config_dir: str) -> str:
    """Returns the absolute path of the project-wide config file inside ``config_dir``."""
    return os.path.join(config_dir, GENERAL_CONFIG_FILENAME)


def resolve_general_settings(config_dir: str) -> ResolvedSettings:
    """Resolves every project-wide setting against ``config/general.json`` in one read.

    A missing file, a missing key, or an invalid value all degrade to each spec's
    default with the matching ``STATUS_*`` code, exactly like a scraper's settings -
    ``general.json`` is entirely optional.

    Args:
        config_dir (str): The directory holding the config files.

    Returns:
        ResolvedSettings: The resolved settings, queryable by key and as views.
    """
    return resolve_all(GENERAL_SETTING_SPECS, general_config_path(config_dir), plugin=None)


SPEC_REMINDER = SettingSpec(
    key=KEY_REMINDER,
    label="Reminder",
    # The settings layer speaks the user's vocabulary: the effective value is the
    # canonical reminder key (e.g. "1m"). Translation to a week count happens at the
    # scheduling boundary (core.general.reminder), not here.
    normalize=normalize_reminder,
    display=display_reminder_row,
    warning=unsupported_value_message(KEY_REMINDER, display_reminder(DEFAULT_REMINDER)),
    default=DEFAULT_REMINDER,
)

SPEC_REMINDER_DAY = SettingSpec(
    key=KEY_REMINDER_DAY,
    label="Reminder Day",
    normalize=normalize_reminder_day,
    display=lambda name: name,
    warning=unsupported_value_message(KEY_REMINDER_DAY, DEFAULT_REMINDER_DAY),
    default=DEFAULT_REMINDER_DAY,
)

SPEC_REMINDER_TIME = SettingSpec(
    key=KEY_REMINDER_TIME,
    label="Reminder Time",
    normalize=normalize_reminder_time,
    display=lambda hhmm: hhmm,
    warning=unsupported_value_message(KEY_REMINDER_TIME, DEFAULT_REMINDER_TIME),
    default=DEFAULT_REMINDER_TIME,
)

# The project-wide settings, in display order. Adding a general setting is exactly one
# new spec appended here; resolution, defaulting and the panel row need no new code.
GENERAL_SETTING_SPECS: list[SettingSpec] = [SPEC_REMINDER, SPEC_REMINDER_DAY, SPEC_REMINDER_TIME]
