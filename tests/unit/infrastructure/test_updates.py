"""Deterministic tests for local release and remote update inspection."""

from __future__ import annotations

import subprocess
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
    branch: str = "main",
    advertise_head: bool = True,
) -> list[tuple[str, ...]]:
    calls: list[tuple[str, ...]] = []

    def output(*args: str) -> str:
        calls.append(args)
        if args == ("tag", "--merged", "HEAD", "--list", "v*"):
            return local_tags
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            return branch
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
            # Real output for this project: the release workflow creates annotated
            # tags, so every tag advertises both its tag object and a peeled `^{}`
            # entry for the commit. A remote whose symbolic HEAD points at a ref that
            # no longer exists still exits 0 and simply omits the HEAD line.
            tag_lines: list[str] = []
            for tag in remote_tags:
                tag_lines.append(f"tag-object\trefs/tags/{tag}")
                tag_lines.append(f"peeled-commit\trefs/tags/{tag}^{{}}")
            head_lines = [f"{remote_hash}\tHEAD"] if advertise_head else []
            return "\n".join([*head_lines, *tag_lines])
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


@pytest.mark.parametrize("branch", ["beta", "feature/x"])
def test_non_release_branch_reports_itself_without_a_remote_comparison(
    monkeypatch: pytest.MonkeyPatch, branch: str
):
    # Remote HEAD tracks main, so comparing against it off main would advertise an
    # update `./scrooge-alert update` refuses to install. No ls-remote is attempted.
    calls = _git_outputs(monkeypatch, branch=branch, remote_hash="remote")

    assert updates.inspect_software_version() == SoftwareVersionStatus(
        "1.7.0", None, non_release_branch=branch
    )
    assert not any(args[0] == "ls-remote" for args in calls)


@pytest.mark.parametrize("branch", ["main", "HEAD", ""])
def test_release_branch_and_detached_head_keep_the_remote_comparison(
    monkeypatch: pytest.MonkeyPatch, branch: str
):
    # A detached HEAD has no branch to name, so it keeps the ordinary verdict instead of
    # gaining a state of its own.
    calls = _git_outputs(monkeypatch, branch=branch, remote_hash="remote")

    assert updates.inspect_software_version() == SoftwareVersionStatus("1.7.0", True)
    assert any(args[0] == "ls-remote" for args in calls)


def test_unreadable_branch_keeps_the_remote_comparison(monkeypatch: pytest.MonkeyPatch):
    _git_outputs(monkeypatch, remote_hash="remote")
    original: Callable[..., str] = updates._git_output

    def fail_branch(*args: str) -> str:
        if args == ("rev-parse", "--abbrev-ref", "HEAD"):
            raise subprocess.CalledProcessError(128, "git")
        return original(*args)

    monkeypatch.setattr(updates, "_git_output", fail_branch)

    assert updates.inspect_software_version() == SoftwareVersionStatus("1.7.0", True)


def test_non_git_checkout_is_unknown_without_remote_access(monkeypatch: pytest.MonkeyPatch):
    # An installation without Git history (an extracted archive rather than a clone)
    # has no release to report. That must read as "Unknown" on the panel instead of
    # escaping as a traceback out of status and the interactive run.
    calls = _git_outputs(monkeypatch)
    original: Callable[..., str] = updates._git_output

    def not_a_repository(*args: str) -> str:
        if args[0] == "tag":
            raise subprocess.CalledProcessError(128, "git")
        return original(*args)

    monkeypatch.setattr(updates, "_git_output", not_a_repository)

    assert updates.inspect_software_version() == SoftwareVersionStatus(None, None)
    assert not any(args[0] == "ls-remote" for args in calls)


def test_unadvertised_remote_head_is_unknown_rather_than_an_update(
    monkeypatch: pytest.MonkeyPatch,
):
    # A remote whose symbolic HEAD points at a deleted or renamed branch answers
    # ls-remote successfully but advertises no HEAD. Comparing the local commit
    # against that absent revision would report an update forever, on every run.
    _git_outputs(monkeypatch, local_hash="local", advertise_head=False)

    assert updates.inspect_software_version() == SoftwareVersionStatus("1.7.0", None)


def test_peeled_annotated_tag_entries_are_not_versions(monkeypatch: pytest.MonkeyPatch):
    # Annotated tags advertise `refs/tags/<tag>` for the tag object and
    # `refs/tags/<tag>^{}` for the commit. Only the tag name is a release version;
    # the peeled entry must never reach version selection.
    head, tags = updates._remote_refs(
        "\n".join(
            (
                "head-commit\tHEAD",
                "tag-object\trefs/tags/v1.8.0",
                "peeled-commit\trefs/tags/v1.8.0^{}",
            )
        )
    )
    assert (head, tags) == ("head-commit", ["v1.8.0"])

    # And end to end: the peeled entry never becomes the advertised release version.
    _git_outputs(monkeypatch, local_hash="old", remote_hash="new", remote_tags=("v1.8.0",))
    assert updates.inspect_software_version() == SoftwareVersionStatus("1.7.0", True, "1.8.0")


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
