import datetime
import logging
import os
import time
import traceback
from collections.abc import Mapping
from logging.handlers import TimedRotatingFileHandler

from rich.console import Console
from rich.padding import Padding
from rich.text import Text

from core.constants import LOGS_DIR
from core.settings import DEFAULT_LOG_RETENTION_DAYS

console = Console()


class RichConsoleHandler(logging.Handler):
    """Custom handler that uses Rich for console output and supports padding."""

    def emit(self, record):
        """Emits a formatted log record."""
        try:
            msg = self.format(record)

            pad_top = getattr(record, "pad_top", 0)
            pad_bottom = getattr(record, "pad_bottom", 0)

            text_msg = Text(msg)

            if pad_top > 0 or pad_bottom > 0:
                console.print(Padding(text_msg, (pad_top, 0, pad_bottom, 0)))
            else:
                console.print(text_msg)
        except Exception:
            self.handleError(record)


class NonEmptyFilter(logging.Filter):
    """Filter that prevents empty or whitespace-only log messages from being recorded."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Filters a log record to ensure it is not empty."""
        return bool(record.getMessage().strip())


def setup_global_logging(quiet: bool = False) -> None:
    """Configures the global fallback logging level and format.

    This is used by CLI tools (ping, status) and startup messages.
    It logs strictly to the terminal.

    Args:
        quiet (bool): If True, silences the global logger entirely.
    """
    os.makedirs(LOGS_DIR, exist_ok=True)

    level = logging.CRITICAL if quiet else logging.INFO

    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)

    rich_handler = RichConsoleHandler()
    rich_handler.setFormatter(logging.Formatter("%(message)s"))

    logging.root.setLevel(level)
    logging.root.addHandler(rich_handler)

    logging.getLogger("apprise").setLevel(logging.CRITICAL)
    logging.getLogger("urllib3").setLevel(logging.CRITICAL)


def get_target_logger(
    target_name: str,
    quiet: bool = False,
    retention_days: int = DEFAULT_LOG_RETENTION_DAYS,
) -> logging.Logger:
    """Creates or retrieves a configured logger for a specific scraper target.

    If 'quiet' is True, logs are written to a daily rotating file
    ('logs/{target_name}/output.log').
    Otherwise, logs are output to the terminal via the root logger.

    Log retention (the rotating handler's ``backupCount``) is passed in by the caller
    rather than resolved here, so this logging utility stays free of any dependency on
    the scraper/plugin system. The component that owns the run (the orchestrator)
    receives the explicitly resolved ``settings.log_retention_days`` and hands it
    down; callers that don't care (e.g. abort-path error logging) get the default.

    Args:
        target_name (str): The identifier for the scraper (e.g., 'skroutz').
        quiet (bool): If True, logs to file silently. Otherwise, logs to terminal.
        retention_days (int): How many daily log files to keep (``backupCount``).

    Returns:
        logging.Logger: The configured logger instance.
    """
    logger = logging.getLogger(f"scraper.{target_name}")
    logger.setLevel(logging.INFO)

    # Prevent the logger from passing messages to the root logger when in quiet mode
    # so we don't accidentally print to terminal
    logger.propagate = not quiet

    expected_path = os.path.abspath(os.path.join(LOGS_DIR, target_name, "output.log"))
    reusable = False
    for handler in logger.handlers[:]:
        if (
            quiet
            and isinstance(handler, TimedRotatingFileHandler)
            and os.path.abspath(handler.baseFilename) == expected_path
        ):
            handler.backupCount = retention_days
            reusable = True
            continue
        handler.close()
        logger.removeHandler(handler)

    if reusable:
        return logger

    if quiet:
        # Log line timestamps are emitted in UTC (with an explicit marker) so they
        # match the UTC last_checked written to state and are immune to host
        # timezone/DST shifts. utc=True keeps the daily rollover boundary aligned
        # with those UTC timestamps.
        log_format = "[%(asctime)s UTC] %(message)s"
        date_format = "%Y-%m-%d %H:%M:%S"

        target_logs_dir = os.path.join(LOGS_DIR, target_name)
        os.makedirs(target_logs_dir, exist_ok=True)
        log_path = expected_path

        # How many daily files to keep is the caller-supplied retention (resolved from
        # the scraper's configured log_retention_days; default 7). An invalid configured
        # value has already been resolved to the default upstream, so it is always safe
        # to apply here; the invalid value is reported to the user via the settings
        # section / silent settings log, not by this logger.
        rotating_handler = TimedRotatingFileHandler(
            log_path,
            when="midnight",
            interval=1,
            backupCount=retention_days,
            encoding="utf-8",
            utc=True,
        )
        rotating_handler.addFilter(NonEmptyFilter())
        formatter = logging.Formatter(log_format, datefmt=date_format)
        formatter.converter = time.gmtime
        rotating_handler.setFormatter(formatter)

        logger.addHandler(rotating_handler)

    return logger


def save_traceback(
    logger: logging.Logger,
    target_name: str | None = None,
    url: str | None = None,
    diagnostic_context: Mapping[str, str] | None = None,
    log_to_console: bool = True,
) -> None:
    """Saves the current exception traceback to a target-specific error log file.

    Args:
        logger (logging.Logger): The logger instance to log the error summary to.
        target_name (str | None): The identifier for the scraper. If None, saves to root logs dir.
        url (str | None): The URL associated with the error, if any.
        diagnostic_context: Non-secret plugin diagnostics associated with the error.
        log_to_console (bool): If True, logs the critical error message to the logger.
    """
    if target_name:
        target_logs_dir = os.path.join(LOGS_DIR, target_name)
    else:
        target_logs_dir = LOGS_DIR

    os.makedirs(target_logs_dir, exist_ok=True)

    log_path = os.path.join(target_logs_dir, "errors.txt")
    if log_to_console:
        logger.critical(
            f"🛑 An error occurred! Check {log_path} for details.", extra={"pad_top": 1}
        )

    time_now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d (%H:%M:%S)")
    with open(log_path, "a", newline="") as log_file:
        log_file.write(f"\n\nAn error occurred at {time_now} UTC:\n")
        if url:
            log_file.write(f"URL: {url}\n")
        if diagnostic_context:
            details = ", ".join(
                f"{key}: {value}" for key, value in sorted(diagnostic_context.items())
            )
            log_file.write(f"Diagnostic context: {details}\n")
        traceback.print_exc(file=log_file)
        log_file.write(f"\n{'-' * 100}")
