import logging
import signal
import datetime
import random
import time
from collections.abc import Callable
from dataclasses import dataclass
from types import FrameType
from typing import Any

from core import messages
from core.locks import acquire_lock
from core.constants import MIN_DELAY_SECONDS, RANDOM_DELAY_MIN, RANDOM_DELAY_MAX, RETRY_DELAY_MULTIPLIER, MAX_RETRIES, OLD_ENTRY_HOURS, EXIT_CODE_RATE_LIMIT_ERROR, EXIT_CODE_INTERRUPT, EXIT_CODE_SKIPPED, EXIT_CODE_SUCCESS, EXIT_CODE_PRODUCTS_ERROR, TIMESTAMP_FORMAT
from core.exceptions import RateLimitError, ServerError, ScraperParseError, LockAcquisitionError, StorageFileError, ProductNotFoundError, ProductUnavailableError, InvalidURLError, PluginDependencyError
from core.ui.config_check import TargetLoad, config_view
from core.scrapers.base.model import BaseTrackedItem, ScrapeResult
from core.scrapers.base.storage import BaseDataManager
from core.scrapers.base.settings import KEY_RETENTION, KEY_NOTIFY
from core.scrapers.registry import ScraperRegistry
from core.notifier import Notifier
from core.logger import save_traceback, get_target_logger
from core.ui.tui import ExecutionStrategy, SilentExecutionStrategy, Notes, PriceOutcome
from core.utils import describe_signal


def _utc_now() -> datetime.datetime:
    """Returns the current time as a naive UTC datetime.

    Timestamps are written and compared in naive UTC so the staleness window is
    immune to host timezone or DST changes (UTC has no DST). The value is naive
    (no tzinfo) so it formats with TIMESTAMP_FORMAT and parses back without a
    timezone suffix, keeping existing config files migration-free.
    """
    return datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)


# --- Error handling policy -------------------------------------------------
# scrape_product signals every outcome through the exception it raises. The
# behavior for each retryable error — whether to refresh identity before
# retrying, abort the whole target, count it as a notified failure, save a
# traceback, and any extra footnotes — is declared here once instead of in a
# branching ladder. See BaseScraperClient's docstring for the full contract.

# Terminal, non-retryable errors: the item is skipped (warning, not a failure).
SKIP_ERRORS = (ProductNotFoundError, ProductUnavailableError, InvalidURLError)

# Placeholder in extra_notes, replaced at runtime with the per-target error-log pointer.
ERRORS_LOG_TOKEN = "<errors_log>"


@dataclass(frozen=True)
class ErrorPolicy:
    """How the orchestrator treats a single retryable scrape error.

    Attributes:
        refresh_before_retry: Call ``scraper.refresh_identity()`` between attempts.
        abort: Abort the entire target run once this error becomes terminal.
        counts_as_failure: Include the item in the notified ``failed_items`` list.
        save_traceback: Append a full traceback to the target's errors.txt when terminal.
        extra_notes: Footnotes shown on the terminal failure row. ``ERRORS_LOG_TOKEN``
            entries are replaced with the per-target error-log pointer at runtime.
    """
    refresh_before_retry: bool = True
    abort: bool = False
    counts_as_failure: bool = True
    save_traceback: bool = False
    extra_notes: tuple[str, ...] = ()


_DEFAULT_POLICY = ErrorPolicy(save_traceback=True, extra_notes=(ERRORS_LOG_TOKEN,))

# Matched by isinstance in insertion order; first hit wins, else _DEFAULT_POLICY.
_RETRY_POLICIES: dict[type[Exception], ErrorPolicy] = {
    RateLimitError: ErrorPolicy(
        abort=True,
        save_traceback=True,
        extra_notes=(messages.NOTE_RATE_LIMIT_ABORTED, ERRORS_LOG_TOKEN),
    ),
    # A 5xx is a transient server-side fault: shown and logged, but intentionally
    # not notified and not counted as a failure (a long outage surfaces via stale
    # tracking instead). Retried without rotating identity.
    ServerError: ErrorPolicy(refresh_before_retry=False, counts_as_failure=False),
    ScraperParseError: ErrorPolicy(),
}


