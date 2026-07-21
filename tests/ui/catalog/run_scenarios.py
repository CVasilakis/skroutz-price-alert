"""Interactive scraping-panel scenarios (the standard, no-flag run).

Each scenario replays the exact sequence of ``InteractiveRunReporter`` calls the
application workflow makes for a given situation, ending at the visual state to capture. A
*finished* target ends with ``complete_target()`` (settling the final border color); a
*mid-flight* state (spinner, sleeping) stops earlier. The note strings come straight
from the production message catalog (``core.messages``), so these fixtures cannot drift
from what the application actually emits: a reword there flows into these snapshots via
``UPDATE_SNAPSHOTS=1``. Only store-specific details (e.g. a parse error's message)
stay as illustrative literals — they are arbitrary inputs, not framework wording.
"""

from core import messages
from core.application.contracts import ConfigOutcome, PriceOutcome
from core.constants import OLD_ENTRY_HOURS
from core.settings import SettingStatus
from ui.catalog._base import Surface, scenario
from ui.catalog.inputs import (
    CURRENCY,
    STORAGE_BAD_JSON,
    interval_view,
    notify_view,
    retention_view,
    stub_logger,
    suppress_repeated_view,
    views_all_default,
    views_all_ok,
    views_one_invalid_each,
)
from ui.harness.drivers import drive_run

STATUS_OK = SettingStatus.OK
STATUS_DEFAULT = SettingStatus.DEFAULT
STATUS_INVALID = SettingStatus.INVALID

LOGGER = stub_logger()

# The healthy 'Config' row every real Scraping panel leads with (overridden per scenario).
_CONFIG_OK = ConfigOutcome(5)

# Common notes, resolved through the production catalog with this suite's fixed
# example inputs (target 'skroutz', an example stale timestamp).
NOTIFIED_OK = messages.NOTE_NOTIFIED_OK
NOTIFIED_FAIL = messages.NOTE_NOTIFIED_FAIL
NOTIFIED_NONE = messages.NOTE_NOTIFIED_NONE
ERRORS_LOG = messages.errors_log_pointer("skroutz")
ABORTED = messages.NOTE_RATE_LIMIT_ABORTED
STALE = messages.stale_note("25-06-2026 09:00:00", OLD_ENTRY_HOURS)


def _attempts(*error_types: str) -> list[str]:
    """Consecutive per-attempt footnotes (attempts 1..n), as item execution builds them."""
    return [messages.attempt_note(i + 1, t) for i, t in enumerate(error_types)]


def _start(s, settings=None, target="Skroutz", config=_CONFIG_OK):
    """Opens a target with a realistic 'Config' row + settings section (defaults unless overridden)."""
    s.start_target(target, LOGGER, views_all_default() if settings is None else settings, config)


# --- Single-attempt price outcomes --------------------------------------------------


@scenario(
    Surface.RUN,
    "success_drop_notified",
    "Price drop below target; notification delivered",
    tags=("price_drop",),
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result(
            "Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP, notes=[NOTIFIED_OK]
        )
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "success_drop_suppressed",
    "Repeated price-drop notification suppressed",
    tags=("price_drop",),
)
def _():
    def script(s):
        settings = [
            *views_all_default()[:-1],
            suppress_repeated_view(True, STATUS_OK, True),
        ]
        _start(s, settings)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result(
            "Sony WH-1000XM5",
            248.0,
            CURRENCY,
            300.0,
            PriceOutcome.DROP,
            notes=[messages.NOTE_REPEATED_PRICE_ALERT_SUPPRESSED],
        )
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "success_drop_notify_failed",
    "Price drop; some notifications failed to deliver",
    tags=("price_drop",),
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result(
            "Sony WH-1000XM5",
            248.0,
            CURRENCY,
            300.0,
            PriceOutcome.DROP,
            notes=[NOTIFIED_FAIL],
            delivery_failed=True,
        )
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "success_drop_not_configured",
    "Price drop; no notification URLs are configured",
    tags=("price_drop",),
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result(
            "Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP, notes=[NOTIFIED_NONE]
        )
        s.complete_target()

    return drive_run(script)


