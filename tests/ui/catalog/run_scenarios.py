"""Interactive scraping-panel scenarios (the standard, no-flag run).

Each scenario replays the exact sequence of ``InteractiveExecutionStrategy`` calls the
orchestrator makes for a given situation, ending at the visual state to capture. A
*finished* target ends with ``complete_target()`` (settling the final border color); a
*mid-flight* state (spinner, sleeping) stops earlier. The note strings here mirror what
``orchestrator.py`` produces at runtime.
"""

from tui import PriceOutcome

from ui.catalog._base import scenario, Surface
from ui.catalog.inputs import (
    stub_logger, CURRENCY,
    views_all_default, views_all_ok, views_one_invalid_each,
    interval_view, retention_view, notify_view,
)
from ui.harness.drivers import drive_run
from scrapers.base.settings import STATUS_OK, STATUS_DEFAULT, STATUS_INVALID

LOGGER = stub_logger()

# Common note strings as the orchestrator phrases them (kept here so the fixtures read
# like real output; they represent inputs to the UI, not assertions on the orchestrator).
NOTIFIED_OK = "Notification delivered to all valid apprise URL(s)."
NOTIFIED_FAIL = "Notification delivery failed for some apprise URL(s)."
NOTIFIED_NONE = "No notification sent (.env not configured)."
ERRORS_LOG = "See logs/skroutz/errors.txt for details."
ABORTED = "Rate limit reached; scraping aborted."
CORRUPTED_TS = "Corrupted timestamp! Updated to current time."
STALE = "Stale: last scraped 25-06-2026 09:00:00 UTC (over 48h ago)."


def _start(s, settings=None, block_warning=None, target="skroutz"):
    """Opens a target with a realistic settings section (defaults unless overridden)."""
    s.start_target(target, LOGGER, views_all_default() if settings is None else settings, block_warning)


# --- Single-attempt price outcomes --------------------------------------------------

@scenario(Surface.RUN, "success_drop_notified", "Price drop below target; notification delivered", tags=("price", "drop"))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result("Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP, notes=[NOTIFIED_OK])
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "success_drop_notify_failed", "Price drop; some notifications failed to deliver", tags=("price", "drop"))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result("Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP, notes=[NOTIFIED_FAIL])
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "success_drop_not_configured", "Price drop; .env has no notification URLs", tags=("price", "drop"))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result("Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP, notes=[NOTIFIED_NONE])
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "success_ok", "Price at/above target (no drop)", tags=("price",))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Logitech MX Master 3S", 1, 3)
        s.complete_scraping()
        s.log_price_result("Logitech MX Master 3S", 79.0, CURRENCY, 70.0, PriceOutcome.OK)
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "no_target_missing", "Success with no target_price field set", tags=("price", "no_target"))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Generic Monitor", 1, 3)
        s.complete_scraping()
        s.log_price_result("Generic Monitor", 55.0, CURRENCY, 0.0, PriceOutcome.NO_TARGET,
                           notes=[f"Missing target price. Defaulting to 0.0 {CURRENCY}"])
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "no_target_invalid", "Success with an unparseable target_price", tags=("price", "no_target"))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Generic Monitor", 1, 3)
        s.complete_scraping()
        s.log_price_result("Generic Monitor", 55.0, CURRENCY, 0.0, PriceOutcome.NO_TARGET,
                           notes=[f"Invalid target price 'abc'. Defaulting to 0.0 {CURRENCY}"])
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "no_target_zero", "Success with target explicitly 0.0 (no note)", tags=("price", "no_target"))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Generic Monitor", 1, 3)
        s.complete_scraping()
        s.log_price_result("Generic Monitor", 55.0, CURRENCY, 0.0, PriceOutcome.NO_TARGET)
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "success_corrupted_timestamp", "Success after repairing a corrupted timestamp", tags=("price",))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result("Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP, notes=[NOTIFIED_OK, CORRUPTED_TS])
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "success_stale", "Success on a product that had gone stale (>48h)", tags=("price", "stale"))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result("Sony WH-1000XM5", 320.0, CURRENCY, 300.0, PriceOutcome.OK, notes=[STALE])
        s.complete_target()
    return drive_run(script)


