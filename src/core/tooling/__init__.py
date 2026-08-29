"""Installation-lifecycle tooling for the project as a whole.

Commands that act on an entire checkout rather than on any one scraper: JSON
schema migration and version reporting, invoked by ``./scrooge-alert update`` and
``./scripts/dev/migrate.sh``.

Not to be confused with :mod:`core.scrapers.tooling`, which is the separate
contributor toolbox for building and verifying one plugin. The names are similar
because both are command-line tooling; the split is by what they operate on — a
whole install here, a single plugin there. Neither imports the other.
"""
