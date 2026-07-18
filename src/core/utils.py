import os
import re
import sys
import json
import math
import signal
import subprocess
from dotenv import load_dotenv


from core.constants import BASE_DIR, APPRISE_PLACEHOLDERS, EXIT_CODE_INTERRUPT
from core.exceptions import EnvFileError, UpdateCheckError

# Upper bound (seconds) for the git subprocesses in :func:`check_for_updates`. The
# ``ls-remote`` call reaches the network, so without a cap a hung connection would block
# the caller indefinitely - including the reminder check that runs ahead of a scheduled
# scrape. A timeout raises ``subprocess.TimeoutExpired`` (an ``Exception``), which the
# function already folds into ``UpdateCheckError`` (degrading to "could not check").
UPDATE_CHECK_TIMEOUT = 10


def write_json_atomically(path: str, data) -> None:
    """Serializes ``data`` to ``path`` as JSON via a temp-file swap.

    Writes ``<path>.tmp`` then ``os.replace``s it over ``path``, so a crash mid-write can
    never leave a partially written file. The single atomic-JSON writer shared by the
    storage backend and the reminder state file; it raises ``OSError`` and lets each
    framework state repositories and the reminder; callers choose their error policy.
    """
    temp_path = path + ".tmp"
    with open(temp_path, mode="w") as file:
        json.dump(data, file, indent=2)
    os.replace(temp_path, path)

def parse_price(raw_value) -> float | None:
    """Parses a raw price value into a float.

    This is the scraper price-normalization routine, so a new
    store never needs to re-implement price cleaning. Ints and floats are returned directly; strings
    may carry a currency symbol, surrounding quotes/whitespace, and either European
    (``1.299,00``) or US (``1,299.00``) grouping.

    Normalization rule: after stripping everything but digits, ``.``, ``,`` and a
    leading sign, the right-most ``.``/``,`` is treated as the decimal separator and
    every other separator is dropped as a thousands grouping. A value with a single
    separator is therefore read as a decimal (``"1,234"`` -> ``1.234``), matching the
    previous behavior. Returns None when the value cannot be parsed into a finite number.

    Args:
        raw_value: The raw price value (str, int, float, or None).

    Returns:
        float | None: The parsed price, or None if parsing fails.
    """
    if raw_value is None or isinstance(raw_value, bool):
        return None

    if isinstance(raw_value, (int, float)):
        try:
            value = float(raw_value)
        except OverflowError:
            return None
        return value if math.isfinite(value) else None

    if not isinstance(raw_value, str):
        return None

    # Keep only digits, separators and a leading sign (drops currency symbols,
    # spaces, and surrounding quotes in one pass).
    cleaned = re.sub(r'[^\d.,-]', '', raw_value)
    sign = '-' if cleaned.startswith('-') else ''
    cleaned = cleaned.replace('-', '')
    if not cleaned:
        return None

    decimal_pos = max(cleaned.rfind('.'), cleaned.rfind(','))
    if decimal_pos == -1:
        number = cleaned
    else:
        integer_part = re.sub(r'[.,]', '', cleaned[:decimal_pos])
        fractional_part = re.sub(r'[.,]', '', cleaned[decimal_pos + 1:])
        number = f"{integer_part}.{fractional_part}"

    try:
        value = float(f"{sign}{number}")
        return value if math.isfinite(value) else None
    except (ValueError, OverflowError):
        return None

def is_valid_apprise_url(url: str) -> bool:
    """Returns whether a single Apprise URL is usable.

    A URL is valid when it is non-empty, contains no unconfigured placeholder
    (e.g. ``<token>``), and Apprise can instantiate it. This is the single
    predicate used everywhere notification URLs are validated.

    Args:
        url (str): A single Apprise URL (surrounding whitespace is ignored).

    Returns:
        bool: True if the URL is a usable Apprise endpoint.
    """
    url = (url or "").strip()
    if not url:
        return False
    if any(p in url for p in APPRISE_PLACEHOLDERS):
        return False
    # Deferred so importing utils (pulled in almost everywhere via parse_price)
    # does not load apprise (~88ms) for commands that never validate a URL.
    import apprise
    return bool(apprise.Apprise.instantiate(url))

