import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _sandboxed_migrate_script(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    scripts = project / "scripts"
    libraries = scripts / "lib"
    python = project / "venv" / "bin" / "python3"
    libraries.mkdir(parents=True)
    python.parent.mkdir(parents=True)
    (project / "src").mkdir()
    shutil.copy(ROOT / "scripts" / "migrate.sh", scripts / "migrate.sh")
    shutil.copy(ROOT / "scripts" / "lib" / "common.sh", libraries / "common.sh")
    shutil.copy(ROOT / "scripts" / "lib" / "preflight.sh", libraries / "preflight.sh")
    python.write_text(
        """#!/bin/sh
case "${1:-}" in
    -c)
        case "${2:-}" in
            *print*) printf '%s\\n' "3.12.0" ;;
        esac
        exit 0
        ;;
    -m)
        printf '%s' "${FAKE_MIGRATION_STDOUT:-}"
        printf '%s' "${FAKE_MIGRATION_STDERR:-}" >&2
        exit "${FAKE_MIGRATION_EXIT:-0}"
        ;;
esac
exit 1
""",
        encoding="utf-8",
    )
    python.chmod(0o755)
    return scripts / "migrate.sh"


def _run(script: Path, *args: str, **env_overrides: str):
    env = os.environ.copy()
    env.update(env_overrides)
    return subprocess.run(
        [str(script), *args],
        cwd=script.parents[1],
        text=True,
        capture_output=True,
        env=env,
    )


def test_machine_mode_preserves_exact_stdout_and_status_while_hiding_stderr(tmp_path):
    script = _sandboxed_migrate_script(tmp_path)
    report = (
        "general_config\tgeneral\tcurrent\tconfig/general.json\t\n"
        "target_config\tskroutz\tfailed\tconfig/skroutz.json\tinvalid legacy config\n"
    )

    result = _run(
        script,
        "--machine",
        "--check",
        FAKE_MIGRATION_STDOUT=report,
        FAKE_MIGRATION_STDERR="injected engine noise\n",
        FAKE_MIGRATION_EXIT="15",
    )

    assert result.returncode == 15
    assert result.stdout == report
    assert result.stderr == ""


def test_debug_machine_mode_mirrors_noise_without_corrupting_stdout(tmp_path):
    script = _sandboxed_migrate_script(tmp_path)
    report = "general_config\tgeneral\tcurrent\tconfig/general.json\t\n"

    result = _run(
        script,
        "--debug",
        "--machine",
        FAKE_MIGRATION_STDOUT=report,
        FAKE_MIGRATION_STDERR="injected engine noise\n",
        FAKE_MIGRATION_EXIT="19",
    )

    assert result.returncode == 19
    assert result.stdout == report
    assert result.stderr == "3.12.0\n" + report + "injected engine noise\n"


@pytest.mark.parametrize("status", [0, 1, 15, 16, 19])
def test_machine_mode_preserves_every_document_family_exit_code(tmp_path, status):
    script = _sandboxed_migrate_script(tmp_path)

    result = _run(
        script,
        "--machine",
        FAKE_MIGRATION_STDOUT="general_config\tgeneral\tcurrent\tconfig/general.json\t\n",
        FAKE_MIGRATION_EXIT=str(status),
    )

    assert result.returncode == status


def test_migrate_help_is_shell_rendered_and_needs_no_venv(tmp_path):
    script = _sandboxed_migrate_script(tmp_path)
    (script.parents[1] / "venv" / "bin" / "python3").unlink()

    result = _run(script, "--help")

    assert result.returncode == 0, result.stderr
    assert result.stdout == (
        "\n"
        "Usage: migrate.sh [-h] [--check] [--debug]\n"
        "\n"
        "Validate and migrate every known Scrooge Alert JSON document.\n"
        "With no flag, migrate outdated managed JSON documents in place.\n"
        "\n"
        "Optional arguments:\n"
        "  -h, --help        show this help message and exit\n"
        "  --check           Validate and report without modifying JSON files\n"
        "  --debug           show underlying command output\n"
        "\n"
    )
    assert result.stderr == ""


@pytest.mark.parametrize(
    "args",
    [
        ("--debug",),
        ("--check", "--debug"),
        ("--debug", "--check"),
        ("--machine", "--debug"),
        ("--debug", "--machine", "--check"),
        ("--check", "--debug", "--machine"),
    ],
)
def test_debug_is_accepted_with_every_compatible_flag_position(tmp_path, args):
    script = _sandboxed_migrate_script(tmp_path)
    report = "general_config\tgeneral\tcurrent\tconfig/general.json\t\n"

    result = _run(
        script,
        *args,
        FAKE_MIGRATION_STDOUT=report,
        FAKE_MIGRATION_STDERR="debug-noise\n",
    )

    assert result.returncode == 0
    assert "debug-noise\n" in result.stderr
    if "--machine" in args:
        assert result.stdout == report
    else:
        assert result.stdout.startswith("\n[+] General configuration\n")
        assert result.stdout.endswith("\n\n")


@pytest.mark.parametrize(
    "args",
    [
        ("--check", "--check"),
        ("--debug", "--debug"),
        ("--machine", "--machine"),
        ("--check", "--debug", "--check", "--debug"),
    ],
)
def test_duplicate_flags_remain_accepted(tmp_path, args):
    script = _sandboxed_migrate_script(tmp_path)
    report = "general_config\tgeneral\tcurrent\tconfig/general.json\t\n"

    result = _run(script, *args, FAKE_MIGRATION_STDOUT=report)

    assert result.returncode == 0


def test_help_keeps_precedence_over_debug_and_invalid_arguments(tmp_path):
    script = _sandboxed_migrate_script(tmp_path)

    result = _run(script, "--debug", "invalid", "--help", "--machine")

    assert result.returncode == 0
    assert result.stdout.startswith("\nUsage:")
    assert result.stderr == ""


@pytest.mark.parametrize("args", [("invalid",), ("--bogus",), ("--check", "extra")])
def test_invalid_human_arguments_keep_exit_two_and_outer_spacing(tmp_path, args):
    script = _sandboxed_migrate_script(tmp_path)

    result = _run(script, *args)

    assert result.returncode == 2
    assert result.stdout.startswith("\n[+] JSON migration\n")
    assert "    [x] Invalid argument:" in result.stdout
    assert result.stdout.endswith("\n\n")
    assert result.stderr == ""


@pytest.mark.parametrize(
    "args",
    [
        ("--machine", "--bogus"),
        ("--bogus", "--machine"),
        ("bad", "--debug", "--machine"),
    ],
)
def test_invalid_machine_argument_has_no_stdout_decoration(tmp_path, args):
    script = _sandboxed_migrate_script(tmp_path)

    result = _run(script, *args)

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith("Error: Invalid argument: ")


def test_missing_python_preflight_is_quiet_actionable_and_padded(tmp_path):
    script = _sandboxed_migrate_script(tmp_path)
    (script.parents[1] / "venv" / "bin" / "python3").unlink()

    result = _run(script)

    assert result.returncode == 1
    assert result.stdout == (
        "\n"
        "[+] Migration preflight\n"
        "    [x] Python 3.10 or newer is required.\n"
        "    [i] Run ./install.sh, then retry the migration.\n"
        "\n"
    )
    assert result.stderr == ""


def test_normal_human_mode_hides_engine_noise_and_renders_noop(tmp_path):
    script = _sandboxed_migrate_script(tmp_path)
    report = (
        "general_config\tgeneral\tcurrent\tconfig/general.json\t\n"
        "target_config\tskroutz\tmissing\tconfig/skroutz.json\t\n"
        "scraper_state\tskroutz\tcurrent\tstate/skroutz.json\t\n"
        "reminder_state\tgeneral\tmissing\tstate/general.json\t\n"
    )

    result = _run(
        script,
        FAKE_MIGRATION_STDOUT=report,
        FAKE_MIGRATION_STDERR="injected engine noise\n",
    )

    assert result.returncode == 0
    assert result.stdout == (
        "\n"
        "[+] General configuration\n"
        "    [v] config/general.json is current.\n"
        "\n"
        "[+] Target state\n"
        "    [v] state/skroutz.json is current.\n"
        "\n"
    )
    assert result.stderr == ""


def test_check_mode_renders_pending_migrations_as_information(tmp_path):
    script = _sandboxed_migrate_script(tmp_path)
    report = "general_config\tgeneral\tmigrated\tconfig/general.json\tpending v1 to v2\n"

    result = _run(
        script,
        "--debug",
        "--check",
        FAKE_MIGRATION_STDOUT=report,
        FAKE_MIGRATION_STDERR="check noise\n",
        FAKE_MIGRATION_EXIT="1",
    )

    assert result.returncode == 1
    assert result.stdout == (
        "\n[+] General configuration\n    [i] config/general.json requires migration: v1 to v2.\n\n"
    )
    assert result.stderr == "3.12.0\n" + report + "check noise\n"


def test_partial_failure_preserves_detail_recovery_and_family_exit(tmp_path):
    script = _sandboxed_migrate_script(tmp_path)
    report = (
        "general_config\tgeneral\tmigrated\tconfig/general.json\tv1 to v2\n"
        "target_config\tskroutz\tfailed\tconfig/skroutz.json\t"
        "invalid JSON. Original preserved; compare the example.\n"
        "scraper_state\tskroutz\tcurrent\tstate/skroutz.json\t\n"
        "recovery\tgeneral\tretained\t/project/state/.migration-recovery.example\t\n"
    )

    result = _run(
        script,
        FAKE_MIGRATION_STDOUT=report,
        FAKE_MIGRATION_EXIT="15",
    )

    assert result.returncode == 15
    assert "[v] config/general.json migrated: v1 to v2." in result.stdout
    assert (
        "    [x] config/skroutz.json: invalid JSON. Original preserved; compare the"
        in result.stdout
    )
    assert "        example." in result.stdout
    assert "[!] Recovery copies\n    [!] Retained at " in result.stdout
    assert result.stdout.startswith("\n")
    assert result.stdout.endswith("\n\n")


def test_total_failure_renders_every_family_and_preserves_precedence(tmp_path):
    script = _sandboxed_migrate_script(tmp_path)
    report = (
        "general_config\tgeneral\tfailed\tconfig/general.json\tgeneral failed\n"
        "target_config\tskroutz\tfailed\tconfig/skroutz.json\ttarget failed\n"
        "scraper_state\tskroutz\tfailed\tstate/skroutz.json\ttarget state failed\n"
        "reminder_state\tgeneral\tfailed\tstate/general.json\treminder state failed\n"
    )

    result = _run(
        script,
        FAKE_MIGRATION_STDOUT=report,
        FAKE_MIGRATION_EXIT="15",
    )

    assert result.returncode == 15
    assert result.stdout.count("[+]") == 4
    assert result.stdout.count("[x]") == 4
    assert result.stdout.startswith("\n")
    assert result.stdout.endswith("\n\n")
    assert "\n\n\n" not in result.stdout


def test_startup_failure_is_script_owned_and_debug_reveals_engine_diagnostic(tmp_path):
    script = _sandboxed_migrate_script(tmp_path)

    quiet = _run(
        script,
        FAKE_MIGRATION_STDERR="Migration could not start: catalog unavailable\n",
        FAKE_MIGRATION_EXIT="1",
    )
    debug = _run(
        script,
        "--debug",
        FAKE_MIGRATION_STDERR="Migration could not start: catalog unavailable\n",
        FAKE_MIGRATION_EXIT="1",
    )

    assert quiet.returncode == debug.returncode == 1
    assert quiet.stderr == ""
    assert "    [x] Migration could not start." in quiet.stdout
    assert "catalog unavailable" not in quiet.stdout
    assert "Migration could not start: catalog unavailable\n" in debug.stderr