@scenario(Surface.RUN, "success_ok", "Price at/above target (no drop)", tags=("ok",))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Logitech MX Master 3S", 1, 3)
        s.complete_scraping()
        s.log_price_result("Logitech MX Master 3S", 79.0, CURRENCY, 70.0, PriceOutcome.OK)
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "listing_matches_notified",
    "Listing search: adverts below target, one push each",
    tags=("price_drop", "listing"),
)
def _():
    def script(s):
        _start(s, target="Insomnia")
        s.start_scraping("Google Pixel 9 (128 GB)", 1, 3)
        s.complete_scraping()
        s.log_price_result(
            "Google Pixel 9 (128 GB)",
            185.0,
            CURRENCY,
            200.0,
            PriceOutcome.DROP,
            notes=[messages.advert_matches_note(3, 2), messages.advert_notified_ok(2)],
        )
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "listing_matches_notify_failed",
    "Listing search: some per-advert pushes failed to deliver",
    tags=("price_drop", "listing"),
)
def _():
    def script(s):
        _start(s, target="Insomnia")
        s.start_scraping("Google Pixel 9 (128 GB)", 1, 3)
        s.complete_scraping()
        s.log_price_result(
            "Google Pixel 9 (128 GB)",
            185.0,
            CURRENCY,
            200.0,
            PriceOutcome.DROP,
            notes=[messages.advert_matches_note(3, 2), messages.advert_notified_fail(1, 2)],
            delivery_failed=True,
        )
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "listing_matches_partly_suppressed",
    "Listing search: old advert suppressed and new advert notified",
    tags=("price_drop", "listing"),
)
def _():
    def script(s):
        settings = [
            *views_all_default()[:-1],
            suppress_repeated_view(True, STATUS_OK, True),
        ]
        _start(s, settings, target="Insomnia")
        s.start_scraping("Google Pixel 9 (128 GB)", 1, 3)
        s.complete_scraping()
        s.log_price_result(
            "Google Pixel 9 (128 GB)",
            185.0,
            CURRENCY,
            200.0,
            PriceOutcome.DROP,
            notes=[
                messages.advert_matches_note(3, 2),
                messages.advert_notified_ok(1),
                messages.advert_alerts_suppressed(1),
            ],
        )
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "listing_no_match",
    "Listing search checked fine; no advert matched",
    tags=("ok", "listing"),
)
def _():
    def script(s):
        _start(s, target="Insomnia")
        s.start_scraping("Google Pixel 9 (128 GB)", 1, 3)
        s.complete_scraping()
        s.log_price_result("Google Pixel 9 (128 GB)", None, CURRENCY, 200.0, PriceOutcome.NO_MATCH)
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN, "no_target_zero", "Success with target explicitly 0.0 (no note)", tags=("ok",)
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Generic Monitor", 1, 3)
        s.complete_scraping()
        s.log_price_result("Generic Monitor", 55.0, CURRENCY, 0.0, PriceOutcome.NO_TARGET)
        s.complete_target()

    return drive_run(script)


# --- Skips and non-retryable warnings -----------------------------------------------


@scenario(Surface.RUN, "skip_true", "Item skipped via skip:true in config", tags=("skipped",))
def _():
    def script(s):
        _start(s)
        s.log_result("✅", "Paused Product", "Skipped", messages.NOTE_SKIP_FIELD)
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN, "skip_product_not_found", "Scraper raised ProductNotFoundError", tags=("skipped",)
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Removed Product", 1, 3)
        s.complete_scraping()
        s.log_error(
            "Removed Product",
            messages.skipping_warning("ProductNotFoundError"),
            [messages.not_found_detail(404)],
        )
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "skip_product_unavailable",
    "Scraper raised ProductUnavailableError",
    tags=("skipped",),
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Out Of Stock Item", 1, 3)
        s.complete_scraping()
        s.log_error(
            "Out Of Stock Item",
            messages.skipping_warning("ProductUnavailableError"),
            ["Product found but has no available price."],
        )
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN, "skip_invalid_url_error", "Scraper raised InvalidURLError", tags=("skipped",)
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Weird URL Product", 1, 3)
        s.complete_scraping()
        s.log_error(
            "Weird URL Product",
            messages.skipping_warning("InvalidURLError"),
            ["Could not parse a product ID from the URL."],
        )
        s.complete_target()

    return drive_run(script)


# --- Retries that eventually succeed -------------------------------------------------