def _policy_for(exc: Exception) -> ErrorPolicy:
    """Returns the ErrorPolicy for a retryable exception (isinstance match, else default)."""
    for exc_type, policy in _RETRY_POLICIES.items():
        if isinstance(exc, exc_type):
            return policy
    return _DEFAULT_POLICY


class ScrapingOrchestrator:
    """Orchestrates the scraping process across multiple targets and manages execution flow."""
    def __init__(self, targets_to_run: list[str], registry: ScraperRegistry, notifier: Notifier, config_dir: str, quiet: bool = False, ui_strategy: ExecutionStrategy | None = None, loads_by_target: dict[str, TargetLoad] | None = None, now_fn: Callable[[], datetime.datetime] = _utc_now):
        """Initializes the ScrapingOrchestrator.

        Args:
            targets_to_run (list[str]): A list of scraper target names to run.
            registry (ScraperRegistry): The unified registry for scraper clients and data managers.
            notifier (Notifier): The service used to send notifications.
            config_dir (str): The directory for saving user data and configuration.
            quiet (bool): Whether to log to file silently.
            ui_strategy (ExecutionStrategy | None): The strategy for the UI console output.
            loads_by_target (dict[str, TargetLoad] | None): The preflight ``load_targets`` outcomes keyed by
                target (``{target: TargetLoad}``). Drives the per-scraper 'Config' row and the
                per-target skip of a scraper whose products config failed to load. Targets
                absent from the map (e.g. missing dependencies) simply get no 'Config' row.
            now_fn (Callable): Returns the current time as a naive *UTC* datetime (the
                contract of ``_utc_now``, its default). The clock seam mirroring
                ``ReminderService.now_fn``, so the staleness window and timestamp
                writes are testable without patching the module.
        """
        self.targets_to_run: list[str] = targets_to_run
        self.registry: ScraperRegistry = registry
        self.notifier: Notifier = notifier
        self.config_dir: str = config_dir
        self.quiet: bool = quiet
        self.interrupted: bool = False
        self._interrupt_message: str = ""
        self._current_target: str = ""
        self._current_logger: logging.Logger | None = None
        self._stale_items: list[BaseTrackedItem] = []
        self.ui_strategy: ExecutionStrategy = ui_strategy or SilentExecutionStrategy()
        self.loads_by_target: dict[str, TargetLoad] = loads_by_target or {}
        self._now_fn: Callable[[], datetime.datetime] = now_fn

    def signal_handler(self, signum: int, _frame: FrameType | None) -> None:
        """Handles termination signals gracefully.

        Only sets the interrupted flag and stores the message. All UI cleanup
        is deferred to the main loop to avoid race conditions with the Rich
        Live display's background refresh thread.

        Args:
            signum (int): The signal number received.
            _frame: The current stack frame (unused).
        """
        self._interrupt_message = f"Received signal {describe_signal(signum)}"
        self.interrupted = True

    def _sleep_with_jitter(self, base_delay: float, attempt: int = 0, is_retry: bool = False) -> None:
        """Pauses execution for a calculated duration with random jitter.

        Args:
            base_delay (float): The minimum delay in seconds.
            attempt (int): The retry attempt number to increase the delay. Defaults to 0.
            is_retry (bool): True when this is a retry back-off rather than the normal
                pacing delay between products (controls the sleep row label).
        """
        jitter = random.uniform(RANDOM_DELAY_MIN, RANDOM_DELAY_MAX)
        total_delay = base_delay + (RETRY_DELAY_MULTIPLIER * attempt) + jitter

        start_time = time.monotonic()
        # During a retry back-off the upcoming attempt is the failed (0-based) attempt + 2.
        retry_attempt = attempt + 2 if is_retry else 0
        self.ui_strategy.start_sleep(total_delay, retry_attempt, MAX_RETRIES if is_retry else 0)
        while time.monotonic() - start_time < total_delay:
            if self.interrupted:
                break
            remaining = max(0.0, total_delay - (time.monotonic() - start_time))
            self.ui_strategy.update_sleep(remaining)
            time.sleep(0.05)

        if not self.interrupted:
            actual_delay = time.monotonic() - start_time
            self.ui_strategy.complete_sleep(actual_delay)

    def _check_and_repair_timestamp(self, item: BaseTrackedItem, data_manager: BaseDataManager) -> str | None:
        """Evaluates — and may repair — the stored timestamp of an unscraped product.

        This method writes: a corrupted (unparseable) timestamp is repaired in place
        via the data manager's update mechanism. It is called only for non-skipped
        products whose scrape did not succeed (a successful scrape refreshes the
        timestamp to now, so such a product is never stale). Genuinely stale products
        are recorded for the aggregated end-of-target notification.

        Timestamps are written and compared in naive UTC (see ``_utc_now``), so the
        staleness window is immune to host timezone or DST changes.

        Args:
            item (BaseTrackedItem): The product whose timestamp is being evaluated.
            data_manager (BaseDataManager): The data manager, used to repair a
                corrupted timestamp via the atomic update mechanism.

        Returns:
            str | None: A footnote to attach to the product's row, or None when
                the product has no usable timestamp or is still fresh.
        """
        if not item.last_checked:
            return None

        try:
            timestamp = datetime.datetime.strptime(item.last_checked, TIMESTAMP_FORMAT)
        except ValueError:
            data_manager.update_item(
                item.url,
                last_price=item.last_price,
                last_checked=self._now_fn().strftime(TIMESTAMP_FORMAT)
            )
            return messages.NOTE_CORRUPTED_TIMESTAMP

        if (self._now_fn() - timestamp) > datetime.timedelta(hours=OLD_ENTRY_HOURS):
            self._stale_items.append(item)
            return messages.stale_note(item.last_checked, OLD_ENTRY_HOURS)

        return None

    @staticmethod
    def _combine_notes(*notes: Notes) -> list[str] | None:
        """Flattens the given note values (strings, lists, or None) into one list.

        Returns:
            list | None: A flat list of note strings, or None when empty.
        """
        flat = []
        for note in notes:
            if not note:
                continue
            if isinstance(note, str):
                flat.append(note)
            else:
                flat.extend(note)
        return flat or None

    def _record_attempt(self, item_name: str, attempt: int, error_type: str, detail: str, attempt_notes: list[str]) -> None:
        """Records a single failed scrape attempt.

        Streams the full detail to the silent strategy (one log line per attempt) and
        buffers a concise footnote for the collapsed interactive failure row.

        Args:
            item_name (str): The product name.
            attempt (int): The 0-based attempt index.
            error_type (str): The exception type name of this attempt.
            detail (str): The full error detail (type and message).
            attempt_notes (list): The accumulator for the per-attempt footnotes.
        """
        self.ui_strategy.log_attempt(item_name, attempt + 1, MAX_RETRIES, detail)
        attempt_notes.append(messages.attempt_note(attempt + 1, error_type))

    def _emit_failure(self, item: BaseTrackedItem, data_manager: BaseDataManager, error_type: str, attempt_notes: list[str], extra_notes: Notes = None) -> None:
        """Emits the terminal failure row for a product after all retries are exhausted.

        Args:
            item (BaseTrackedItem): The product that failed.
            data_manager (BaseDataManager): The data manager, for stale evaluation.
            error_type (str): The exception type of the final failed attempt.
            attempt_notes (list): The accumulated per-attempt footnotes.
            extra_notes (Notes): Additional footnotes for this failure (e.g. an
                errors.txt pointer), shown by every strategy alongside the stale note.
        """
        stale_note = self._check_and_repair_timestamp(item, data_manager)
        self.ui_strategy.log_failure(item.name, error_type, attempt_notes=attempt_notes,
                                     extra_notes=self._combine_notes(extra_notes, stale_note))

    def _errors_log_pointer(self) -> str:
        """Returns the footnote pointing at the current target's error log."""
        return messages.errors_log_pointer(self._current_target)

    def _resolve_policy_notes(self, policy: ErrorPolicy) -> list[str] | None:
        """Expands a policy's extra_notes, substituting the runtime error-log pointer."""
        if not policy.extra_notes:
            return None
        return [self._errors_log_pointer() if n == ERRORS_LOG_TOKEN else n for n in policy.extra_notes]

    def _handle_successful_scrape(self, item: BaseTrackedItem, result: ScrapeResult, data_manager: BaseDataManager, original_invalid_price: object | None = None, missing_target_price: bool = False, retries_used: int = 0, attempt_notes: list[str] | None = None) -> None:
        """Processes a successful product scrape, sending notifications if necessary.

        Args:
            item (BaseTrackedItem): The product that was scraped.
            result (ScrapeResult): The result containing the current price and currency.
            data_manager (BaseDataManager): The data manager responsible for saving the updates.
            original_invalid_price: The raw value from the config if the target price was
                unparseable, or None if it was valid.
            missing_target_price (bool): True if the config entry had no target_price field.
            retries_used (int): The number of failed attempts preceding this success.
            attempt_notes (list | None): Per-attempt footnotes for preceding failed
                retries, surfaced on the interactive row ahead of the success notes.
        """
        notes: list[str] = []
        if retries_used > 0:
            notes.append(messages.succeeded_on_attempt(retries_used + 1, MAX_RETRIES))
        if original_invalid_price is not None:
            notes.append(messages.invalid_target_price(original_invalid_price, result.currency))
        elif missing_target_price:
            notes.append(messages.missing_target_price(result.currency))

        if result.price < item.target_price:
            outcome = PriceOutcome.DROP
            if self.notifier.has_services:
                if self.notifier.notify_low_price(item.name, item.target_price, result.price, item.url, result.currency):
                    notes.append(messages.NOTE_NOTIFIED_OK)
                else:
                    notes.append(messages.NOTE_NOTIFIED_FAIL)
            else:
                notes.append(messages.NOTE_NOTIFIED_NONE)
        elif item.target_price == 0.0:
            outcome = PriceOutcome.NO_TARGET
        else:
            outcome = PriceOutcome.OK

        self.ui_strategy.log_price_result(item.name, result.price, result.currency, item.target_price, outcome, notes=notes, attempt_notes=attempt_notes)

        data_manager.update_item(
            item.url,
            last_price=result.price,
            last_checked=self._now_fn().strftime(TIMESTAMP_FORMAT)
        )

    @staticmethod
    def _normalize_target_price(item: BaseTrackedItem, row: dict[str, Any]) -> tuple[object | None, bool]:
        """Detects and neutralizes a missing or invalid target price.

        A negative sentinel target price (set by ``from_dict`` for unparseable input)
        is reset to ``0.0`` in place so price comparisons are safe. The raw values are
        returned rather than injected onto the item so the success handler can surface
        them as footnotes.

        Args:
            item (BaseTrackedItem): The parsed product (mutated in place when invalid).
            row (dict): The raw config row, used to recover the original raw value.

        Returns:
            tuple[object | None, bool]: ``(original_invalid_price, missing_target_price)``
                where ``original_invalid_price`` is the raw unparseable value (or None
                when the price was valid) and ``missing_target_price`` is True when the
                field was absent entirely.
        """
        missing_target_price = 'target_price' not in row
        original_invalid_price = None
        if item.target_price < 0:
            original_invalid_price = row.get('target_price')
            item.target_price = 0.0
        return original_invalid_price, missing_target_price

    def _process_product(self, row: dict[str, Any], data_manager: BaseDataManager) -> tuple[BaseTrackedItem, Exception | None, bool]:
        """Processes a single product from the configuration, attempting to scrape it.

        Args:
            row (dict): The dictionary representation of the product.
            data_manager (BaseDataManager): The data manager.

        Returns:
            tuple[BaseTrackedItem, Exception | None, bool]: A tuple containing:
                - item: The parsed product (returned so the caller need not re-parse it).
                - error: The Exception that caused the failure, or None if successful.
                - abort_scraping: True if scraping should be aborted entirely (e.g., rate limit).
        """
        item = data_manager.parse_item(row)

        if item.skip:
            self.ui_strategy.log_result("✅", item.name, "Skipped", messages.NOTE_SKIP_FIELD)
            return item, None, False

        if not data_manager.is_scrapable_item(row):
            stale_note = self._check_and_repair_timestamp(item, data_manager)
            self.ui_strategy.log_warning(item.name, messages.WARN_INVALID_URL, notes=stale_note)
            return item, None, False

        original_invalid_price, missing_target_price = self._normalize_target_price(item, row)

        self._sleep_with_jitter(MIN_DELAY_SECONDS)
        if self.interrupted:
            return item, None, False

        error, abort = self._run_attempts(item, data_manager, original_invalid_price, missing_target_price)
        return item, error, abort

    def _run_attempts(self, item: BaseTrackedItem, data_manager: BaseDataManager, original_invalid_price: object | None, missing_target_price: bool) -> tuple[Exception | None, bool]:
        """Runs the retry loop for one product, mapping each error through its policy.

        On success it delegates to ``_handle_successful_scrape`` and returns no error.
        Terminal SKIP_ERRORS surface as a warning (no failure). For other errors the
        per-error ``ErrorPolicy`` decides refresh/abort/notify/traceback behavior.

        Args:
            item (BaseTrackedItem): The product to scrape.
            data_manager (BaseDataManager): The data manager (for stale evaluation).
            original_invalid_price: The raw target price when it was unparseable, else None.
            missing_target_price (bool): True when the config row had no target price.

        Returns:
            tuple[Exception | None, bool]: ``(error, abort_scraping)`` — the failure to
                count (or None), and whether the whole target run should abort.
        """
        scraper = self.registry.get_scraper(item.url)
        attempt_notes: list[str] = []

        for attempt in range(MAX_RETRIES):
            if self.interrupted:
                break

            try:
                self.ui_strategy.start_scraping(item.name, attempt + 1, MAX_RETRIES)
                try:
                    result = scraper.scrape_product(item.url)
                finally:
                    self.ui_strategy.complete_scraping()

                if self.interrupted:
                    break

                self._handle_successful_scrape(item, result, data_manager, original_invalid_price, missing_target_price, retries_used=attempt, attempt_notes=attempt_notes)
                return None, False

            except SKIP_ERRORS as e:
                # Terminal for this item, no retry: the product is gone, unavailable,
                # or its URL is unusable. Surfaced as a warning, not a failure.
                stale_note = self._check_and_repair_timestamp(item, data_manager)
                self.ui_strategy.log_warning(item.name, messages.skipping_warning(type(e).__name__),
                                             notes=self._combine_notes(str(e), stale_note), attempt_notes=attempt_notes)
                return None, False

            except Exception as e:
                # Retryable errors: how each is handled (refresh, abort, notify,
                # traceback, footnotes) is declared once in the ErrorPolicy table.
                policy = _policy_for(e)
                self._record_attempt(item.name, attempt, type(e).__name__, f"{type(e).__name__}: {e}", attempt_notes)

                if attempt == MAX_RETRIES - 1:
                    self._emit_failure(item, data_manager, type(e).__name__, attempt_notes, self._resolve_policy_notes(policy))
                    if policy.save_traceback and self._current_logger:
                        save_traceback(self._current_logger, target_name=self._current_target, url=item.url, headers=scraper.get_current_headers(), log_to_console=False)
                    return (e if policy.counts_as_failure else None), policy.abort

                if policy.refresh_before_retry:
                    scraper.refresh_identity()
                self._sleep_with_jitter(MIN_DELAY_SECONDS, attempt, is_retry=True)

        return None, False

    def run(self) -> int:
        """Starts the scraping orchestrator loop.

        Iterates through all configured targets, attempts to scrape their products,
        and manages the overall workflow, including saving state and error reporting.
        """
        signal.signal(signal.SIGINT, self.signal_handler)
        signal.signal(signal.SIGTERM, self.signal_handler)

        any_rate_limited = False
        products_error = False
        skipped_count = 0

        for target in self.targets_to_run:
            failed_items: list[tuple[BaseTrackedItem, Exception]] = []
            self._stale_items = []
            needs_save = False
            abort_target = False

            if self.interrupted:
                break

            self._current_target = target
            # Resolve this target's settings once for the whole run; the registry caches
            # the read and shares the very same accessor with the client and storage. The
            # logger, the start_target settings section, and the notify-on-errors gate
            # below are all derived from this single snapshot. The day-count is handed to
            # the logging utility, which is kept free of any plugin-system dependency; an
            # invalid value is reported to the user via the settings section / silent log
            # at start_target, not by the logger itself.
            settings = self.registry.settings_for(target)
            self._current_logger = get_target_logger(target, self.quiet, settings.value(KEY_RETENTION))
            settings_view = settings.views()

            # A target whose products config failed to load (missing / bad permissions /
            # invalid JSON) is skipped individually — the healthy scrapers still run — with
            # the failure surfaced in this scraper's own panel/log as its 'Config' row.
            # Mirrors the missing-dependency skip below; the whole run no longer aborts.
            load = self.loads_by_target.get(target)
            if load is not None and load.error is not None:
                self.ui_strategy.start_target(
                    target, self._current_logger, settings_view, settings.block_warning,
                    config_view(0, (), load.error),
                )
                self.ui_strategy.complete_target()
                products_error = True
                continue

            try:
                data_manager = self.registry.get_manager(target)
            except ValueError:
                continue
            except PluginDependencyError as e:
                # This scraper's storage layer needs dependencies that are not
                # installed. Skip just this target with an actionable message
                # (mirroring the client-instantiation handler below); other
                # targets and the rest of the run proceed.
                self.ui_strategy.start_target(target, self._current_logger, settings_view, settings.block_warning)
                self.ui_strategy.log_error("System", str(e))
                self.ui_strategy.complete_target()
                continue

            # Storage was already read and validated during the preflight load phase;
            # the registry returns that same cached, in-memory snapshot here.
            if data_manager.get_item_count() == 0:
                continue

            # The 'Config' row (products-config health) leads this scraper's panel, above
            # its settings section — built from the same loaded snapshot the run iterates.
            self.ui_strategy.start_target(
                target, self._current_logger, settings_view, settings.block_warning,
                config_view(data_manager.get_item_count(), data_manager.get_faulty_indices()),
            )

            try:
                with acquire_lock(target):
                    # Normalize the in-memory snapshot the loop iterates. The
                    # actual rewrite happens in save() below, under this same lock,
                    # so a concurrent instance can't race the read-merge-rewrite.
                    data_manager.clean_storage()
                    for row in data_manager.get_items():
                        if abort_target or self.interrupted:
                            break

                        item, product_error, product_abort = self._process_product(row, data_manager)
                        if product_error:
                            failed_items.append((item, product_error))
                        abort_target = abort_target or product_abort
                        any_rate_limited = any_rate_limited or product_abort
                        needs_save = True

                    # Persist under the same lock as clean_storage(): save() does a
                    # read-merge-rewrite, so a concurrent instance must not race the
                    # final write.
                    if needs_save:
                        try:
                            data_manager.save()
                        except StorageFileError as e:
                            # The config filename comes from the plugin descriptor (the
                            # single source of truth) — it is not always <target>.json.
                            config_filename = self.registry.get_plugin(target).get_config_filename()
                            self.ui_strategy.log_error("Storage", messages.save_failed(config_filename), str(e))

                # Notifications involve network I/O and need no lock.
                if not self.interrupted and self._stale_items:
                    self.notifier.notify_old_entries(self._stale_items, OLD_ENTRY_HOURS)

                if not self.interrupted and failed_items:
                    # Per-scraper opt-out: notify_scraping_errors=false silences the
                    # "Scraping Errors" push for this target. Stale-product and crash
                    # alerts are unaffected (and the rate-limit exit code is unchanged),
                    # so a sustained failure still surfaces. The resolver is the single
                    # home for the default-ON rule: unset/unparseable values resolve to
                    # True (notify), so only an explicit, valid `false` silences the push.
                    if settings.value(KEY_NOTIFY):
                        self.notifier.notify_errors(failed_items)

            except LockAcquisitionError:
                self.ui_strategy.log_error("System", messages.ERR_LOCK_HELD)
                self.ui_strategy.complete_target()
                skipped_count += 1
                continue

            except PluginDependencyError as e:
                # This scraper's dependencies are not installed (e.g. run manually
                # after a single-plugin install). Skip just this target with an
                # actionable message; other targets and the rest of the run proceed.
                self.ui_strategy.log_error("System", str(e))
                self.ui_strategy.complete_target()
                continue

            if self.interrupted:
                self.ui_strategy.log_interrupt(self._interrupt_message)
            self.ui_strategy.complete_target()

        if self.interrupted:
            return EXIT_CODE_INTERRUPT

        # A products-config failure is a persistent setup problem (a scraper couldn't even
        # start), so it outranks a transient rate-limit; the run still tried every healthy
        # target. In single-plugin service runs this reproduces the previous exit 15.
        if products_error:
            return EXIT_CODE_PRODUCTS_ERROR

        if any_rate_limited:
            return EXIT_CODE_RATE_LIMIT_ERROR

        if skipped_count > 0 and skipped_count == len(self.targets_to_run):
            return EXIT_CODE_SKIPPED

        return EXIT_CODE_SUCCESS
