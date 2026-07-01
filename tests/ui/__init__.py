"""Automated terminal-UI test suite.

Renders every UI state the application can produce (the interactive scraping panel, and
the ``--status`` / ``--ping`` / configuration panels) from a single scenario catalog, and
checks each against a committed plain-text golden snapshot (layout + a border-color
header). The same catalog feeds ``gallery.py`` for a full-color human review.

See ``tests/ui/README.md`` for the add/modify/remove workflow.
"""
