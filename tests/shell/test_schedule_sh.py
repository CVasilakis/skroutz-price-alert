import subprocess
from dataclasses import replace

import pytest
import ui.catalog  # noqa: F401  # initialize catalog before importing its shell harness
from ui.harness.shell import ShellWorld, _build_sandbox, _cleanup, _fake_env

from shell.assertions import assert_task_status

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
    assert_task_status(
        result.stdout, "v", "[skroutz] Timer updated and its previous state preserved."
    )
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
    assert_task_status(result.stdout, "x", "The command-line arguments are invalid.")
    assert_task_status(result.stdout, "i", "Run ./scrooge-alert schedule --help for usage.")
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
    assert_task_status(debug.stdout, "x", "One or more timer schedules could not be applied.")


def test_successful_change_is_framed_and_reports_each_phase():
    result = _run(CHANGED)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "[+] Execution intervals" in result.stdout
    assert_task_status(result.stdout, "i", "[skroutz] Timer schedule change queued.")
    assert "[+] Timer updates" in result.stdout
    assert_task_status(
        result.stdout, "v", "[skroutz] Timer updated and its previous state preserved."
    )
    assert "[+] Schedule result" in result.stdout
    _assert_standalone_frame(result.stdout)


def test_no_op_is_framed_and_does_not_render_update_phase():
    result = _run(INSTALLED)

    assert result.returncode == 0, result.stdout + result.stderr
    assert_task_status(
        result.stdout, "i", "[skroutz] Timer already matches the configured interval."
    )
    assert "[+] Timer updates" not in result.stdout
    assert_task_status(result.stdout, "v", "No eligible timer changes were required.")
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
    assert_task_status(result.stdout, "i", "[skroutz] Timer schedule change queued.")
    assert_task_status(
        result.stdout, "x", "[amazon] Remove unsupported keys from `config/amazon.json`."
    )
    assert_task_status(
        result.stdout, "v", "[skroutz] Timer updated and its previous state preserved."
    )
    assert_task_status(result.stdout, "v", "Updated targets: skroutz")
    _assert_standalone_frame(result.stdout)


def test_total_transaction_failure_is_framed_and_preserves_exit_one():
    result = _run(replace(CHANGED, systemctl_fail=("restart",), active_timers=("skroutz",)))

    assert result.returncode == 1
    assert_task_status(result.stdout, "x", "One or more timer schedules could not be applied.")
    assert_task_status(result.stdout, "i", "Previous timer files and states were restored.")
    assert "[+] Schedule result" not in result.stdout
    _assert_standalone_frame(result.stdout)


def test_each_updated_timer_receives_its_own_resolved_calendar():
    """The only test that reads what schedule.sh actually wrote.

    Every other assertion here is on rendered status lines, and those stay
    identical whether the right cadence reaches the timer or not:
    validate_staged_units checks the staged file against the same calendar the
    transaction was handed, so a wrong value validates against itself. Two
    targets with distinct cadences are the point -- one target could pass while
    reading its own row by luck, but a lost tab, a swapped row, or a hardcoded
    calendar all cross the targets and fail here.
    """
    world = ShellWorld(
        plugins=("skroutz", "amazon"),
        installed_timers=("skroutz", "amazon"),
        installed_services=("skroutz", "amazon"),
        schedules={"skroutz": "daily", "amazon": "*:0/15"},
    )
    checkout = _build_sandbox(world)
    try:
        env = _fake_env(checkout, world)
        env["NO_COLOR"] = "1"
        result = subprocess.run(
            ["/bin/sh", str(checkout / "scripts/schedule.sh")],
            cwd=checkout,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        unit_dir = checkout / "xdg" / "systemd" / "user"
        for target, calendar in (("skroutz", "daily"), ("amazon", "*:0/15")):
            written = (unit_dir / f"{target}-scraper.timer").read_text(encoding="utf-8")
            assert f"OnCalendar={calendar}\n" in written, written
            assert f"Unit={target}-scraper.service\n" in written, written
    finally:
        _cleanup(checkout)


def test_unresolvable_schedule_metadata_refuses_before_reading_any_timer():
    """The one reachable path to the shared metadata refusal.

    Selection needs only the catalog, so it succeeds; the separate `schedules`
    command behind prime_plugin_schedules is what fails. The refusal must land
    before the per-target loop, so no installed timer is read or rewritten.
    """
    world = replace(INSTALLED, schedule_report_fails=True)

    normal = _run(world)
    debug = _run(world, "--debug")

    assert normal.returncode == debug.returncode == 1
    for result in (normal, debug):
        assert_task_status(result.stdout, "x", "Failed to resolve target scheduling metadata.")
        assert "[skroutz]" not in result.stdout
        assert "Timer updates" not in result.stdout
