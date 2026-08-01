"""The terminal-UI / presentation layer: everything Scrooge Alert draws.

A cohesive package for the Rich-rendered surfaces, kept together so the presentation
concern has one home (like ``settings``, ``scrapers`` and ``general``) and the entry
points (``run.py``, ``status.py``, ``ping.py``) import their panels from here rather than
from loose top-level modules.

Layout:
    * :mod:`~core.tui.panel` - the shared ``StatusPanelBuilder`` primitive and column
      helpers every panel is assembled from.
    * :mod:`~core.tui.footnotes` - safe shared note registration, inline-code styling,
      and responsive hanging-indent rendering.
    * :mod:`~core.tui.config_check` - the Configuration Check panel and the shared settings/
      config row helpers reused by the status and Scraping panels.
    * :mod:`~core.tui.run_reporter` - the live interactive Scraping reporter.
    * :mod:`~core.tui.ping` and :mod:`~core.tui.status` - pure one-shot panel builders.
    * :mod:`~core.tui.service_verdicts` - service exit-code presentation decisions.

Consumers import the specific submodule they need (``from core.tui.run_reporter import ...``); this
package intentionally does not re-export the modules' large public surfaces.
"""