# --- Skips and non-retryable warnings -----------------------------------------------

@scenario(Surface.RUN, "skip_true", "Product skipped via skip:true in config", tags=("skip",))
def _():
    def script(s):
        _start(s)
        s.log_result("✅", "Paused Product", "Skipped", "The skip field was set to true in the configuration file.")
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "invalid_url_warning", "Product URL not scrapable; skipped with a warning", tags=("warning",))
def _():
    def script(s):
        _start(s)
        s.log_warning("Mistyped Product", "Invalid URL. Skipping product...")
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "invalid_url_warning_stale", "Invalid-URL warning on a stale product", tags=("warning", "stale"))
def _():
    def script(s):
        _start(s)
        s.log_warning("Mistyped Product", "Invalid URL. Skipping product...", STALE)
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "skip_product_not_found", "Scraper raised ProductNotFoundError", tags=("warning", "skip_error"))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Removed Product", 1, 3)
        s.complete_scraping()
        s.log_warning("Removed Product", "Skipping (ProductNotFoundError)", ["Product not found or removed (HTTP 404)."])
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "skip_product_unavailable", "Scraper raised ProductUnavailableError", tags=("warning", "skip_error"))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Out Of Stock Item", 1, 3)
        s.complete_scraping()
        s.log_warning("Out Of Stock Item", "Skipping (ProductUnavailableError)", ["Product found but has no available price."])
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "skip_invalid_url_error", "Scraper raised InvalidURLError", tags=("warning", "skip_error"))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Weird URL Product", 1, 3)
        s.complete_scraping()
        s.log_warning("Weird URL Product", "Skipping (InvalidURLError)", ["Could not parse a product ID from the URL."])
        s.complete_target()
    return drive_run(script)


# --- Retries that eventually succeed -------------------------------------------------

@scenario(Surface.RUN, "retry_success_attempt2", "Attempt 1 failed (parse), attempt 2 succeeded", tags=("retry",))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_attempt("Sony WH-1000XM5", 1, 3, "ScraperParseError: No price element found")
        s.start_scraping("Sony WH-1000XM5", 2, 3)
        s.complete_scraping()
        s.log_price_result("Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP,
                           notes=["Succeeded on attempt 2/3", NOTIFIED_OK],
                           attempt_notes=["Attempt 1: ScraperParseError"])
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "retry_success_attempt3", "Two different failures, then success on attempt 3", tags=("retry",))
def _():
    def script(s):
        _start(s)
        for a in (1, 2):
            s.start_scraping("Sony WH-1000XM5", a, 3)
            s.complete_scraping()
        s.log_attempt("Sony WH-1000XM5", 1, 3, "ScraperParseError: No price element found")
        s.log_attempt("Sony WH-1000XM5", 2, 3, "ServerError: Server error (HTTP 503)")
        s.start_scraping("Sony WH-1000XM5", 3, 3)
        s.complete_scraping()
        s.log_price_result("Sony WH-1000XM5", 320.0, CURRENCY, 300.0, PriceOutcome.OK,
                           notes=["Succeeded on attempt 3/3"],
                           attempt_notes=["Attempt 1: ScraperParseError", "Attempt 2: ServerError"])
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "retry_success_stale_notified", "Stale product: fail attempt 1, succeed attempt 2, drop + notify", tags=("retry", "stale", "drop"))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_attempt("Sony WH-1000XM5", 1, 3, "ScraperParseError: No price element found")
        s.start_scraping("Sony WH-1000XM5", 2, 3)
        s.complete_scraping()
        s.log_price_result("Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP,
                           notes=["Succeeded on attempt 2/3", NOTIFIED_OK, STALE],
                           attempt_notes=["Attempt 1: ScraperParseError"])
        s.complete_target()
    return drive_run(script)


