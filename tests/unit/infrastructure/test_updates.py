"""Deterministic tests for local release and remote update inspection."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from core.exceptions import UpdateCheckError
from core.infrastructure import updates
from core.infrastructure.updates import SoftwareVersionStatus


def _git_outputs(
    monkeypatch: pytest.MonkeyPatch,
    *,
    local_tags: str = "v1.7.0",
    local_hash: str = "local",
    remote_hash: str = "local",
    remote_tags: tuple[str, ...] = ("v1.7.0",),
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def output(*args: str) -> str:
        calls.append(args)
        if args == ("tag", "--merged", "HEAD", "--list", "v*"):
            return local_tags
        if args == ("config", "--get", "remote.origin.url"):
            return "git@github.com:owner/project.git"
        if args == ("rev-parse", "HEAD"):
            return local_hash
        if args == (
            "ls-remote",
            "https://github.com/owner/project.git",
            "HEAD",
            "refs/tags/v*",
        ):
            tag_lines = [f"tag-object\trefs/tags/{tag}" for tag in remote_tags]
            return "\n".join([f"{remote_hash}\tHEAD", *tag_lines])
        raise AssertionError(f"Unexpected Git command: {args!r}")

    monkeypatch.setattr(updates, "_git_output", output)
    return calls


def test_up_to_date_uses_highest_reachable_stable_semver(monkeypatch: pytest.MonkeyPatch):
    calls = _git_outputs(
        monkeypatch,
        local_tags="\n".join(
            (
                "v1.9.0",
                "v1.10.0",
                "v2.0.0-rc1",
                "1.11.0",
                "v01.12.0",
                "not-a-release",
            )
        ),
        remote_tags=("v1.9.0", "v1.10.0", "v2.0.0-rc1"),
    )

    assert updates.inspect_software_version() == SoftwareVersionStatus("1.10.0", False)
    assert calls[-1][0] == "ls-remote"


@pytest.mark.parametrize("available", ["1.7.1", "1.8.0", "2.0.0"])
def test_newer_release_tag_is_reported(monkeypatch: pytest.MonkeyPatch, available: str):
    _git_outputs(
        monkeypatch,
        local_hash="old",
        remote_hash="new",
        remote_tags=("v1.7.0", f"v{available}"),
    )

    assert updates.inspect_software_version() == SoftwareVersionStatus("1.7.0", True, available)


def test_new_remote_commits_without_release_are_minor_fixes(
    monkeypatch: pytest.MonkeyPatch,
):
    _git_outputs(monkeypatch, local_hash="old", remote_hash="new")

    assert updates.inspect_software_version() == SoftwareVersionStatus("1.7.0", True)


def test_remote_failure_preserves_the_local_version(monkeypatch: pytest.MonkeyPatch):
    _git_outputs(monkeypatch)

    def offline(*_args: str) -> str:
        raise OSError("offline")

    original: Callable[..., str] = updates._git_output

    def fail_remote(*args: str) -> str:
        if args[0] == "ls-remote":
            return offline(*args)
        return original(*args)

    monkeypatch.setattr(updates, "_git_output", fail_remote)

    assert updates.inspect_software_version() == SoftwareVersionStatus("1.7.0", None)


def test_missing_local_release_is_unknown_without_remote_access(
    monkeypatch: pytest.MonkeyPatch,
):
    calls = _git_outputs(monkeypatch, local_tags="v1.7.0-dev\nrelease")

    assert updates.inspect_software_version() == SoftwareVersionStatus(None, None)
    assert calls == [("tag", "--merged", "HEAD", "--list", "v*")]


def test_boolean_compatibility_wrapper_preserves_success_and_failure(
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        updates,
        "inspect_software_version",
        lambda: SoftwareVersionStatus("1.7.0", True, "1.8.0"),
    )
    assert updates.check_for_updates() is True

    monkeypatch.setattr(
        updates,
        "inspect_software_version",
        lambda: SoftwareVersionStatus("1.7.0", None),
    )
    with pytest.raises(UpdateCheckError, match="Could not check for updates"):
        updates.check_for_updates()
