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
    * :mod:`~core.general.vocab` - the tolerant reminder/weekday/time vocabulary and the
      display/parse helpers (the analog of the per-scraper ``intervals.py``).
    * :mod:`~core.general.settings` - the general :class:`SettingSpec` declarations and
      their resolution (via the shared :mod:`core.settings` machinery, ``plugin=None``).
      Adding a project-wide setting is exactly one spec appended to ``GENERAL_SETTING_SPECS``.
    * :mod:`~core.general.reminder` - the periodic liveness reminder: the anchor-slot grid,
      the persisted state, and the dispatch service.

This package re-exports only the small **production** surface consumers need. Tests and
tooling that reach for the vocabulary, keys or specs import them from the submodule that
owns them (``core.general.vocab`` / ``core.general.settings`` / ``core.general.reminder``).
"""

from core.general.settings import general_config_path, resolve_general_settings
from core.general.reminder import ReminderService

__all__ = [
    "general_config_path",
    "resolve_general_settings",
    "ReminderService",
]