# --- Terminal failures ---------------------------------------------------------------

@scenario(Surface.RUN, "failure_all_parse", "All three attempts failed (ScraperParseError)", tags=("failure", "retry"))
def _():
    def script(s):
        _start(s)
        for a in (1, 2, 3):
            s.start_scraping("Flaky Product", a, 3)
            s.complete_scraping()
            s.log_attempt("Flaky Product", a, 3, "ScraperParseError: No price element found")
        s.log_failure("Flaky Product", "ScraperParseError",
                      ["Attempt 1: ScraperParseError", "Attempt 2: ScraperParseError", "Attempt 3: ScraperParseError"],
                      [ERRORS_LOG])
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "failure_server_error", "All attempts hit 5xx (ServerError; no errors.txt pointer)", tags=("failure",))
def _():
    def script(s):
        _start(s)
        for a in (1, 2, 3):
            s.start_scraping("Down Backend Item", a, 3)
            s.complete_scraping()
            s.log_attempt("Down Backend Item", a, 3, "ServerError: Server error (HTTP 503)")
        s.log_failure("Down Backend Item", "ServerError",
                      ["Attempt 1: ServerError", "Attempt 2: ServerError", "Attempt 3: ServerError"])
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "failure_network", "All attempts hit a network error (Wi-Fi down)", tags=("failure", "network"))
def _():
    def script(s):
        _start(s)
        for a in (1, 2, 3):
            s.start_scraping("Sony WH-1000XM5", a, 3)
            s.complete_scraping()
            s.log_attempt("Sony WH-1000XM5", a, 3, "ConnectionError: Connection aborted")
        s.log_failure("Sony WH-1000XM5", "ConnectionError",
                      ["Attempt 1: ConnectionError", "Attempt 2: ConnectionError", "Attempt 3: ConnectionError"],
                      [ERRORS_LOG])
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "failure_rate_limit", "Rate limited on every attempt; target aborts", tags=("failure", "rate_limit"))
def _():
    def script(s):
        _start(s)
        for a in (1, 2, 3):
            s.start_scraping("Blocked Product", a, 3)
            s.complete_scraping()
            s.log_attempt("Blocked Product", a, 3, "RateLimitError: Blocked or rate limited (HTTP 429)")
        s.log_failure("Blocked Product", "RateLimitError",
                      ["Attempt 1: RateLimitError", "Attempt 2: RateLimitError", "Attempt 3: RateLimitError"],
                      [ABORTED, ERRORS_LOG])
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "failure_rate_limit_mixed", "Two failures then a rate limit on attempt 3; aborts", tags=("failure", "rate_limit", "retry"))
def _():
    def script(s):
        _start(s)
        for a in (1, 2, 3):
            s.start_scraping("Blocked Product", a, 3)
            s.complete_scraping()
        s.log_attempt("Blocked Product", 1, 3, "ScraperParseError: No price element found")
        s.log_attempt("Blocked Product", 2, 3, "ServerError: Server error (HTTP 503)")
        s.log_attempt("Blocked Product", 3, 3, "RateLimitError: Blocked or rate limited (HTTP 429)")
        s.log_failure("Blocked Product", "RateLimitError",
                      ["Attempt 1: ScraperParseError", "Attempt 2: ServerError", "Attempt 3: RateLimitError"],
                      [ABORTED, ERRORS_LOG])
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "failure_stale", "Terminal failure on a product that is also stale", tags=("failure", "stale"))
def _():
    def script(s):
        _start(s)
        for a in (1, 2, 3):
            s.start_scraping("Flaky Stale Item", a, 3)
            s.complete_scraping()
            s.log_attempt("Flaky Stale Item", a, 3, "ScraperParseError: No price element found")
        s.log_failure("Flaky Stale Item", "ScraperParseError",
                      ["Attempt 1: ScraperParseError", "Attempt 2: ScraperParseError", "Attempt 3: ScraperParseError"],
                      [ERRORS_LOG, STALE])
        s.complete_target()
    return drive_run(script)


