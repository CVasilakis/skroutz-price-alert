import subprocess
from dataclasses import replace

import pytest
import ui.catalog  # noqa: F401  # initialize catalog before importing its shell harness
from ui.harness.shell import ShellWorld, _build_sandbox, _cleanup, _fake_env

INSTALLED = ShellWorld(installed_timers=("skroutz",), installed_services=("skroutz",))
CHANGED = replace(INSTALLED, schedules={"skroutz": "daily"})


def _run(world: ShellWorld, *args: str):
    checkout = _build_sandbox(world)
    try:
        env = _fake_env(checkout, world)
        env["NO_COLOR"] = "1"
        return subprocess.run(
            ["/bin/sh", str(checkout / "scripts/schedule.sh"), *args],
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
    assert "Usage: schedule.sh [-h] [--debug] [--<target> ...]" in result.stdout
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
    result = _run(CHANGED, *args)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[v] [skroutz] Timer updated and its previous state preserved." in result.stdout
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
    assert "Usage: schedule.sh" in result.stdout
    assert "[+] Execution intervals" not in result.stdout
    _assert_standalone_frame(result.stdout)


@pytest.mark.parametrize("args", (("invalid",), ("--",), ("--debug", "invalid")))
def test_invalid_arguments_keep_exit_one_and_use_framed_status_output(args):
    result = _run(INSTALLED, *args)

    assert result.returncode == 1
    assert "[x] The command-line arguments are invalid." in result.stdout
    assert "[i] Run ./scripts/schedule.sh --help for usage." in result.stdout
    _assert_standalone_frame(result.stdout)


def test_duplicate_target_keeps_success_semantics_and_runs_once():
    result = _run(CHANGED, "--skroutz", "--skroutz")

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("Timer updated and its previous state preserved.") == 1


def test_normal_mode_hides_subprocess_noise_and_debug_exposes_the_same_noise():
    world = replace(
        CHANGED,
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
    assert "skroutz\tdaily\tok" in debug.stderr


def test_debug_exposes_failing_subprocess_noise_without_changing_exit_status():
    world = replace(
        CHANGED,
        systemctl_fail=("daemon-reload",),
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
    assert "[x] One or more timer schedules could not be applied." in debug.stdout


def test_successful_change_is_framed_and_reports_each_phase():
    result = _run(CHANGED)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[+] Execution intervals" in result.stdout
    assert "[i] [skroutz] Timer schedule change queued." in result.stdout
    assert "[+] Timer updates" in result.stdout
    assert "[v] [skroutz] Timer updated and its previous state preserved." in result.stdout
    assert "[+] Schedule result" in result.stdout
    _assert_standalone_frame(result.stdout)


def test_no_op_is_framed_and_does_not_render_update_phase():
    result = _run(INSTALLED)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[i] [skroutz] Timer already matches the configured interval." in result.stdout
    assert "[+] Timer updates" not in result.stdout
    assert "[v] No eligible timer changes were required." in result.stdout
    _assert_standalone_frame(result.stdout)


def test_partial_config_failure_updates_healthy_target_and_returns_fifteen():
    world = ShellWorld(
        plugins=("skroutz", "amazon"),
        installed_timers=("skroutz", "amazon"),
        installed_services=("skroutz", "amazon"),
        schedules={"skroutz": "daily", "amazon": "hourly"},
        schedule_errors={"amazon": "Remove unsupported keys from `config/amazon.json`."},
    )

    result = _run(world)

    assert result.returncode == 15
    assert "[i] [skroutz] Timer schedule change queued." in result.stdout
    assert "[x] [amazon] Remove unsupported keys from `config/amazon.json`." in result.stdout
    assert "[v] [skroutz] Timer updated and its previous state preserved." in result.stdout
    assert "[v] Updated targets: skroutz" in result.stdout
    _assert_standalone_frame(result.stdout)


def test_total_transaction_failure_is_framed_and_preserves_exit_one():
    result = _run(replace(CHANGED, systemctl_fail=("restart",), active_timers=("skroutz",)))

    assert result.returncode == 1
    assert "[x] One or more timer schedules could not be applied." in result.stdout
    assert "[i] Previous timer files and states were restored." in result.stdout
    assert "[+] Schedule result" not in result.stdout
    _assert_standalone_frame(result.stdout)
