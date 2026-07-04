"""The terminal-UI / presentation layer: everything Scrooge Alert draws.

A cohesive package for the Rich-rendered surfaces, kept together so the presentation
concern has one home (like ``settings``, ``scrapers`` and ``general``) and the entry
points (``main.py``, ``status.py``, ``ping.py``) import their panels from here rather than
from loose top-level modules.

Layout:
    * :mod:`~core.ui.panel` - the shared ``StatusPanelBuilder`` primitive and column
      helpers every panel is assembled from.
    * :mod:`~core.ui.config_check` - the Configuration Check panel and the shared settings/
      config row helpers reused by the ``--status`` and Scraping panels.
    * :mod:`~core.ui.tui` - the live interactive Scraping panel (the execution strategies).

Consumers import the specific submodule they need (``from core.ui.tui import ...``); this
package intentionally does not re-export the modules' large public surfaces.
"""
