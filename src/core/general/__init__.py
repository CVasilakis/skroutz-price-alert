"""Project-wide settings and reminder service.

``config/general.json`` is an optional, read-only project configuration document::

    {
      "notifications": { "urls": ["json://localhost"] },
      "settings": { "reminder": "1 month" }
    }

Machine-owned ``last_reminder`` state is persisted separately in
``state/general.json`` as RFC 3339 UTC. Missing configuration uses defaults.

Layout:
    * :mod:`~core.general.vocab` - the tolerant reminder/weekday/time vocabulary and the
      display and parse helpers.
    * :mod:`~core.general.settings` - the general :class:`SettingSpec` declarations and
      their resolution via the shared :mod:`core.settings` machinery.
      Adding a project-wide setting is exactly one spec appended to ``GENERAL_SETTING_SPECS``.
    * :mod:`~core.general.reminder` - the periodic liveness reminder: the anchor-slot grid,
      the persisted state, and the dispatch service.

This package re-exports only the small **production** surface consumers need. Tests and
tooling that reach for the vocabulary, keys or specs import them from the submodule that
owns them (``core.general.vocab`` / ``core.general.settings`` / ``core.general.reminder``).
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