@scenario(
    Surface.RUN,
    "retry_success_attempt2",
    "Attempt 1 failed (parse), attempt 2 succeeded",
    tags=("retry",),
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_attempt("Sony WH-1000XM5", 1, 3, "ScraperParseError: No price element found")
        s.start_scraping("Sony WH-1000XM5", 2, 3)
        s.complete_scraping()
        s.log_price_result(
            "Sony WH-1000XM5",
            248.0,
            CURRENCY,
            300.0,
            PriceOutcome.DROP,
            notes=[messages.succeeded_on_attempt(2, 3), NOTIFIED_OK],
            attempt_notes=_attempts("ScraperParseError"),
        )
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "retry_success_attempt3",
    "Two different failures, then success on attempt 3",
    tags=("retry",),
)
def _():
    def script(s):
        _start(s)
        for a in (1, 2):
            s.start_scraping("Sony WH-1000XM5", a, 3)
            s.complete_scraping()
        s.log_attempt("Sony WH-1000XM5", 1, 3, "ScraperParseError: No price element found")
        s.log_attempt("Sony WH-1000XM5", 2, 3, f"ServerError: {messages.server_error_detail(503)}")
        s.start_scraping("Sony WH-1000XM5", 3, 3)
        s.complete_scraping()
        s.log_price_result(
            "Sony WH-1000XM5",
            320.0,
            CURRENCY,
            300.0,
            PriceOutcome.OK,
            notes=[messages.succeeded_on_attempt(3, 3)],
            attempt_notes=_attempts("ScraperParseError", "ServerError"),
        )
        s.complete_target()

    return drive_run(script)


# --- Terminal failures ---------------------------------------------------------------


@scenario(
    Surface.RUN,
    "failure_all_parse",
    "All three attempts failed (ScraperParseError; no errors.txt pointer)",
    tags=("error", "retry"),
)
def _():
    # ScraperParseError's policy saves no traceback, so its failure row carries no
    # errors.txt pointer (only default-policy errors like ConnectionError do).
    def script(s):
        _start(s)
        for a in (1, 2, 3):
            s.start_scraping("Flaky Product", a, 3)
            s.complete_scraping()
            s.log_attempt("Flaky Product", a, 3, "ScraperParseError: No price element found")
        s.log_failure(
            "Flaky Product",
            "ScraperParseError",
            _attempts("ScraperParseError", "ScraperParseError", "ScraperParseError"),
        )
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "failure_server_error",
    "All attempts hit 5xx (ServerError; no errors.txt pointer)",
    tags=("error",),
)
def _():
    def script(s):
        _start(s)
        for a in (1, 2, 3):
            s.start_scraping("Down Backend Item", a, 3)
            s.complete_scraping()
            s.log_attempt(
                "Down Backend Item", a, 3, f"ServerError: {messages.server_error_detail(503)}"
            )
        s.log_failure(
            "Down Backend Item",
            "ServerError",
            _attempts("ServerError", "ServerError", "ServerError"),
        )
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN, "failure_network", "All attempts hit a network error (Wi-Fi down)", tags=("error",)
)
def _():
    def script(s):
        _start(s)
        for a in (1, 2, 3):
            s.start_scraping("Sony WH-1000XM5", a, 3)
            s.complete_scraping()
            s.log_attempt("Sony WH-1000XM5", a, 3, "ConnectionError: Connection aborted")
        s.log_failure(
            "Sony WH-1000XM5",
            "ConnectionError",
            _attempts("ConnectionError", "ConnectionError", "ConnectionError"),
            [ERRORS_LOG],
        )
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "failure_rate_limit",
    "Rate limited on every attempt; target aborts",
    tags=("error",),
)
def _():
    def script(s):
        _start(s)
        for a in (1, 2, 3):
            s.start_scraping("Blocked Product", a, 3)
            s.complete_scraping()
            s.log_attempt(
                "Blocked Product", a, 3, f"RateLimitError: {messages.rate_limited_detail(429)}"
            )
        s.log_failure(
            "Blocked Product",
            "RateLimitError",
            _attempts("RateLimitError", "RateLimitError", "RateLimitError"),
            [ABORTED, ERRORS_LOG],
        )
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "failure_rate_limit_mixed",
    "Two failures then a rate limit on attempt 3; aborts",
    tags=("error", "retry"),
)
def _():
    def script(s):
        _start(s)
        for a in (1, 2, 3):
            s.start_scraping("Blocked Product", a, 3)
            s.complete_scraping()
        s.log_attempt("Blocked Product", 1, 3, "ScraperParseError: No price element found")
        s.log_attempt("Blocked Product", 2, 3, f"ServerError: {messages.server_error_detail(503)}")
        s.log_attempt(
            "Blocked Product", 3, 3, f"RateLimitError: {messages.rate_limited_detail(429)}"
        )
        s.log_failure(
            "Blocked Product",
            "RateLimitError",
            _attempts("ScraperParseError", "ServerError", "RateLimitError"),
            [ABORTED, ERRORS_LOG],
        )
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "failure_stale",
    "Terminal failure on an item that is also stale",
    tags=("error",),
)
def _():
    # A default-policy error (ConnectionError): its failure row stacks the
    # errors.txt pointer with the stale warning.
    def script(s):
        _start(s)
        for a in (1, 2, 3):
            s.start_scraping("Flaky Stale Item", a, 3)
            s.complete_scraping()
            s.log_attempt("Flaky Stale Item", a, 3, "ConnectionError: Connection aborted")
        s.log_failure(
            "Flaky Stale Item",
            "ConnectionError",
            _attempts("ConnectionError", "ConnectionError", "ConnectionError"),
            [ERRORS_LOG, STALE],
        )
        s.complete_target()

    return drive_run(script)


