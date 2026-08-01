import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
import ui.catalog  # noqa: F401  # initialize catalog before importing its shell harness
from ui.harness.shell import ShellWorld, _build_sandbox, _cleanup, _fake_env

from shell.assertions import assert_task_status

INSTALLED = ShellWorld(
    installed_timers=("skroutz",),
    installed_services=("skroutz",),
)
ACTIVE = replace(
    INSTALLED,
    enabled_timers=("skroutz",),
    active_timers=("skroutz",),
    active_services=("skroutz",),
)


def _run(world: ShellWorld, *args: str):
    checkout = _build_sandbox(world)
    try:
        return _run_checkout(checkout, world, *args)
    finally:
        _cleanup(checkout)


def _run_checkout(checkout: Path, world: ShellWorld, *args: str):
    env = _fake_env(checkout, world)
    env["NO_COLOR"] = "1"
    return subprocess.run(
        ["/bin/sh", str(checkout / "scripts/uninstall.sh"), *args],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


def _assert_standalone_frame(output: str):
    assert output.startswith("\n")
    assert not output.startswith("\n\n")
    assert output.endswith("\n\n")
    assert not output.endswith("\n\n\n")


def test_help_documents_debug_and_preserves_outer_blank_lines():
    result = _run(INSTALLED, "--help")

    assert result.returncode == 0
    assert result.stderr == ""
    assert "Usage: uninstall.sh [-h] [--debug] [--<target> ...]" in result.stdout
    assert "  --debug           show underlying command output" in result.stdout
    _assert_standalone_frame(result.stdout)


@pytest.mark.parametrize(
    "args",
    (
        ("--debug",),
        ("--debug", "--debug"),
        ("--skroutz", "--debug"),
        ("--debug", "--skroutz"),
    ),
)
def test_debug_is_accepted_alone_with_targets_and_when_duplicated(args):
    result = _run(INSTALLED, *args)

    assert result.returncode == 0, result.stdout + result.stderr
    assert_task_status(result.stdout, "v", "[skroutz] Timer and service unit entries removed.")
    _assert_standalone_frame(result.stdout)


@pytest.mark.parametrize(
    "args",
    (
        ("--help", "--debug"),
        ("--debug", "--help", "invalid"),
        ("invalid", "--help", "--debug"),
        ("--skroutz", "--help"),
    ),
)
def test_help_keeps_precedence_with_debug_and_targets_in_every_position(args):
    result = _run(INSTALLED, *args)

    assert result.returncode == 0
    assert result.stderr == ""
    assert "Usage: uninstall.sh" in result.stdout
    assert "[+] Installed units" not in result.stdout
    _assert_standalone_frame(result.stdout)


@pytest.mark.parametrize("args", (("invalid",), ("--",), ("--debug", "invalid")))
def test_invalid_arguments_keep_exit_one_and_use_framed_status_output(args):
    result = _run(INSTALLED, *args)

    assert result.returncode == 1
    assert_task_status(result.stdout, "x", "The command-line arguments are invalid.")
    assert_task_status(result.stdout, "i", "Run scrooge-alert uninstall --help for usage.")
    _assert_standalone_frame(result.stdout)


def test_duplicate_target_keeps_success_semantics_and_runs_once():
    result = _run(INSTALLED, "--skroutz", "--skroutz")

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("Timer and service unit entries removed.") == 1


def test_selected_removal_preserves_venv_and_other_target_units():
    world = ShellWorld(
        plugins=("skroutz", "amazon"),
        installed_timers=("skroutz", "amazon"),
        installed_services=("skroutz", "amazon"),
    )
    checkout = _build_sandbox(world)
    try:
        result = _run_checkout(checkout, world, "--skroutz")

        assert result.returncode == 0, result.stdout + result.stderr
        assert (checkout / "venv").is_dir()
        unit_dir = checkout / "xdg/systemd/user"
        assert not (unit_dir / "skroutz-scraper.timer").exists()
        assert not (unit_dir / "skroutz-scraper.service").exists()
        assert (unit_dir / "amazon-scraper.timer").is_file()
        assert (unit_dir / "amazon-scraper.service").is_file()
    finally:
        _cleanup(checkout)


def test_full_noop_needs_no_systemctl_and_removes_the_venv():
    world = ShellWorld(plugins=(), tools="no-systemctl")
    checkout = _build_sandbox(world)
    try:
        result = _run_checkout(checkout, world)

        assert result.returncode == 0, result.stdout + result.stderr
        assert_task_status(result.stdout, "i", "No installed target timer or service units found.")
        assert_task_status(result.stdout, "v", "Python virtual environment removed.")
        assert not (checkout / "venv").exists()
        _assert_standalone_frame(result.stdout)
    finally:
        _cleanup(checkout)


def test_installed_units_require_systemctl_before_any_removal():
    world = replace(INSTALLED, tools="no-systemctl")
    checkout = _build_sandbox(world)
    try:
        result = _run_checkout(checkout, world)

        assert result.returncode == 1
        assert_task_status(
            result.stdout, "x", "systemctl (systemd) is not installed or not available."
        )
        assert_task_status(result.stdout, "!", "Install systemd, then retry this command.")
        assert (checkout / "venv").is_dir()
        assert len(list((checkout / "xdg/systemd/user").iterdir())) == 2
        _assert_standalone_frame(result.stdout)
    finally:
        _cleanup(checkout)


def test_project_venv_symlink_is_rejected_without_following_or_removing_it(tmp_path):
    checkout = _build_sandbox(INSTALLED)
    external_venv = tmp_path / "external-venv"
    external_venv.mkdir()
    sentinel = external_venv / "keep"
    sentinel.write_text("preserved\n", encoding="utf-8")
    try:
        _cleanup_venv = checkout / "venv"
        shutil.rmtree(_cleanup_venv)
        _cleanup_venv.symlink_to(external_venv)

        result = _run_checkout(checkout, INSTALLED)

        assert result.returncode == 1
        assert "must be a project-owned directory, not a symlink." in result.stdout
        assert _cleanup_venv.is_symlink()
        assert sentinel.read_text(encoding="utf-8") == "preserved\n"
        assert len(list((checkout / "xdg/systemd/user").iterdir())) == 2
        _assert_standalone_frame(result.stdout)
    finally:
        _cleanup(checkout)


def test_normal_mode_hides_subprocess_noise_and_debug_exposes_the_same_noise():
    world = replace(
        ACTIVE,
        systemctl_stdout="injected systemctl stdout",
        systemctl_stderr="injected systemctl stderr",
    )

    normal = _run(world, "--skroutz")
    debug = _run(world, "--debug", "--skroutz")

    assert normal.returncode == debug.returncode == 0
    assert "injected systemctl stdout" not in normal.stdout + normal.stderr
    assert "injected systemctl stderr" not in normal.stdout + normal.stderr
    assert "injected systemctl stdout" in debug.stdout + debug.stderr
    assert "injected systemctl stderr" in debug.stdout + debug.stderr
    assert "skroutz\tSkroutz" not in normal.stdout + normal.stderr
    assert "skroutz\tSkroutz" in debug.stderr


def test_debug_exposes_failing_subprocess_noise_without_changing_exit_status():
    world = replace(
        ACTIVE,
        systemctl_fail=("stop",),
        systemctl_stdout="injected failing systemctl stdout",
        systemctl_stderr="injected failing systemctl stderr",
    )

    normal = _run(world)
    debug = _run(world, "--debug")

    assert normal.returncode == debug.returncode == 1
    assert "injected failing systemctl stdout" not in normal.stdout + normal.stderr
    assert "injected failing systemctl stderr" not in normal.stdout + normal.stderr
    assert "injected failing systemctl stdout" in debug.stdout + debug.stderr
    assert "injected failing systemctl stderr" in debug.stdout + debug.stderr
    assert "[x] [skroutz] Background timer or service could not be disabled safely." in (
        debug.stdout
    )


def test_partial_disable_failure_removes_no_units_and_returns_one():
    world = ShellWorld(
        plugins=("skroutz", "amazon"),
        installed_timers=("skroutz", "amazon"),
        installed_services=("skroutz", "amazon"),
        enabled_timers=("skroutz", "amazon"),
        active_timers=("skroutz", "amazon"),
        systemctl_fail=("disable",),
        systemctl_fail_target="amazon",
    )
    checkout = _build_sandbox(world)
    try:
        result = _run_checkout(checkout, world)

        assert result.returncode == 1
        assert_task_status(result.stdout, "v", "[skroutz] Background timer and service disabled.")
        assert (
            "[x] [amazon] Background timer or service could not be disabled safely."
            in result.stdout
        )
        unit_dir = checkout / "xdg/systemd/user"
        assert sorted(path.name for path in unit_dir.iterdir()) == [
            "amazon-scraper.service",
            "amazon-scraper.timer",
            "skroutz-scraper.service",
            "skroutz-scraper.timer",
        ]
        assert (checkout / "venv").is_dir()
        _assert_standalone_frame(result.stdout)
    finally:
        _cleanup(checkout)


def test_total_disable_failure_is_framed_and_removes_nothing():
    result = _run(replace(ACTIVE, systemctl_fail=("disable",)))

    assert result.returncode == 1
    assert "[x] [skroutz] Background timer or service could not be disabled safely." in (
        result.stdout
    )
    assert "[!] No unit entries were removed" in result.stdout
    assert "[+] Installed units" not in result.stdout
    _assert_standalone_frame(result.stdout)


def test_daemon_reload_failure_keeps_exit_one_after_unit_removal():
    world = replace(INSTALLED, systemctl_fail=("daemon-reload",))
    checkout = _build_sandbox(world)
    try:
        result = _run_checkout(checkout, world, "--skroutz")

        assert result.returncode == 1
        assert_task_status(result.stdout, "x", "The systemd user manager could not be reloaded.")
        assert "Run systemctl --user daemon-reload" in result.stdout
        unit_dir = checkout / "xdg/systemd/user"
        assert list(unit_dir.iterdir()) == []
        assert (checkout / "venv").is_dir()
        _assert_standalone_frame(result.stdout)
    finally:
        _cleanup(checkout)
