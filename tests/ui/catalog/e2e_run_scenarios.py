"""Orchestrator-driven scraping-panel scenarios (the end-to-end bridge).

Unlike ``run_scenarios`` — which replays hand-written strategy calls and covers every
rendering state exhaustively — these scenarios put the *real* ``ScrapingOrchestrator``
in the loop: a scripted client returns each product's outcomes, and whatever notes and
footnotes the orchestrator emits land on the captured panel. A change to the
orchestrator's UI payloads (wording, ordering, which notes appear at all) flips these
goldens even if the hand-scripted catalog were forgotten, closing the gap where UI
output changes could pass every test.

Kept to the main note-producing flows; rendering states the orchestrator can't finish
deterministically (spinners, sleeps, interrupts, stale timestamps) stay in
``run_scenarios``.
"""

from core.exceptions import ProductNotFoundError, ScraperParseError, ServerError
from core import messages
from core.scrapers.base.model import ScrapeResult

from ui.catalog._base import scenario, Surface
from ui.harness.drivers import drive_orchestrated_run

_URL = "https://fake-store.example/p/{}"


@scenario(Surface.E2E_RUN, "drop_notified", "Real orchestrator: price drop, notification delivered", tags=("e2e", "drop"))
def _():
    return drive_orchestrated_run(
        products=[{"name": "Sony WH-1000XM5", "url": _URL.format(1), "target_price": 300.0}],
        results_by_url={_URL.format(1): [ScrapeResult(price=248.0, currency="€")]},
        has_services=True,
    )


@scenario(Surface.E2E_RUN, "retry_then_success", "Real orchestrator: 5xx on attempt 1, success on attempt 2", tags=("e2e", "retry"))
def _():
    return drive_orchestrated_run(
        products=[{"name": "Sony WH-1000XM5", "url": _URL.format(1), "target_price": 300.0}],
        results_by_url={_URL.format(1): [
            ServerError(messages.server_error_detail(503)),
            ScrapeResult(price=320.0, currency="€"),
        ]},
    )


@scenario(Surface.E2E_RUN, "failure_all_parse", "Real orchestrator: every attempt fails to parse", tags=("e2e", "failure"))
def _():
    return drive_orchestrated_run(
        products=[{"name": "Flaky Product", "url": _URL.format(1), "target_price": 50.0}],
        results_by_url={_URL.format(1): [ScraperParseError("No price element found")]},
    )


@scenario(Surface.E2E_RUN, "mixed_skip_warning_no_target", "Real orchestrator: skip, 404 warning, and a missing target price", tags=("e2e", "combined"))
def _():
    return drive_orchestrated_run(
        products=[
            {"name": "Paused Product", "url": _URL.format(1), "target_price": 10.0, "skip": True},
            {"name": "Removed Product", "url": _URL.format(2), "target_price": 10.0},
            {"name": "Untargeted Product", "url": _URL.format(3)},
        ],
        results_by_url={
            _URL.format(2): [ProductNotFoundError(messages.not_found_detail(404))],
            _URL.format(3): [ScrapeResult(price=55.0, currency="€")],
        },
    )
