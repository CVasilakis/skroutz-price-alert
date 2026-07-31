"""Remote repository update inspection."""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass

from core.constants import BASE_DIR
from core.exceptions import UpdateCheckError

# Upper bound for each git subprocess, including the networked ``ls-remote`` call.
UPDATE_CHECK_TIMEOUT = 10

_VERSION_TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")
_Version = tuple[int, int, int]


@dataclass(frozen=True)
class SoftwareVersionStatus:
    """Installed release version and the already-collected remote update outcome."""

    current_version: str | None
    update_available: bool | None
    available_version: str | None = None


def _git_output(*args: str) -> str:
    """Run one bounded Git inspection and return decoded output."""
    return (
        subprocess.check_output(
            ["git", *args],
            cwd=BASE_DIR,
            stderr=subprocess.DEVNULL,
            timeout=UPDATE_CHECK_TIMEOUT,
        )
        .decode("utf-8")
        .strip()
    )


def _parse_version_tag(tag: str) -> _Version | None:
    match = _VERSION_TAG.fullmatch(tag)
    if match is None:
        return None
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def _latest_version(tags: list[str]) -> tuple[_Version, str] | None:
    versions = [
        (parsed, ".".join(str(part) for part in parsed))
        for tag in tags
        if (parsed := _parse_version_tag(tag)) is not None
    ]
    return max(versions, default=None)


def _remote_refs(output: str) -> tuple[str, list[str]]:
    remote_head = ""
    tags: list[str] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) != 2:
            continue
        revision, ref = parts
        if ref == "HEAD":
            remote_head = revision
        elif ref.startswith("refs/tags/") and not ref.endswith("^{}"):
            tags.append(ref.removeprefix("refs/tags/"))
    return remote_head, tags


def inspect_software_version() -> SoftwareVersionStatus:
    """Collect the local release and remote update state without leaking Git failures.

    The current version is the highest stable semantic-version tag reachable from the
    installed ``HEAD``. Remote inspection remains a comparison with remote ``HEAD``;
    advertised tags only enrich an available update with its release version.
    """
    try:
        local_tags = _git_output("tag", "--merged", "HEAD", "--list", "v*").splitlines()
        current = _latest_version(local_tags)
    except Exception:
        return SoftwareVersionStatus(None, None)

    if current is None:
        return SoftwareVersionStatus(None, None)

    current_parsed, current_display = current
    try:
        remote_url = _git_output("config", "--get", "remote.origin.url")
        if remote_url.startswith("git@github.com:"):
            remote_url = remote_url.replace("git@github.com:", "https://github.com/", 1)

        local_hash = _git_output("rev-parse", "HEAD")
        remote_output = _git_output("ls-remote", remote_url, "HEAD", "refs/tags/v*")
        remote_head, remote_tags = _remote_refs(remote_output)
        if not remote_head:
            return SoftwareVersionStatus(current_display, None)

        update_available = local_hash != remote_head
        remote = _latest_version(remote_tags)
        available_version = (
            remote[1]
            if update_available and remote is not None and remote[0] > current_parsed
            else None
        )
        return SoftwareVersionStatus(
            current_display,
            update_available,
            available_version,
        )
    except Exception:
        return SoftwareVersionStatus(current_display, None)


def check_for_updates() -> bool:
    """Return whether remote HEAD differs, preserving the reminder's boolean API."""
    status = inspect_software_version()
    if status.update_available is None:
        raise UpdateCheckError("Could not check for updates")
    return status.update_available


__all__ = [
    "UPDATE_CHECK_TIMEOUT",
    "SoftwareVersionStatus",
    "check_for_updates",
    "inspect_software_version",
]
