"""Application-driven scraping-panel scenarios (the end-to-end bridge).

Unlike ``run_scenarios`` — which replays hand-written reporter calls and covers every
rendering state exhaustively — these scenarios put the *real* ``ScrapingOrchestrator``
and its target/item/result collaborators in the loop: a scripted client returns each
item's outcomes, and whatever notes and footnotes the application emits land on the
captured panel. A change to the application's UI payloads (wording, ordering, which
notes appear at all) flips these
goldens even if the hand-scripted catalog were forgotten, closing the gap where UI
output changes could pass every test.

Kept to the main note-producing flows; rendering states the workflow can't finish
deterministically (spinners, sleeps, interrupts, stale timestamps) stay in
``run_scenarios``.
"""

from core import messages
from core.exceptions import ResourceNotFoundError, ScraperParseError, ServerError
from core.scrapers.api import PriceResult
from ui.catalog._base import Surface, scenario
from ui.harness.drivers import drive_orchestrated_run

_URL = "https://fake-store.example/p/{}"


@scenario(
    Surface.E2E_RUN, "drop_notified", "Price drop, notification delivered", tags=("price_drop",)
)
def _():
    return drive_orchestrated_run(
        items=[{"name": "Sony WH-1000XM5", "url": _URL.format(1), "target_price": 300.0}],
        results_by_url={_URL.format(1): [PriceResult(price=248.0, currency="€")]},
        has_services=True,
    )


@scenario(
    Surface.E2E_RUN,
    "drop_notify_failed",
    "Price drop, notification delivery failed",
    tags=("price_drop",),
)
def _():
    return drive_orchestrated_run(
        items=[{"name": "Sony WH-1000XM5", "url": _URL.format(1), "target_price": 300.0}],
        results_by_url={_URL.format(1): [PriceResult(price=248.0, currency="€")]},
        has_services=True,
        delivery_ok=False,
    )


@scenario(
    Surface.E2E_RUN,
    "drop_not_configured",
    "Price drop with no notification services configured",
    tags=("price_drop",),
)
def _():
    return drive_orchestrated_run(
        items=[{"name": "Sony WH-1000XM5", "url": _URL.format(1), "target_price": 300.0}],
        results_by_url={_URL.format(1): [PriceResult(price=248.0, currency="€")]},
        has_services=False,
    )


@scenario(
    Surface.E2E_RUN, "retry_then_success", "5xx on attempt 1, success on attempt 2", tags=("retry",)
)
def _():
    return drive_orchestrated_run(
        items=[{"name": "Sony WH-1000XM5", "url": _URL.format(1), "target_price": 300.0}],
        results_by_url={
            _URL.format(1): [
                ServerError(messages.server_error_detail(503)),
                PriceResult(price=320.0, currency="€"),
            ]
        },
    )


@scenario(Surface.E2E_RUN, "failure_all_parse", "Every attempt fails to parse", tags=("error",))
def _():
    return drive_orchestrated_run(
        items=[{"name": "Flaky Item", "url": _URL.format(1), "target_price": 50.0}],
        results_by_url={_URL.format(1): [ScraperParseError("No price element found")]},
    )


@scenario(
    Surface.E2E_RUN,
    "mixed_skip_warning_zero_target",
    "Skip, 404 warning, and an explicit zero target",
    tags=("combined",),
)
def _():
    return drive_orchestrated_run(
        items=[
            {"name": "Paused Item", "url": _URL.format(1), "target_price": 10.0, "skip": True},
            {"name": "Removed Resource", "url": _URL.format(2), "target_price": 10.0},
            {"name": "Untargeted Item", "url": _URL.format(3), "target_price": 0.0},
        ],
        results_by_url={
            _URL.format(2): [ResourceNotFoundError(messages.not_found_detail(404))],
            _URL.format(3): [PriceResult(price=55.0, currency="€")],
        },
    )


@scenario(
    Surface.E2E_RUN,
    "mixed_unsafe_and_valid",
    "A null row is reported while a valid row completes",
    tags=("target_config", "combined"),
)
def _():
    return drive_orchestrated_run(
        items=[
            None,
            {"name": "Sony WH-1000XM5", "url": _URL.format(1), "target_price": 300.0},
        ],
        results_by_url={_URL.format(1): [PriceResult(price=320.0, currency="€")]},
    )