# --- Mid-flight states (sleep / spinner; no complete_target) -------------------------


@scenario(
    Surface.RUN,
    "sleeping_pacing",
    "Normal pacing delay between items (progress bar)",
    tags=("in_progress",),
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Logitech MX Master 3S", 1, 3)
        s.complete_scraping()
        s.log_price_result("Logitech MX Master 3S", 79.0, CURRENCY, 70.0, PriceOutcome.OK)
        s.start_sleep(23.7, 0, 0)
        s.update_sleep(12.3)

    return drive_run(script)


@scenario(
    Surface.RUN,
    "sleeping_retry",
    "Retry back-off delay ('Retrying (2/3)')",
    tags=("in_progress", "retry"),
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_attempt("Sony WH-1000XM5", 1, 3, "ScraperParseError: No price element found")
        s.start_sleep(26.0, 2, 3)
        s.update_sleep(18.4)

    return drive_run(script)


@scenario(
    Surface.RUN, "scraping_spinner", "Active scraping spinner (attempt 1)", tags=("in_progress",)
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Apple AirPods Pro 2", 1, 3)

    return drive_run(script)


@scenario(
    Surface.RUN,
    "scraping_spinner_retry",
    "Active scraping spinner showing the retry counter",
    tags=("in_progress", "retry"),
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Apple AirPods Pro 2", 1, 3)
        s.complete_scraping()
        s.log_attempt("Apple AirPods Pro 2", 1, 3, "ScraperParseError: No price element found")
        s.start_scraping("Apple AirPods Pro 2", 2, 3)

    return drive_run(script)


# --- Interrupts ----------------------------------------------------------------------


@scenario(
    Surface.RUN,
    "interrupt_during_scraping",
    "Ctrl+C while an item was being scraped",
    tags=("interrupt",),
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_interrupt("Received signal SIGINT")
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "interrupt_during_sleep",
    "Ctrl+C during the pacing delay",
    tags=("interrupt", "in_progress"),
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Logitech MX Master 3S", 1, 3)
        s.complete_scraping()
        s.log_price_result("Logitech MX Master 3S", 79.0, CURRENCY, 70.0, PriceOutcome.OK)
        s.start_sleep(23.0, 0, 0)
        s.update_sleep(9.1)
        s.log_interrupt("Received signal SIGINT")
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "interrupt_during_retry_sleep",
    "Ctrl+C during a retry back-off",
    tags=("interrupt", "in_progress", "retry"),
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_attempt("Sony WH-1000XM5", 1, 3, "ScraperParseError: No price element found")
        s.start_sleep(26.0, 2, 3)
        s.update_sleep(20.5)
        s.log_interrupt("Received signal SIGTERM")
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "interrupt_between_products",
    "Ctrl+C after an item, before the next",
    tags=("interrupt",),
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result(
            "Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP, notes=[NOTIFIED_OK]
        )
        s.log_interrupt("Received signal SIGINT")
        s.complete_target()

    return drive_run(script)


# --- System / storage errors ---------------------------------------------------------


@scenario(Surface.RUN, "system_lock_held", "Another instance holds the lock", tags=("system",))
def _():
    def script(s):
        _start(s)
        s.log_error("System", messages.ERR_LOCK_HELD)
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "system_plugin_dependency",
    "Plugin dependencies are not installed",
    tags=("system",),
)
def _():
    def script(s):
        _start(s)
        s.log_error("System", messages.plugin_dependency_detail("skroutz", "tls_client"))
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN, "storage_save_failure", "The state file could not be persisted", tags=("system",)
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result(
            "Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP, notes=[NOTIFIED_OK]
        )
        s.log_error(
            "Storage",
            messages.state_save_failed("skroutz"),
            "Permission denied: 'state/skroutz.json'",
        )
        s.complete_target()

    return drive_run(script)


# --- Settings section variants -------------------------------------------------------


@scenario(
    Surface.RUN,
    "settings_all_default",
    "All settings unset (showing active defaults)",
    tags=("settings",),
)
def _():
    def script(s):
        _start(s)
        s.log_price_result("Sony WH-1000XM5", 320.0, CURRENCY, 300.0, PriceOutcome.OK)
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN, "settings_all_ok", "All settings explicitly configured (valid)", tags=("settings",)
)
def _():
    def script(s):
        _start(s, views_all_ok())
        s.log_price_result("Sony WH-1000XM5", 320.0, CURRENCY, 300.0, PriceOutcome.OK)
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "settings_each_invalid",
    "Every setting invalid (each row footnoted)",
    tags=("settings",),
)
def _():
    def script(s):
        _start(s, views_one_invalid_each())
        s.log_price_result("Sony WH-1000XM5", 320.0, CURRENCY, 300.0, PriceOutcome.OK)
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "settings_mixed",
    "One invalid setting among valid/default ones",
    tags=("settings",),
)
def _():
    def script(s):
        settings = [
            interval_view("1h", STATUS_OK, "1h"),
            retention_view(7, STATUS_INVALID, 99),
            notify_view(True, STATUS_DEFAULT, None),
            suppress_repeated_view(False, STATUS_OK, False),
        ]
        _start(s, settings)
        s.log_price_result("Sony WH-1000XM5", 320.0, CURRENCY, 300.0, PriceOutcome.OK)
        s.complete_target()

    return drive_run(script)


# --- Products-config ('Config' row) variants -----------------------------------------
# The healthy 'Config' row leads every scenario above (_start defaults to a clean load);
# these cover the faulty row and the per-target broken-config skip.


@scenario(
    Surface.RUN,
    "config_faulty",
    "Some items misconfigured (Config row leads)",
    tags=("products",),
)
def _():
    def script(s):
        _start(s, config=ConfigOutcome(3, (2, 4)))
        s.log_price_result("Sony WH-1000XM5", 320.0, CURRENCY, 300.0, PriceOutcome.OK)
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "config_failed_skip",
    "Products config failed to load; scraper skipped",
    tags=("products", "error"),
)
def _():
    # Mirrors the application's per-target skip: open the panel with a failed 'Config' row
    # and finish immediately — no items are scraped for this target.
    def script(s):
        _start(s, config=ConfigOutcome(0, error=STORAGE_BAD_JSON))
        s.complete_target()

    return drive_run(script)