# --- Mid-flight states (sleep / spinner; no complete_target) -------------------------

@scenario(Surface.RUN, "sleeping_pacing", "Normal pacing delay between products (progress bar)", tags=("sleep",))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Logitech MX Master 3S", 1, 3)
        s.complete_scraping()
        s.log_price_result("Logitech MX Master 3S", 79.0, CURRENCY, 70.0, PriceOutcome.OK)
        s.start_sleep(23.7, 0, 0)
        s.update_sleep(12.3)
    return drive_run(script)


@scenario(Surface.RUN, "sleeping_retry", "Retry back-off delay ('Retrying (2/3)')", tags=("sleep", "retry"))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_attempt("Sony WH-1000XM5", 1, 3, "ScraperParseError: No price element found")
        s.start_sleep(26.0, 2, 3)
        s.update_sleep(18.4)
    return drive_run(script)


@scenario(Surface.RUN, "scraping_spinner", "Active scraping spinner (attempt 1)", tags=("spinner",))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Apple AirPods Pro 2", 1, 3)
    return drive_run(script)


@scenario(Surface.RUN, "scraping_spinner_retry", "Active scraping spinner showing the retry counter", tags=("spinner", "retry"))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Apple AirPods Pro 2", 1, 3)
        s.complete_scraping()
        s.log_attempt("Apple AirPods Pro 2", 1, 3, "ScraperParseError: No price element found")
        s.start_scraping("Apple AirPods Pro 2", 2, 3)
    return drive_run(script)


# --- Interrupts ----------------------------------------------------------------------

@scenario(Surface.RUN, "interrupt_during_scraping", "Ctrl+C while a product was being scraped", tags=("interrupt",))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_interrupt("Received signal SIGINT")
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "interrupt_during_sleep", "Ctrl+C during the pacing delay", tags=("interrupt", "sleep"))
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


@scenario(Surface.RUN, "interrupt_during_retry_sleep", "Ctrl+C during a retry back-off", tags=("interrupt", "sleep", "retry"))
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


@scenario(Surface.RUN, "interrupt_between_products", "Ctrl+C after a product, before the next", tags=("interrupt",))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result("Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP, notes=[NOTIFIED_OK])
        s.log_interrupt("Received signal SIGINT")
        s.complete_target()
    return drive_run(script)


# --- System / storage errors ---------------------------------------------------------

@scenario(Surface.RUN, "system_lock_held", "Another instance holds the lock", tags=("system",))
def _():
    def script(s):
        _start(s)
        s.log_error("System", "Another instance is currently running. Aborting...")
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "system_plugin_dependency", "Plugin dependencies are not installed", tags=("system",))
def _():
    def script(s):
        _start(s)
        s.log_error("System", "Scraper 'skroutz' is missing its dependencies. Run './install.sh --skroutz' to install them.")
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "storage_save_failure", "The config file could not be written back", tags=("system",))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result("Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP, notes=[NOTIFIED_OK])
        s.log_error("Storage", "Failed to update config/skroutz.json file!", "Permission denied: 'config/skroutz.json'")
        s.complete_target()
    return drive_run(script)


# --- Settings section variants -------------------------------------------------------

@scenario(Surface.RUN, "settings_all_default", "All settings unset (showing active defaults)", tags=("settings",))
def _():
    def script(s):
        s.start_target("skroutz", LOGGER, views_all_default(), None)
        s.log_price_result("Sony WH-1000XM5", 320.0, CURRENCY, 300.0, PriceOutcome.OK)
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "settings_all_ok", "All settings explicitly configured (valid)", tags=("settings",))
def _():
    def script(s):
        s.start_target("skroutz", LOGGER, views_all_ok(), None)
        s.log_price_result("Sony WH-1000XM5", 320.0, CURRENCY, 300.0, PriceOutcome.OK)
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "settings_each_invalid", "Every setting invalid (each row footnoted)", tags=("settings",))
def _():
    def script(s):
        s.start_target("skroutz", LOGGER, views_one_invalid_each(), None)
        s.log_price_result("Sony WH-1000XM5", 320.0, CURRENCY, 300.0, PriceOutcome.OK)
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "settings_mixed", "One invalid setting among valid/default ones", tags=("settings",))
def _():
    def script(s):
        settings = [
            interval_view("1h", STATUS_OK, "1h"),
            retention_view(7, STATUS_INVALID, 99),
            notify_view(True, STATUS_DEFAULT, None),
        ]
        s.start_target("skroutz", LOGGER, settings, None)
        s.log_price_result("Sony WH-1000XM5", 320.0, CURRENCY, 300.0, PriceOutcome.OK)
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "settings_malformed_block", "The settings block is not an object (ignored)", tags=("settings",))
def _():
    def script(s):
        s.start_target("skroutz", LOGGER, views_all_default(), "settings block is not an object; using defaults")
        s.log_price_result("Sony WH-1000XM5", 320.0, CURRENCY, 300.0, PriceOutcome.OK)
        s.complete_target()
    return drive_run(script)


