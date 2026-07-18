"""Scraper plugin packages.

Plugins are discovered and registered explicitly via
``ScraperRegistry.discover()``, which the registry's own lookup methods call
lazily and idempotently. Importing this package therefore has no registration
side effects, and a populated registry never depends on a caller remembering to
import it first.

Each plugin package exposes a module-level ``PLUGIN`` definition in ``plugin.py``.
Its package ``__init__`` remains import-light and has no registration side effect.
"""
