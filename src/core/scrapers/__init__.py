"""Scraper plugin packages.

``framework.catalog.PluginCatalog.discover()`` atomically compiles descriptors from
``core.scrapers.plugins`` into an immutable catalog. Importing this package has no
registration side effects or mutable global registry state.

Each plugin package exposes a module-level ``PLUGIN`` definition in ``plugin.py``.
Its package ``__init__`` remains import-light and has no registration side effect.
"""
