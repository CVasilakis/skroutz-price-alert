from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import ui.catalog  # noqa: F401  # initialize the shared shell catalog
from ui.harness.shell import ShellWorld, _build_sandbox, _cleanup, _fake_env

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _run(checkout: Path, world: ShellWorld, *args: str):
    return subprocess.run(
        ["/bin/sh", str(checkout / "scripts/scrooge-alert"), *args],
        cwd=checkout,
        env=_fake_env(checkout, world),
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_bare_and_global_help_are_stable_and_public():
    world = ShellWorld()
    checkout = _build_sandbox(world)
    try:
        bare = _run(checkout, world)
        explicit = _run(checkout, world, "--help")
    finally:
        _cleanup(checkout)

    assert bare.returncode == explicit.returncode == 0
    assert bare.stdout == explicit.stdout
    assert "Usage: scrooge-alert <command> [options]" in bare.stdout
    assert "Commands:\n" in bare.stdout
    assert "Options:\n" in bare.stdout
    assert "  --version " in bare.stdout


@pytest.mark.parametrize(
    "args",
    (
        ("-h",),
        ("--ping",),
        ("--status",),
        ("--skroutz", "run"),
        ("scrape",),
        ("run", "run"),
        ("ping", "extra"),
        ("status", "--quiet"),
        ("run", "--debug"),
    ),
)
def test_public_aliases_unknown_commands_and_invalid_combinations_fail(args):
    world = ShellWorld()
    checkout = _build_sandbox(world)
    try:
        result = _run(checkout, world, *args)
    finally:
        _cleanup(checkout)

    assert result.returncode == 1
    assert "Run 'scrooge-alert --help' for usage." in _ANSI.sub("", result.stderr)
    assert "\x1b[1;36mscrooge-alert --help\x1b[0m" in result.stderr


@pytest.mark.parametrize(
    ("command", "relative_script", "expected_args"),
    (
        ("run", "scripts/run.sh", ("--quiet", "--skroutz")),
        ("ping", "scripts/run.sh", ("--ping",)),
        ("status", "scripts/run.sh", ("--status",)),
        ("install", "install.sh", ("--debug", "--skroutz")),
        ("enable", "scripts/enable.sh", ("--debug", "--skroutz")),
        ("disable", "scripts/disable.sh", ("--debug", "--skroutz")),
        ("stop", "scripts/stop.sh", ("--debug", "--skroutz")),
        ("schedule", "scripts/schedule.sh", ("--debug", "--skroutz")),
        ("update", "update.sh", ("--debug",)),
        ("uninstall", "scripts/uninstall.sh", ("--debug", "--skroutz")),
    ),
)
def test_commands_exec_the_existing_owner_and_preserve_status(
    command, relative_script, expected_args
):
    world = ShellWorld()
    checkout = _build_sandbox(world)
    try:
        owner = checkout / relative_script
        owner.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$0\"\n"
            'for arg in "$@"; do printf \'arg:%s\\n\' "$arg"; done\n'
            "exit 37\n"
        )
        owner.chmod(0o755)
        public_args = () if command in ("ping", "status") else expected_args
        result = _run(checkout, world, command, *public_args)
    finally:
        _cleanup(checkout)

    assert result.returncode == 37
    assert result.stdout.splitlines()[0].endswith(relative_script)
    assert result.stdout.splitlines()[1:] == [f"arg:{arg}" for arg in expected_args]


def test_command_help_uses_public_usage_and_dynamic_target_policy():
    world = ShellWorld(
        plugins=("skroutz", "insomnia"),
        installed_timers=("skroutz",),
        installed_services=("skroutz", "orphan"),
    )
    checkout = _build_sandbox(world)
    try:
        run_help = _run(checkout, world, "run", "--help")
        uninstall_help = _run(checkout, world, "uninstall", "--help")
    finally:
        _cleanup(checkout)

    assert run_help.returncode == uninstall_help.returncode == 0
    assert "Usage: scrooge-alert run" in run_help.stdout
    assert "Options:" in run_help.stdout
    assert "--skroutz" in run_help.stdout and "--insomnia" in run_help.stdout
    assert "Usage: scrooge-alert uninstall" in uninstall_help.stdout
    assert "--orphan" in uninstall_help.stdout


