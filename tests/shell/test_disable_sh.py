import subprocess
from dataclasses import replace

import pytest
import ui.catalog  # noqa: F401  # initialize catalog before importing its shell harness
from ui.harness.shell import ShellWorld, _build_sandbox, _cleanup, _fake_env

INSTALLED = ShellWorld(installed_timers=("skroutz",), installed_services=("skroutz",))
ENABLED = replace(
    INSTALLED,
    enabled_timers=("skroutz",),
    active_timers=("skroutz",),
)


def _run(world: ShellWorld, *args: str):
    checkout = _build_sandbox(world)
    try:
        env = _fake_env(checkout, world)
        env["NO_COLOR"] = "1"
        return subprocess.run(
            ["/bin/sh", str(checkout / "scripts/disable.sh"), *args],
            cwd=checkout,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
    finally:
        _cleanup(checkout)


def _assert_standalone_frame(output: str):
    assert output.startswith("\n")
    assert not output.startswith("\n\n")
    assert output.endswith("\n\n")
    assert not output.endswith("\n\n\n")


def test_help_documents_debug_and_preserves_outer_blank_lines():
    result = _run(INSTALLED, "--help")

    assert result.returncode == 0
    assert result.stderr == ""
    assert "Usage: disable.sh [-h] [--debug] [--<target> ...]" in result.stdout
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
    result = _run(ENABLED, *args)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[v] [skroutz] Background execution disabled." in result.stdout
    _assert_standalone_frame(result.stdout)


@pytest.mark.parametrize(
    "args",
    (
        ("--help", "--debug"),
        ("--debug", "--help", "invalid"),
        ("invalid", "--help", "--debug"),
    ),
)
def test_help_keeps_precedence_with_debug_in_every_position(args):
    result = _run(INSTALLED, *args)

    assert result.returncode == 0
    assert result.stderr == ""
    assert "Usage: disable.sh" in result.stdout
    assert "[+] Background execution" not in result.stdout
    _assert_standalone_frame(result.stdout)


@pytest.mark.parametrize("args", (("invalid",), ("--",), ("--debug", "invalid")))
def test_invalid_arguments_keep_exit_one_and_use_framed_status_output(args):
    result = _run(INSTALLED, *args)

    assert result.returncode == 1
    assert "[x] The command-line arguments are invalid." in result.stdout
    assert "[i] Run ./scripts/disable.sh --help for usage." in result.stdout
    _assert_standalone_frame(result.stdout)


def test_duplicate_target_keeps_success_semantics_and_runs_once():
    result = _run(ENABLED, "--skroutz", "--skroutz")

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("Background execution disabled.") == 1


def test_normal_mode_hides_subprocess_noise_and_debug_exposes_the_same_noise():
    world = replace(
        ENABLED,
        systemctl_stdout="injected systemctl stdout",
        systemctl_stderr="injected systemctl stderr",
    )

    normal = _run(world)
    debug = _run(world, "--debug")

    assert normal.returncode == debug.returncode == 0
    assert "injected systemctl stdout" not in normal.stdout + normal.stderr
    assert "injected systemctl stderr" not in normal.stdout + normal.stderr
    assert "injected systemctl stdout" in debug.stdout + debug.stderr
    assert "injected systemctl stderr" in debug.stdout + debug.stderr
    assert "skroutz\tSkroutz" not in normal.stdout + normal.stderr
    assert "skroutz\tSkroutz" in debug.stderr


def test_debug_exposes_failing_subprocess_noise_without_changing_exit_status():
    world = replace(
        ENABLED,
        systemctl_fail=("disable",),
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
    assert "[x] [skroutz] Background execution was not fully disabled." in debug.stdout


def test_partial_failure_continues_to_other_targets_and_returns_one():
    world = ShellWorld(
        plugins=("skroutz", "amazon"),
        installed_timers=("skroutz", "amazon"),
        installed_services=("skroutz", "amazon"),
        enabled_timers=("skroutz", "amazon"),
        active_timers=("skroutz", "amazon"),
        systemctl_fail=("disable",),
        systemctl_fail_target="amazon",
    )

    result = _run(world)

    assert result.returncode == 1
    assert "[v] [skroutz] Background execution disabled." in result.stdout
    assert "[x] [amazon] Background execution was not fully disabled." in result.stdout
    _assert_standalone_frame(result.stdout)


def test_total_failure_is_framed_and_preserves_exit_one():
    result = _run(replace(ENABLED, systemctl_fail=("disable",)))

    assert result.returncode == 1
    assert "[x] [skroutz] Background execution was not fully disabled." in result.stdout
    assert "[+] Optional controls" not in result.stdout
    _assert_standalone_frame(result.stdout)
