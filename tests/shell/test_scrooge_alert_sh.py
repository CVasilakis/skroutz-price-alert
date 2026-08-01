from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import ui.catalog  # noqa: F401  # initialize the shared shell catalog
from ui.harness.shell import ShellWorld, _build_sandbox, _cleanup, _fake_env

from shell.assertions import shell_outer_padding_errors


def _run(checkout: Path, world: ShellWorld, *args: str, cwd: Path | None = None):
    env = _fake_env(checkout, world)
    env["NO_COLOR"] = "1"
    return subprocess.run(
        [str(checkout / "scrooge-alert"), *args],
        cwd=cwd or checkout,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def test_bare_and_global_help_are_stable_without_installation():
    world = ShellWorld(venv=False)
    checkout = _build_sandbox(world)
    try:
        bare = _run(checkout, world)
        short = _run(checkout, world, "-h")
        explicit = _run(checkout, world, "--help")
    finally:
        _cleanup(checkout)

    assert bare.returncode == short.returncode == explicit.returncode == 0
    assert bare.stdout == short.stdout == explicit.stdout
    assert shell_outer_padding_errors(bare.stdout) == ()
    assert "Usage: ./scrooge-alert <command> [options]" in bare.stdout
    assert "Commands:\n" in bare.stdout
    assert "  --version " in bare.stdout


@pytest.mark.parametrize(
    "args",
    (
        ("--help",),
        ("run", "--help"),
        ("ping", "--help"),
        ("status", "--help"),
        ("install", "--help"),
        ("enable", "--help"),
        ("disable", "--help"),
        ("stop", "--help"),
        ("schedule", "--help"),
        ("update", "--help"),
        ("uninstall", "--help"),
    ),
)
def test_help_has_no_color_when_color_is_enabled(args: tuple[str, ...]):
    world = ShellWorld()
    checkout = _build_sandbox(world)
    try:
        env = _fake_env(checkout, world)
        env.pop("NO_COLOR", None)
        env["CLICOLOR_FORCE"] = "1"
        result = subprocess.run(
            [str(checkout / "scrooge-alert"), *args],
            cwd=checkout,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
    finally:
        _cleanup(checkout)

    assert result.returncode == 0
    assert "Usage: ./scrooge-alert" in result.stdout
    assert "\x1b[" not in result.stdout


@pytest.mark.parametrize(
    ("command", "relative_script", "public_args", "expected_args"),
    (
        ("run", "scripts/run.sh", ("--quiet", "--skroutz"), ("--quiet", "--skroutz")),
        ("ping", "scripts/ping.sh", (), ()),
        ("status", "scripts/status.sh", (), ()),
        (
            "install",
            "scripts/install.sh",
            ("--debug", "--skroutz"),
            ("--debug", "--skroutz"),
        ),
        (
            "enable",
            "scripts/enable.sh",
            ("--debug", "--skroutz"),
            ("--debug", "--skroutz"),
        ),
        (
            "disable",
            "scripts/disable.sh",
            ("--debug", "--skroutz"),
            ("--debug", "--skroutz"),
        ),
        (
            "stop",
            "scripts/stop.sh",
            ("--debug", "--skroutz"),
            ("--debug", "--skroutz"),
        ),
        (
            "schedule",
            "scripts/schedule.sh",
            ("--debug", "--skroutz"),
            ("--debug", "--skroutz"),
        ),
        ("update", "scripts/update.sh", ("--debug",), ("--debug",)),
        (
            "uninstall",
            "scripts/uninstall.sh",
            ("--debug", "--skroutz"),
            ("--debug", "--skroutz"),
        ),
    ),
)
def test_commands_exec_the_existing_owner_and_preserve_status(
    command: str,
    relative_script: str,
    public_args: tuple[str, ...],
    expected_args: tuple[str, ...],
):
    world = ShellWorld()
    checkout = _build_sandbox(world)
    try:
        owner = checkout / relative_script
        owner.write_text(
            "#!/bin/sh\n"
            "printf '%s\\n' \"$SCROOGE_PUBLIC_COMMAND\"\n"
            'for arg in "$@"; do printf \'arg:%s\\n\' "$arg"; done\n'
            "exit 37\n",
            encoding="utf-8",
        )
        owner.chmod(0o755)
        result = _run(checkout, world, command, *public_args)
    finally:
        _cleanup(checkout)

    assert result.returncode == 37
    assert result.stdout.splitlines()[0] == command
    assert result.stdout.splitlines()[1:] == [f"arg:{arg}" for arg in expected_args]


def test_command_help_is_canonical_and_keeps_dynamic_targets():
    world = ShellWorld(plugins=("skroutz", "insomnia"))
    checkout = _build_sandbox(world)
    try:
        run_help = _run(checkout, world, "run", "--help")
        install_help = _run(checkout, world, "install", "--help")
        status_help = _run(checkout, world, "status", "--help")
    finally:
        _cleanup(checkout)

    assert run_help.returncode == install_help.returncode == status_help.returncode == 0
    assert "Usage: ./scrooge-alert run" in run_help.stdout
    assert "--skroutz" in run_help.stdout and "--insomnia" in run_help.stdout
    assert "Usage: ./scrooge-alert install" in install_help.stdout
    assert "--skroutz" in install_help.stdout and "--insomnia" in install_help.stdout
    assert "Usage: ./scrooge-alert status [--help]" in status_help.stdout
    assert "--skroutz" not in status_help.stdout


@pytest.mark.parametrize("target", ("ping", "status"))
def test_command_names_remain_unambiguous_as_dynamic_targets(target: str):
    world = ShellWorld(plugins=("ping", "status"))
    checkout = _build_sandbox(world)
    try:
        command = _run(checkout, world, target)
        selected_target = _run(checkout, world, "run", f"--{target}")
        run_help = _run(checkout, world, "run", "--help")
        install_help = _run(checkout, world, "install", "--help")
    finally:
        _cleanup(checkout)

    assert command.returncode == selected_target.returncode == 0
    assert command.stdout.rstrip().endswith(f"src/core/{target}.py")
    assert selected_target.stdout.rstrip().endswith(f"src/core/run.py --{target}")
    assert f"--{target}" in run_help.stdout
    assert f"--{target}" in install_help.stdout


def test_absolute_invocation_resolves_checkout_from_another_directory(tmp_path: Path):
    world = ShellWorld()
    checkout = _build_sandbox(world)
    try:
        result = _run(checkout, world, "run", "--help", cwd=tmp_path)
    finally:
        _cleanup(checkout)

    assert result.returncode == 0
    assert "Usage: ./scrooge-alert run" in result.stdout


def test_version_is_local_and_falls_back_when_venv_is_missing():
    installed_world = ShellWorld()
    installed_checkout = _build_sandbox(installed_world)
    missing_world = ShellWorld(venv=False)
    missing_checkout = _build_sandbox(missing_world)
    try:
        installed = _run(installed_checkout, installed_world, "--version")
        missing = _run(missing_checkout, missing_world, "--version")
    finally:
        _cleanup(installed_checkout)
        _cleanup(missing_checkout)

    assert installed.stdout == "Scrooge Alert 1.2.3\n"
    assert missing.stdout == "Scrooge Alert unknown\n"
    assert installed.returncode == missing.returncode == 0


@pytest.mark.parametrize("owner_kind", ("missing", "symlink", "not_executable"))
def test_unsafe_command_owner_is_rejected(owner_kind: str):
    world = ShellWorld()
    checkout = _build_sandbox(world)
    owner = checkout / "scripts/enable.sh"
    try:
        if owner_kind == "missing":
            owner.unlink()
        elif owner_kind == "symlink":
            target = checkout / "unsafe-owner"
            target.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            target.chmod(0o755)
            owner.unlink()
            owner.symlink_to(target)
        else:
            owner.chmod(0o644)
        result = _run(checkout, world, "enable")
    finally:
        _cleanup(checkout)

    assert result.returncode == 1
    assert "Command owner is missing or unsafe" in result.stderr
    assert shell_outer_padding_errors(result.stderr) == ()


def test_unknown_command_fails_with_public_guidance():
    world = ShellWorld()
    checkout = _build_sandbox(world)
    try:
        result = _run(checkout, world, "scrape")
    finally:
        _cleanup(checkout)

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == (
        "\nError: Unknown command: scrape.\nRun ./scrooge-alert --help for usage.\n\n"
    )
    assert shell_outer_padding_errors(result.stderr) == ()


def test_install_does_not_create_shell_or_launcher_artifacts():
    world = ShellWorld(config_files=("skroutz.json", "general.json"))
    checkout = _build_sandbox(world)
    try:
        result = _run(checkout, world, "install")
        home = checkout / "home"
        outside_artifacts = (
            home / ".local/bin/scrooge-alert",
            home / ".profile",
            home / ".bashrc",
            home / ".zshrc",
            home / ".local/share/bash-completion",
            home / ".local/share/fish",
        )
        created = [path for path in outside_artifacts if os.path.lexists(path)]
    finally:
        _cleanup(checkout)

    assert result.returncode == 0, result.stdout + result.stderr
    assert created == []


_INSTALLED_WORLD = ShellWorld(
    installed_timers=("skroutz",),
    installed_services=("skroutz",),
    enabled_timers=("skroutz",),
    active_timers=("skroutz",),
    config_files=("skroutz.json", "general.json"),
)


@pytest.mark.parametrize(
    ("command", "args", "world", "expected"),
    (
        pytest.param(
            "run",
            ("--quiet", "--skroutz"),
            ShellWorld(),
            "src/core/run.py --quiet --skroutz",
            id="run",
        ),
        pytest.param("ping", (), ShellWorld(), "src/core/ping.py", id="ping"),
        pytest.param("status", (), ShellWorld(), "src/core/status.py", id="status"),
        pytest.param(
            "install",
            (),
            ShellWorld(config_files=("skroutz.json", "general.json")),
            "Installation complete.",
            id="install",
        ),
        pytest.param(
            "enable",
            (),
            ShellWorld(
                installed_timers=("skroutz",),
                installed_services=("skroutz",),
                config_files=("skroutz.json", "general.json"),
            ),
            "Background schedule enabled and started.",
            id="enable",
        ),
        pytest.param(
            "disable", (), _INSTALLED_WORLD, "Background execution disabled.", id="disable"
        ),
        pytest.param(
            "stop",
            (),
            ShellWorld(installed_services=("skroutz",), active_services=("skroutz",)),
            "Active execution stopped.",
            id="stop",
        ),
        pytest.param(
            "schedule",
            (),
            _INSTALLED_WORLD,
            "No eligible timer changes were required.",
            id="schedule",
        ),
        pytest.param(
            "update",
            (),
            _INSTALLED_WORLD,
            "Update complete. You are now running origin/main.",
            id="update",
        ),
        pytest.param(
            "uninstall",
            ("--skroutz",),
            _INSTALLED_WORLD,
            "Timer and service unit entries removed.",
            id="uninstall",
        ),
    ),
)
def test_public_commands_reach_real_owners(
    command: str,
    args: tuple[str, ...],
    world: ShellWorld,
    expected: str,
):
    checkout = _build_sandbox(world)
    try:
        result = _run(checkout, world, command, *args)
    finally:
        _cleanup(checkout)

    assert result.returncode == 0, result.stdout + result.stderr
    assert expected in result.stdout
