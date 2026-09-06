import subprocess

import pytest
import ui.catalog  # noqa: F401  # initialize catalog before importing its shell harness
from ui.harness.shell import ShellWorld, _build_sandbox, _cleanup, _fake_env

from shell.assertions import assert_task_status


def _run(world: ShellWorld, *args: str, extra_env: dict[str, str] | None = None):
    checkout = _build_sandbox(world)
    try:
        env = _fake_env(checkout, world)
        env.update(extra_env or {})
        env["NO_COLOR"] = "1"
        return subprocess.run(
            ["/bin/sh", str(checkout / "scripts/install.sh"), *args],
            cwd=checkout,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )
    finally:
        _cleanup(checkout)


CONFIGURED = ShellWorld(config_files=("skroutz.json", "general.json"))


def _assert_standalone_frame(output: str):
    assert output.startswith("\n")
    assert not output.startswith("\n\n")
    assert output.endswith("\n\n")
    assert not output.endswith("\n\n\n")


def test_help_documents_debug_and_preserves_outer_blank_lines():
    result = _run(CONFIGURED, "--help")

    assert result.returncode == 0
    assert result.stderr == ""
    assert "Usage: install.sh [-h] [--debug] [--<target> ...]" in result.stdout
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
    result = _run(CONFIGURED, *args)

    assert result.returncode == 0, result.stdout + result.stderr
    assert_task_status(result.stdout, "v", "Installation complete.")
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
    result = _run(CONFIGURED, *args)

    assert result.returncode == 0
    assert result.stderr == ""
    assert "Usage: install.sh" in result.stdout
    assert "[+] Installation" not in result.stdout
    _assert_standalone_frame(result.stdout)


@pytest.mark.parametrize("args", (("invalid",), ("--",), ("--debug", "invalid")))
def test_invalid_arguments_keep_exit_one_and_use_framed_status_output(args):
    result = _run(CONFIGURED, *args)

    assert result.returncode == 1
    assert_task_status(result.stdout, "x", "The command-line arguments are invalid.")
    assert_task_status(result.stdout, "i", "Run ./scrooge-alert install --help for usage.")
    _assert_standalone_frame(result.stdout)


def test_duplicate_target_keeps_success_semantics_and_runs_once():
    world = ShellWorld(
        config_files=("skroutz.json", "general.json"),
        requirements={"skroutz": "/unused/normalized/by-harness"},
    )
    result = _run(world, "--skroutz", "--skroutz")

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.count("[skroutz] Installed private dependencies.") == 1


def test_normal_mode_hides_subprocess_noise_and_debug_exposes_the_same_noise():
    world = ShellWorld(
        config_files=("skroutz.json", "general.json"),
        pip_stdout="injected pip stdout",
        pip_stderr="injected pip stderr",
    )

    normal = _run(world)
    debug = _run(world, "--debug")

    assert normal.returncode == debug.returncode == 0
    assert "injected pip stdout" not in normal.stdout + normal.stderr
    assert "injected pip stderr" not in normal.stdout + normal.stderr
    assert "injected pip stdout" in debug.stdout + debug.stderr
    assert "injected pip stderr" in debug.stdout + debug.stderr


def test_debug_exposes_failing_subprocess_noise_without_changing_exit_status():
    world = ShellWorld(
        config_files=("skroutz.json", "general.json"),
        pip_fail="upgrade",
        pip_stdout="injected failing pip stdout",
        pip_stderr="injected failing pip stderr",
    )

    normal = _run(world)
    debug = _run(world, "--debug")

    assert normal.returncode == debug.returncode == 1
    assert "injected failing pip stdout" not in normal.stdout + normal.stderr
    assert "injected failing pip stderr" not in normal.stdout + normal.stderr
    assert "injected failing pip stdout" in debug.stdout + debug.stderr
    assert "injected failing pip stderr" in debug.stdout + debug.stderr
    assert_task_status(debug.stdout, "x", "Packaging tools could not be updated.")


