"""Abstract base contracts for scraper plugins.

This package intentionally performs NO imports at the package level. Its modules
are imported directly via their full submodule path
(e.g. ``from core.scrapers.base.http_client import HttpScraperClient``), never as
re-exports from here. Keeping this ``__init__`` import-free guarantees the base
package can never transitively pull in a transport library (``tls_client``,
``selenium``, ...) merely because someone imported, say,
``core.scrapers.base.plugin`` during plugin discovery. Heavy transport imports live
at the top of their concrete client module and load only when that client is
actually instantiated for a scrape.
"""
