"""Project-wide settings: the ``settings`` block of ``config/general.json``.

Declares the general (non-scraper) settings as :class:`SettingSpec` objects and resolves
them with the same strict machinery as per-scraper configs, so statuses, defaults,
and invalid-value handling (warn + default)
behave identically everywhere. Adding a project-wide setting is exactly one new spec
appended to :data:`GENERAL_SETTING_SPECS` - its vocabulary lives in
:mod:`core.general.vocab`, and resolution, defaulting and the panel row need no new code.

Import-light: builds on the stdlib-only :mod:`core.settings` engine and the general
vocabulary, so it is safe to import from the status command and the config panel.
"""

import os

from core.exceptions import ConfigFileError
from core.general.vocab import (
    DEFAULT_REMINDER,
    DEFAULT_REMINDER_DAY,
    DEFAULT_REMINDER_TIME,
    display_reminder,
    display_reminder_row,
    normalize_reminder,
    normalize_reminder_day,
    normalize_reminder_time,
)
from core.settings import (
    ResolvedSettings,
    SettingSpec,
    SettingsValidationError,
    SettingsValidationProblem,
    resolve_settings,
    unsupported_value_message,
    validate_settings_block,
)

GENERAL_CONFIG_FILENAME = "general.json"

# The JSON keys of the general settings in general.json's ``settings`` block. Exported so
# general-feature consumers reference them by name instead of repeating string literals.
KEY_REMINDER = "reminder"
KEY_REMINDER_DAY = "reminder_day"
KEY_REMINDER_TIME = "reminder_time"


class GeneralSettingsConfigError(ConfigFileError):
    """A typed general-settings failure retaining ConfigFileError compatibility."""

    def __init__(
        self,
        problem: SettingsValidationProblem,
        detail: str,
    ) -> None:
        self.problem = problem
        super().__init__(detail)


def general_config_path(config_dir: str) -> str:
    """Returns the absolute path of the project-wide config file inside ``config_dir``."""
    return os.path.join(config_dir, GENERAL_CONFIG_FILENAME)


def resolve_general_settings(block: object | None) -> ResolvedSettings:
    """Resolve the already-loaded ``settings`` section without performing file I/O."""
    if block is None:
        return resolve_settings(GENERAL_SETTING_SPECS, None)
    try:
        return validate_settings_block(GENERAL_SETTING_SPECS, block)
    except SettingsValidationError as exc:
        message = str(exc)
        if exc.problem is SettingsValidationProblem.NOT_OBJECT:
            message = "General settings must be an object"
        elif exc.problem is SettingsValidationProblem.UNKNOWN:
            message = "Unknown general settings:" + message.removeprefix("unknown settings:")
        raise GeneralSettingsConfigError(exc.problem, message) from exc


def _decode(normalizer, raw):
    value = normalizer(raw)
    if value is None:
        raise ValueError("unsupported value")
    return value


SPEC_REMINDER = SettingSpec(
    key=KEY_REMINDER,
    label="Reminder",
    # The settings layer speaks the user's vocabulary: the effective value is the
    # canonical reminder key (e.g. "1m"). Translation to a week count happens at the
    # scheduling boundary (core.general.reminder), not here.
    decode=lambda raw: _decode(normalize_reminder, raw),
    display=display_reminder_row,
    warning=unsupported_value_message(KEY_REMINDER, display_reminder(DEFAULT_REMINDER)),
    default=DEFAULT_REMINDER,
)

SPEC_REMINDER_DAY = SettingSpec(
    key=KEY_REMINDER_DAY,
    label="Reminder Day",
    decode=lambda raw: _decode(normalize_reminder_day, raw),
    display=lambda name: name,
    warning=unsupported_value_message(KEY_REMINDER_DAY, DEFAULT_REMINDER_DAY),
    default=DEFAULT_REMINDER_DAY,
)

SPEC_REMINDER_TIME = SettingSpec(
    key=KEY_REMINDER_TIME,
    label="Reminder Time",
    decode=lambda raw: _decode(normalize_reminder_time, raw),
    display=lambda hhmm: hhmm,
    warning=unsupported_value_message(KEY_REMINDER_TIME, DEFAULT_REMINDER_TIME),
    default=DEFAULT_REMINDER_TIME,
)

# The project-wide settings, in display order. Adding a general setting is exactly one
# new spec appended here; resolution, defaulting and the panel row need no new code.
GENERAL_SETTING_SPECS: list[SettingSpec] = [SPEC_REMINDER, SPEC_REMINDER_DAY, SPEC_REMINDER_TIME]