def classify_notification_urls(notification_urls: str) -> tuple[list, list]:
    """Splits a comma-separated Apprise URL string into valid and invalid URLs.

    A URL is considered valid when it contains no unconfigured placeholder and
    Apprise can instantiate it. Empty entries are ignored.

    Args:
        notification_urls (str): The raw, comma-separated NOTIFICATION_URLS value.

    Returns:
        tuple[list, list]: A (valid_urls, invalid_urls) pair.
    """
    valid_urls, invalid_urls = [], []
    for url in (notification_urls or "").split(','):
        url = url.strip()
        if not url:
            continue
        if is_valid_apprise_url(url):
            valid_urls.append(url)
        else:
            invalid_urls.append(url)
    return valid_urls, invalid_urls

def check_env_file() -> None:
    """Validates the existence and contents of the .env file.

    Raises:
        EnvFileError: If the .env file is missing, unreadable, or missing valid NOTIFICATION_URLS.
    """
    env_path = os.path.join(BASE_DIR, '.env')
    # Existence/readability is checked BEFORE load_dotenv: python-dotenv raises a
    # raw PermissionError on an unreadable file, which would escape as a crash
    # instead of the modeled EnvFileError (clean exit 16) this function promises.
    if not os.path.isfile(env_path) or not os.access(env_path, os.R_OK):
        raise EnvFileError("No .env file found or unreadable")
    try:
        load_dotenv(dotenv_path=env_path)
    except (OSError, UnicodeError) as e:
        raise EnvFileError(f"The .env file is unreadable or not valid UTF-8: {e}") from None

    notification_urls = os.environ.get("NOTIFICATION_URLS", "").strip()
    if not notification_urls:
        raise EnvFileError("No NOTIFICATION_URLS provided in .env file")

    urls = [u.strip() for u in notification_urls.split(',') if u.strip()]

    valid_urls = [u for u in urls if is_valid_apprise_url(u)]
    if not valid_urls:
        raise EnvFileError("NOTIFICATION_URLS contains no valid notification URL(s)")

def check_for_updates() -> bool:
    """Checks if there are new commits in the remote repository.

    Returns:
        bool: True if a new version is available, False otherwise.

    Raises:
        UpdateCheckError: If there's an error communicating with the remote repository.
    """
    try:
        remote_url = subprocess.check_output(['git', 'config', '--get', 'remote.origin.url'], cwd=BASE_DIR, stderr=subprocess.DEVNULL, timeout=UPDATE_CHECK_TIMEOUT).decode('utf-8').strip()

        if remote_url.startswith('git@github.com:'):
            remote_url = remote_url.replace('git@github.com:', 'https://github.com/')

        local_hash = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=BASE_DIR, stderr=subprocess.DEVNULL, timeout=UPDATE_CHECK_TIMEOUT).decode('utf-8').strip()
        remote_output = subprocess.check_output(['git', 'ls-remote', remote_url, 'HEAD'], cwd=BASE_DIR, stderr=subprocess.DEVNULL, timeout=UPDATE_CHECK_TIMEOUT).decode('utf-8').strip()
        if remote_output:
            remote_hash = remote_output.split()[0]
            return local_hash != remote_hash
        else:
            raise UpdateCheckError("Failed to retrieve remote repository version information")
    except Exception as e:
        raise UpdateCheckError(f"Could not check for updates: {e}")

def describe_signal(signum) -> str:
    """Returns a human-readable name for a termination signal.

    Args:
        signum: The signal number received.

    Returns:
        str: A friendly label (e.g. ``'SIGINT (Ctrl+C)'``), or the raw number as a string.
    """
    if signum == signal.SIGINT:
        return 'SIGINT (Ctrl+C)'
    if signum == signal.SIGTERM:
        return 'SIGTERM (System Shutdown/Termination)'
    return str(signum)

def install_interrupt_handler() -> None:
    """Installs SIGINT/SIGTERM handlers that print a clean message and exit.

    Shared by the one-shot CLI entrypoints (main's pre-flight phase, status, ping):
    clears the current terminal line, prints the interrupt reason, and exits with
    ``EXIT_CODE_INTERRUPT``. The long-running scrape loop installs its own
    deferred handler instead (see ScrapingOrchestrator.signal_handler).
    """
    # Deferred so importing utils does not load rich (~30ms) for paths that never
    # render output (e.g. the registry's list_plugins enumeration in the scripts).
    from rich.console import Console

    def _handler(signum, _frame):
        os.write(1, b"\033[2K\r")
        Console().print(f"🛑 Interrupted! Received signal {describe_signal(signum)}.\n")
        sys.exit(EXIT_CODE_INTERRUPT)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
