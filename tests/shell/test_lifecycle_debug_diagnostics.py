"""Drift guard for the one thing --debug reveals before any command runs.

Every target-selecting lifecycle script advertises ``--debug`` as "show underlying
command output", and ``parse_target_flags`` is the only shared helper whose output
is an *argument* diagnostic rather than an external command's. It gates that
output itself, because a caller-side ``run_action`` wrapper would read DEBUG_MODE
to choose redirection before the parse that sets it. These tests pin both halves:
the diagnostic reaches stderr under --debug, and stays suppressed without it.
"""

import subprocess

import pytest
import ui.catalog  # noqa: F401  # initialize catalog before importing its shell harness
from ui.harness.shell import ShellWorld, _build_sandbox, _cleanup, _fake_env

#: One world that satisfies every script's preflight far enough to reach the parse.
WORLD = ShellWorld(
    config_files=("skroutz.json", "general.json"),
    installed_timers=("skroutz",),
    installed_services=("skroutz",),
)

SCRIPTS = (
    "scripts/install.sh",
    "scripts/enable.sh",
    "scripts/disable.sh",
    "scripts/stop.sh",
    "scripts/schedule.sh",
    "scripts/uninstall.sh",
)

ARGUMENTS = (
    pytest.param("invalid", "Error: Invalid argument: invalid", id="positional"),
    pytest.param("--", "Error: Invalid argument: --", id="bare-double-dash"),
    pytest.param(
        "--Bad",
        "Error: Invalid target 'Bad' (expected a nonblank snake_case name).",
        id="malformed-target",
    ),
)


def _run(script: str, *args: str):
    checkout = _build_sandbox(WORLD)
    try:
        env = _fake_env(checkout, WORLD)
        env["NO_COLOR"] = "1"
        return subprocess.run(
            ["/bin/sh", str(checkout / script), *args],
            cwd=checkout,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
    finally:
        _cleanup(checkout)


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize(("argument", "diagnostic"), ARGUMENTS)
def test_debug_surfaces_the_underlying_argument_diagnostic(script, argument, diagnostic):
    result = _run(script, "--debug", argument)

    assert result.returncode == 1
    assert diagnostic in result.stderr
    assert "The command-line arguments are invalid." in result.stdout


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize(("argument", "diagnostic"), ARGUMENTS)
def test_normal_mode_keeps_the_underlying_argument_diagnostic_quiet(script, argument, diagnostic):
    result = _run(script, argument)

    assert result.returncode == 1
    assert diagnostic not in result.stdout + result.stderr
    assert "The command-line arguments are invalid." in result.stdout