# --- Layout stress: wrapping, truncation, many footnotes -----------------------------

@scenario(Surface.RUN, "wrap_long_footnote", "Very long footnote + a truncated product name", tags=("layout",))
def _():
    def script(s):
        _start(s)
        for a in (1, 2, 3):
            s.start_scraping("Samsung Odyssey G9 49-inch Curved Gaming Monitor", a, 3)
            s.complete_scraping()
            s.log_attempt("Samsung Odyssey G9 49-inch Curved Gaming Monitor", a, 3, "ScraperParseError")
        s.log_failure(
            "Samsung Odyssey G9 49-inch Curved Gaming Monitor", "ScraperParseError",
            ["Attempt 1: ScraperParseError"],
            ["The server returned an unexpected page structure and the price element could not be located "
             "after exhausting every configured selector fallback; inspect the saved response for details."],
        )
        s.complete_target()
    return drive_run(script)


@scenario(Surface.RUN, "wrap_many_footnotes", "A single row carrying six stacked footnotes", tags=("layout",))
def _():
    def script(s):
        _start(s)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result(
            "Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP,
            notes=["Succeeded on attempt 3/3", NOTIFIED_OK, CORRUPTED_TS, STALE],
            attempt_notes=["Attempt 1: ScraperParseError", "Attempt 2: ServerError"],
        )
        s.complete_target()
    return drive_run(script)


# --- A realistic multi-product run ---------------------------------------------------

@scenario(Surface.RUN, "full_run_mixed", "A whole target: drop, ok, skip, no-target, and a failure", tags=("layout", "combined"))
def _():
    def script(s):
        s.start_target("skroutz", LOGGER, views_all_default(), None)
        s.start_scraping("Sony WH-1000XM5", 1, 3)
        s.complete_scraping()
        s.log_price_result("Sony WH-1000XM5", 248.0, CURRENCY, 300.0, PriceOutcome.DROP, notes=[NOTIFIED_OK])
        s.start_scraping("Logitech MX Master 3S", 1, 3)
        s.complete_scraping()
        s.log_price_result("Logitech MX Master 3S", 79.0, CURRENCY, 70.0, PriceOutcome.OK)
        s.log_warning("Removed Product", "Skipping (ProductNotFoundError)", ["Product not found or removed (HTTP 404)."])
        s.log_price_result("Untargeted Product", 55.0, CURRENCY, 0.0, PriceOutcome.NO_TARGET,
                           notes=[f"Missing target price. Defaulting to 0.0 {CURRENCY}"])
        s.log_failure("Flaky Product", "ScraperParseError",
                      ["Attempt 1: ScraperParseError", "Attempt 2: ScraperParseError", "Attempt 3: ScraperParseError"],
                      [ERRORS_LOG])
        s.complete_target()
    return drive_run(script)