def test_deferred_install_inherits_debug_without_adding_outer_completion():
    result = _run(
        CONFIGURED,
        "--skroutz",
        extra_env={
            "SCROOGE_INSTALL_CONTEXT": "deferred",
            "SCROOGE_INTERNAL_UPDATE": "1",
            "SCROOGE_INTERNAL_DEBUG": "1",
        },
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Installation complete." not in result.stdout
    assert not result.stdout.endswith("\n\n")
    assert "skroutz\tSkroutz" in result.stderr


@pytest.mark.parametrize(
    ("config_files", "expected", "unexpected"),
    (
        (
            ("general.json",),
            "cp src/core/scrapers/plugins/skroutz/config.example.json config/skroutz.json",
            "cp src/core/general/config.example.json config/general.json",
        ),
        (
            ("skroutz.json",),
            "cp src/core/general/config.example.json config/general.json",
            "cp src/core/scrapers/plugins/skroutz/config.example.json config/skroutz.json",
        ),
    ),
)
def test_configuration_commands_include_only_missing_files(config_files, expected, unexpected):
    result = _run(ShellWorld(config_files=config_files))

    assert result.returncode == 0, result.stdout + result.stderr
    assert f"        {expected}\n" in result.stdout
    assert unexpected not in result.stdout
    assert "mkdir -p config" not in result.stdout
    assert "<BASE_DIR>" not in result.stdout


def test_missing_config_directory_adds_prepare_command_without_wrapping_commands():
    result = _run(
        ShellWorld(config_dir=False),
        extra_env={"COLUMNS": "40"},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    lines = result.stdout.splitlines()
    assert "        mkdir -p config" in lines
    assert (
        "        cp src/core/scrapers/plugins/skroutz/config.example.json config/skroutz.json"
    ) in lines
    assert ("        cp src/core/general/config.example.json config/general.json") in lines


def test_install_prints_configuration_commands_without_creating_files():
    world = ShellWorld(config_dir=False)
    checkout = _build_sandbox(world)
    try:
        env = _fake_env(checkout, world)
        env["NO_COLOR"] = "1"
        result = subprocess.run(
            ["/bin/sh", str(checkout / "scripts/install.sh")],
            cwd=checkout,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert "mkdir -p config" in result.stdout
        assert not (checkout / "config").exists()
    finally:
        _cleanup(checkout)


def test_install_is_independent_of_the_working_directory():
    """Every project path install.sh reads or creates must be absolute.

    A relative "venv" would make an install launched from a subdirectory create
    the environment in the caller's directory and then fail to find
    requirements.txt, and it would spell the venv differently from
    reject_project_venv_symlink's "$BASE_DIR/venv" guard. The dispatcher does
    not normalize the working directory, so nothing else pins this.
    """
    world = ShellWorld(config_files=("skroutz.json", "general.json"))
    checkout = _build_sandbox(world)
    try:
        caller_dir = checkout / "config"
        env = _fake_env(checkout, world)
        env["NO_COLOR"] = "1"
        result = subprocess.run(
            ["/bin/sh", str(checkout / "scripts/install.sh")],
            cwd=caller_dir,
            env=env,
            text=True,
            capture_output=True,
            timeout=30,
        )

        assert result.returncode == 0, result.stdout + result.stderr
        assert_task_status(result.stdout, "v", "Installation complete.")
        assert not (caller_dir / "venv").exists()
        assert sorted(p.name for p in caller_dir.iterdir()) == [
            "general.json",
            "skroutz.json",
        ]
    finally:
        _cleanup(checkout)


def _run_in(checkout, world: ShellWorld, *args: str):
    """install.sh against a checkout the caller has already tampered with."""
    env = _fake_env(checkout, world)
    env["NO_COLOR"] = "1"
    return subprocess.run(
        ["/bin/sh", str(checkout / "scripts/install.sh"), *args],
        cwd=checkout,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )


# Only files install.sh does not source itself: a missing lib/common.sh or
# lib/systemd.sh fails at the `.` line above with the shell's own error, so those
# entries in the required-file list are unreachable from here by construction.
@pytest.mark.parametrize(
    "required_file",
    ("scrooge-alert", "requirements.txt", "scripts/run.sh", "scripts/lib/runtime.sh"),
)
@pytest.mark.parametrize("kind", ("missing", "symlink"))
def test_unsafe_required_project_file_is_refused_before_any_provisioning(
    required_file: str, kind: str
):
    """require_regular_owned_file's fail-closed arm, at install.sh's first use.

    The symlink half is the one that fails open if the guard is weakened: the link
    resolves, so every later read succeeds and the install provisions units from a
    file the checkout does not own. Asserting the empty unit directory pins that
    the refusal lands before provisioning rather than after it.
    """
    world = ShellWorld(config_files=("skroutz.json", "general.json"))
    checkout = _build_sandbox(world)
    try:
        victim = checkout / required_file
        victim.unlink()
        if kind == "symlink":
            outside = checkout / "outside-the-checkout"
            outside.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            outside.chmod(0o755)
            victim.symlink_to(outside)
        result = _run_in(checkout, world)

        assert result.returncode == 1
        assert_task_status(
            result.stdout,
            "x",
            f"Required project file '{required_file}' is missing or unsafe.",
        )
        assert list((checkout / "xdg/systemd/user").iterdir()) == []
        if kind == "symlink":
            assert victim.is_symlink()
    finally:
        _cleanup(checkout)


@pytest.mark.parametrize("kind", ("missing", "symlink"))
def test_unsafe_plugin_requirements_file_is_refused_for_the_named_target(kind: str):
    """The same guard on the catalog-computed path, which names its target.

    This one is per-selected-plugin rather than per-checkout, so the message
    carries the target and the failure has to survive being reached from inside
    the IFS-split loop over the requirements pairs.
    """
    world = ShellWorld(
        config_files=("skroutz.json", "general.json"),
        requirements={"skroutz": "/normalized/by/the-harness"},
    )
    checkout = _build_sandbox(world)
    try:
        victim = checkout / "src/core/scrapers/plugins/skroutz/requirements.txt"
        victim.unlink()
        if kind == "symlink":
            outside = checkout / "outside-requirements.txt"
            outside.write_text("requests\n", encoding="utf-8")
            victim.symlink_to(outside)
        result = _run_in(checkout, world)

        assert result.returncode == 1
        assert_task_status(
            result.stdout, "x", "[skroutz] Its requirements file is missing or unsafe."
        )
        assert list((checkout / "xdg/systemd/user").iterdir()) == []
    finally:
        _cleanup(checkout)