# --- Layout stress: wrapping, truncation, many footnotes -----------------------------


@scenario(
    Surface.RUN,
    "wrap_long_footnote",
    "Very long footnote + a truncated item name",
    tags=("layout",),
)
def _():
    def script(s):
        _start(s)
        for a in (1, 2, 3):
            s.start_scraping("Samsung Odyssey G9 49-inch Curved Gaming Monitor", a, 3)
            s.complete_scraping()
            s.log_attempt(
                "Samsung Odyssey G9 49-inch Curved Gaming Monitor", a, 3, "ScraperParseError"
            )
        s.log_failure(
            "Samsung Odyssey G9 49-inch Curved Gaming Monitor",
            "ScraperParseError",
            _attempts("ScraperParseError"),
            [
                "The server returned an unexpected page structure and the price element could not be located "
                "after exhausting every configured selector fallback; inspect the saved response for details."
            ],
        )
        s.complete_target()

    return drive_run(script)


@scenario(
    Surface.RUN,
    "wrap_many_footnotes",
    "Synthetic renderer stress: one row with six footnotes",
    tags=("layout", "synthetic"),
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result(
            "Sony WH-1000XM5",
            248.0,
            CURRENCY,
            300.0,
            PriceOutcome.DROP,
            notes=[
                "Synthetic renderer note one",
                "Synthetic renderer note two",
                "Synthetic renderer note three",
                "Synthetic renderer note four",
            ],
            attempt_notes=_attempts("ScraperParseError", "ServerError"),
        )
        s.complete_target()

    return drive_run(script)


# --- A realistic multi-item run ------------------------------------------------------


@scenario(
    Surface.RUN,
    "full_run_mixed",
    "A whole target: drop, ok, skip, no-target, and a failure",
    tags=("layout", "combined"),
)
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result(
            "Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP, notes=[NOTIFIED_OK]
        )
        s.start_scraping("Logitech MX Master 3S", 1, 3)
        s.complete_scraping()
        s.log_price_result("Logitech MX Master 3S", 79.0, CURRENCY, 70.0, PriceOutcome.OK)
        s.log_warning(
            "Removed Product",
            messages.skipping_warning("ProductNotFoundError"),
            [messages.not_found_detail(404)],
        )
        s.log_price_result("Untargeted Product", 55.0, CURRENCY, 0.0, PriceOutcome.NO_TARGET)
        s.log_failure(
            "Flaky Product",
            "ConnectionError",
            _attempts("ConnectionError", "ConnectionError", "ConnectionError"),
            [ERRORS_LOG],
        )
        s.complete_target()

    return drive_run(script)
