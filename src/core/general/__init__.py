"""Project-wide configuration, settings, and reminder service.

``config/general.json`` is an optional, read-only project configuration document::

    {
      "notifications": { "urls": ["json://localhost"] },
      "settings": { "reminder": "1 month" }
    }

Machine-owned ``last_reminder`` state is persisted separately in
``state/general.json`` as RFC 3339 UTC. Missing configuration uses defaults.

Layout:
    * :mod:`~core.general.configuration` - the single-read, section-isolated general
      configuration result.
    * :mod:`~core.general.vocab` - the tolerant reminder/weekday/time vocabulary and the
      display and parse helpers.
    * :mod:`~core.general.settings` - the general :class:`SettingSpec` declarations and
      their resolution via the shared :mod:`core.settings` machinery.
      Adding a project-wide setting is exactly one spec appended to ``GENERAL_SETTING_SPECS``.
    * :mod:`~core.general.reminder_schedule` - pure reminder-slot calendar arithmetic.
    * :mod:`~core.general.reminder` - reminder policy, recovery, and dispatch orchestration.
    * :mod:`~core.general.reminder_state` - schema-versioned reminder persistence.

This package re-exports only the small **production** surface consumers need. Tests and
tooling that reach for implementation details import them from the owning submodule
(``core.general.vocab``, ``core.general.settings``, ``core.general.reminder_schedule``,
``core.general.reminder``, or ``core.general.reminder_state``).
"""

from core.general.configuration import GeneralConfigLoad, load_general_config
from core.general.reminder import ReminderService
from core.general.settings import general_config_path

__all__ = [
    "GeneralConfigLoad",
    "general_config_path",
    "load_general_config",
    "ReminderService",
]
