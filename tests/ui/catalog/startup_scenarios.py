"""Interactive startup-transcript scenarios: the whole console a no-flag ``run.sh`` prints
before scraping — the Configuration Check panel, the once-per-run reminder check, and the
Scraping panel — captured on a single console so any text printed *outside* a panel shows.

These exist to catch a class of regression the single-panel surfaces (RUN/STATUS/CONFIG)
structurally cannot: a stray line breaking the panel layout *between* panels — e.g. a
reminder warning logged straight to the console during an interactive run. The companion
assertion in ``test_ui_snapshots`` fails if any captured line falls outside a panel box,
and the golden transcript lets that stray text be inspected visually in the diff.

Test-only (``in_gallery=False``): every panel in these transcripts is already reviewed
on its own surface (CONFIG + RUN), so the unfiltered gallery and HTML report skip this
section as redundant for a human reviewer. The snapshots and the outside-panels
assertion keep running; render on demand with ``gallery.py --surface startup`` or a
matching ``--tag`` (e.g. ``layout``).
"""

from core.application.contracts import ConfigOutcome, PriceOutcome
from ui.catalog._base import Surface, scenario
from ui.catalog.inputs import CURRENCY, stub_logger, views_all_ok
from ui.harness.drivers import drive_startup

LOGGER = stub_logger()


def _run_script(s):
    """A minimal but realistic interactive run: open a target, scrape one product, finish."""
    s.start_target("Skroutz", LOGGER, views_all_ok(), ConfigOutcome(5))
    s.start_scraping("Sony WH-1000XM5", 1, 1)
    s.complete_scraping()
    s.log_price_result("Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP)
    s.complete_target()


@scenario(
    Surface.STARTUP,
    "clean",
    "Valid config: panels stack with no stray text",
    tags=("layout",),
    in_gallery=False,
)
def _():
    return drive_startup(_run_script, valid_count=1)


@scenario(
    Surface.STARTUP,
    "invalid_reminder",
    "Invalid reminder value must not leak a line between panels",
    tags=("reminder", "layout"),
    in_gallery=False,
)
def _():
    return drive_startup(_run_script, valid_count=1, reminder_raw="fortnightly")
