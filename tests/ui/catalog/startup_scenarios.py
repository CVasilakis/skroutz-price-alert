"""Interactive startup-transcript scenarios: the whole console a no-flag ``run.sh`` prints
before scraping — the Configuration Check panel, the once-per-run reminder check, and the
Scraping panel — captured on a single console so any text printed *outside* a panel shows.

These exist to catch a class of regression the single-panel surfaces (RUN/STATUS/CONFIG)
structurally cannot: a stray line breaking the panel layout *between* panels — e.g. a
reminder warning logged straight to the console during an interactive run. The companion
assertion in ``test_ui_snapshots`` fails if any captured line falls outside a panel box,
and the golden transcript lets that stray text be inspected visually in the diff.
"""

from core.tui import PriceOutcome

from ui.catalog._base import scenario, Surface
from ui.catalog.inputs import CURRENCY, config_ok, stub_logger, views_all_ok
from ui.harness.drivers import drive_startup

LOGGER = stub_logger()


def _run_script(s):
    """A minimal but realistic interactive run: open a target, scrape one product, finish."""
    s.start_target("skroutz", LOGGER, views_all_ok(), None, config_ok())
    s.start_scraping("Sony WH-1000XM5", 1, 1)
    s.complete_scraping()
    s.log_price_result("Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP)
    s.complete_target()


@scenario(Surface.STARTUP, "clean", "Valid config: panels stack with no stray text", tags=("startup",))
def _():
    return drive_startup(_run_script, valid_count=1)


@scenario(Surface.STARTUP, "invalid_reminder", "Invalid reminder value must not leak a line between panels", tags=("startup", "reminder"))
def _():
    return drive_startup(_run_script, valid_count=1, reminder_raw="fortnightly")
