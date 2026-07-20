import os
import signal
import subprocess
import sys

from core.constants import BASE_DIR, EXIT_CODE_INTERRUPT
from core.exceptions import UpdateCheckError

# Upper bound (seconds) for the git subprocesses in :func:`check_for_updates`. The
# ``ls-remote`` call reaches the network, so without a cap a hung connection would block
# the caller indefinitely - including the reminder check that runs ahead of a scheduled
# scrape. A timeout raises ``subprocess.TimeoutExpired`` (an ``Exception``), which the
# function already folds into ``UpdateCheckError`` (degrading to "could not check").
UPDATE_CHECK_TIMEOUT = 10


def check_for_updates() -> bool:
    """Checks if there are new commits in the remote repository.

    Returns:
        bool: True if a new version is available, False otherwise.

    Raises:
        UpdateCheckError: If there's an error communicating with the remote repository.
    """
    try:
        remote_url = (
            subprocess.check_output(
                ["git", "config", "--get", "remote.origin.url"],
                cwd=BASE_DIR,
                stderr=subprocess.DEVNULL,
                timeout=UPDATE_CHECK_TIMEOUT,
            )
            .decode("utf-8")
            .strip()
        )

        if remote_url.startswith("git@github.com:"):
            remote_url = remote_url.replace("git@github.com:", "https://github.com/")

        local_hash = (
            subprocess.check_output(
                ["git", "rev-parse", "HEAD"],
                cwd=BASE_DIR,
                stderr=subprocess.DEVNULL,
                timeout=UPDATE_CHECK_TIMEOUT,
            )
            .decode("utf-8")
            .strip()
        )
        remote_output = (
            subprocess.check_output(
                ["git", "ls-remote", remote_url, "HEAD"],
                cwd=BASE_DIR,
                stderr=subprocess.DEVNULL,
                timeout=UPDATE_CHECK_TIMEOUT,
            )
            .decode("utf-8")
            .strip()
        )
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
        return "SIGINT (Ctrl+C)"
    if signum == signal.SIGTERM:
        return "SIGTERM (System Shutdown/Termination)"
    return str(signum)


def install_interrupt_handler() -> None:
    """Installs SIGINT/SIGTERM handlers that print a clean message and exit.

    Shared by the one-shot CLI entrypoints (main's pre-flight phase, status, ping):
    clears the current terminal line, prints the interrupt reason, and exits with
    ``EXIT_CODE_INTERRUPT``. The long-running scrape loop installs its own
    deferred handler instead (see ScrapingOrchestrator.signal_handler).
    """
    # Deferred so importing utils does not load rich (~30ms) for paths that never
    # render output (e.g. manifest enumeration in the management scripts).
    from rich.console import Console

    def _handler(signum, _frame):
        os.write(1, b"\033[2K\r")
        Console().print(f"🛑 Interrupted! Received signal {describe_signal(signum)}.\n")
        sys.exit(EXIT_CODE_INTERRUPT)

    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)