def test_install_creates_an_owned_executable_launcher_without_editing_profiles():
    world = ShellWorld(config_files=("skroutz.json", "general.json"))
    checkout = _build_sandbox(world)
    try:
        env = _fake_env(checkout, world)
        result = subprocess.run(
            ["/bin/sh", str(checkout / "install.sh")],
            cwd=checkout,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
        launcher = checkout / "home/.local/bin/scrooge-alert"
        launcher_text = launcher.read_text()
        mode = launcher.stat().st_mode & 0o777
        launcher_help = subprocess.run(
            [str(launcher), "--help"],
            cwd=Path("/"),
            env={**env, "PATH": f"{launcher.parent}:{env['PATH']}"},
            text=True,
            capture_output=True,
            timeout=30,
        )
        profile_entries = [
            path
            for name in (".profile", ".bashrc", ".zshrc")
            if (path := checkout / "home" / name).exists()
        ]
    finally:
        _cleanup(checkout)

    assert result.returncode == 0, result.stdout + result.stderr
    assert mode == 0o755
    assert f"# scrooge-alert checkout: {checkout}" in launcher_text
    assert launcher_help.returncode == 0
    assert "Usage: scrooge-alert <command>" in launcher_help.stdout
    assert not profile_entries
    assert "Add " in result.stdout and ".local/bin to PATH" in result.stdout


def test_partial_target_configuration_failure_still_installs_the_command():
    world = ShellWorld(
        plugins=("skroutz", "broken"),
        config_files=("skroutz.json", "broken.json", "general.json"),
        schedule_errors={"broken": "invalid schema"},
    )
    checkout = _build_sandbox(world)
    try:
        result = subprocess.run(
            ["/bin/sh", str(checkout / "install.sh")],
            cwd=checkout,
            env=_fake_env(checkout, world),
            text=True,
            capture_output=True,
            timeout=30,
        )
        launcher = checkout / "home/.local/bin/scrooge-alert"
        installed = launcher.is_file() and os.access(launcher, os.X_OK)
    finally:
        _cleanup(checkout)

    assert result.returncode == 15
    assert installed


@pytest.mark.parametrize("existing_kind", ("regular", "symlink"))
def test_install_rejects_unowned_or_linked_launcher_before_creating_venv(existing_kind):
    world = ShellWorld(venv=False, config_files=("skroutz.json", "general.json"))
    checkout = _build_sandbox(world)
    try:
        launcher = checkout / "home/.local/bin/scrooge-alert"
        launcher.parent.mkdir(parents=True)
        if existing_kind == "regular":
            launcher.write_text("unrelated\n")
        else:
            target = checkout / "unrelated"
            target.write_text("keep\n")
            launcher.symlink_to(target)
        result = subprocess.run(
            ["/bin/sh", str(checkout / "install.sh")],
            cwd=checkout,
            env=_fake_env(checkout, world),
            text=True,
            capture_output=True,
            timeout=30,
        )
        venv_created = (checkout / "venv").exists()
    finally:
        _cleanup(checkout)

    assert result.returncode == 1
    assert "user command destinations are unsafe" in result.stdout
    assert not venv_created


def test_version_falls_back_to_unknown_without_a_usable_venv():
    world = ShellWorld(venv=False)
    checkout = _build_sandbox(world)
    try:
        result = _run(checkout, world, "--version")
    finally:
        _cleanup(checkout)

    assert result.returncode == 0
    assert result.stdout == "Scrooge Alert unknown\n"
    assert result.stderr == ""


@pytest.mark.parametrize(
    ("words", "expected", "unexpected"),
    (
        (("scrooge-alert", ""), ("run", "--help", "--version"), ("--quiet",)),
        (("scrooge-alert", "run", "--"), ("--help", "--quiet", "--skroutz"), ("--debug",)),
        (
            ("scrooge-alert", "run", "--skroutz", "--"),
            ("--quiet", "--skroutz"),
            ("--help", "--debug"),
        ),
        (("scrooge-alert", "install", "--"), ("--help", "--debug", "--skroutz"), ("--quiet",)),
        (("scrooge-alert", "ping", "--"), ("--help",), ("--debug", "--quiet")),
    ),
)
def test_bash_completion_is_driven_by_public_help(words, expected, unexpected):
    word_literals = " ".join(f'"{word}"' for word in words)
    script = (
        f'source "{Path(__file__).parents[2] / "completions/scrooge-alert.bash"}"; '
        f"COMP_WORDS=({word_literals}); COMP_CWORD={len(words) - 1}; "
        '_scrooge_alert_complete; printf "%s\\n" "${COMPREPLY[@]}"'
    )
    env = os.environ.copy()
    env["PATH"] = f"{Path(__file__).parents[2] / 'scripts'}:{env['PATH']}"
    result = subprocess.run(
        ["/bin/bash", "-c", script],
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    candidates = result.stdout.splitlines()
    assert all(candidate in candidates for candidate in expected)
    assert all(candidate not in candidates for candidate in unexpected)
