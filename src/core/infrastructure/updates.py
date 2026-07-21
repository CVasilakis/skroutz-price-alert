"""Remote repository update inspection."""

from __future__ import annotations

import subprocess

from core.constants import BASE_DIR
from core.exceptions import UpdateCheckError

# Upper bound for each git subprocess, including the networked ``ls-remote`` call.
UPDATE_CHECK_TIMEOUT = 10


def check_for_updates() -> bool:
    """Return whether the remote HEAD differs from the local checkout."""
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
            return local_hash != remote_output.split()[0]
        raise UpdateCheckError("Failed to retrieve remote repository version information")
    except Exception as exc:
        raise UpdateCheckError(f"Could not check for updates: {exc}") from exc


__all__ = ["UPDATE_CHECK_TIMEOUT", "check_for_updates"]
