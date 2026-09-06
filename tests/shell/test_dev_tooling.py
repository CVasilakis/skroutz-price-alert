import os
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import termios
import time
from pathlib import Path

import pytest

from core.scrapers.framework.catalog import PluginCatalog
from core.scrapers.tooling.scaffold.cli import _parser as scaffold_parser
from shell.assertions import assert_task_status, logical_task_lines

ROOT = Path(__file__).resolve().parents[2]

HELP_SCRIPTS = (
    "scrooge-alert",
    "scripts/install.sh",
    "scripts/update.sh",
    "scripts/run.sh",
    "scripts/ping.sh",
    "scripts/status.sh",
    "scripts/stop.sh",
    "scripts/disable.sh",
    "scripts/enable.sh",
    "scripts/schedule.sh",
    "scripts/uninstall.sh",
    "scripts/dev/migrate.sh",
    "scripts/dev/setup.sh",
    "scripts/dev/install-hooks.sh",
    "scripts/dev/check.sh",
    "scripts/dev/plugin-check.sh",
    "scripts/dev/plugin-create.sh",
)


def _run(script: str, *args: str, env: dict[str, str] | None = None):
    return subprocess.run(
        ["sh", str(ROOT / script), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=env or os.environ.copy(),
    )


def test_plugin_create_help_needs_no_venv_and_has_required_inputs():
    result = _run("scripts/dev/plugin-create.sh", "--help")
    assert result.returncode == 0, result.stderr
    assert "--display-name" in result.stdout
    assert "--domain" in result.stdout
    assert "--url-prefix" in result.stdout
    assert "--debug" in result.stdout


def test_plugin_create_shell_help_matches_public_backend_options():
    result = _run("scripts/dev/plugin-create.sh", "--help")
    documented = set(re.findall(r"--[a-z][a-z-]*", result.stdout))
    backend = {
        option
        for action in scaffold_parser()._actions
        if action.dest not in {"interactive", "shell_output"}
        for option in action.option_strings
        if option.startswith("--")
    }

    assert documented == backend | {"--debug"}


def test_plugin_create_interactive_output_is_owned_by_rich_panels():
    env = os.environ.copy()
    env["SCROOGE_PLUGIN_CREATE_PYTHON"] = sys.executable

    result = _run("scripts/dev/plugin-create.sh", env=env)

    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stderr == ""
    assert "[+] Plugin scaffold wizard" not in result.stdout
    assert result.stdout.startswith("\n╭")
    assert "Scrooge-Alert Plugin Wizard" in result.stdout
    assert "Interactive terminal required" in result.stdout
    assert "╯\n\n╭" in result.stdout
    assert result.stdout.endswith("\n\n")


def test_plugin_create_debug_alone_still_selects_strict_non_interactive_mode():
    """--debug is an argument, so it opts out of the wizard like any other.

    The wizard is reached only by a truly bare invocation. update.sh strips its
    --debug before applying the same no-argument rule; this script deliberately
    counts it first, and that ordering is what this pins.
    """
    env = os.environ.copy()
    env["SCROOGE_PLUGIN_CREATE_PYTHON"] = sys.executable

    result = _run("scripts/dev/plugin-create.sh", "--debug", env=env)

    assert result.returncode != 0
    assert "Scrooge-Alert Plugin Wizard" not in result.stdout
    assert "non-interactive mode requires target" in result.stdout + result.stderr
    assert_task_status(result.stdout, "x", "Target scaffold could not be created.")


def _interactive_plugin_create_process(*, terminal_type: str = "xterm-256color"):
    master, slave = pty.openpty()
    original = termios.tcgetattr(slave)
    env = os.environ.copy()
    env["SCROOGE_PLUGIN_CREATE_PYTHON"] = sys.executable
    env["NO_COLOR"] = "1"
    env["TERM"] = terminal_type
    process = subprocess.Popen(
        ["sh", str(ROOT / "scripts/dev/plugin-create.sh")],
        cwd=ROOT,
        stdin=slave,
        stdout=slave,
        stderr=slave,
        env=env,
        close_fds=True,
    )
    return process, master, slave, original


@pytest.mark.parametrize("terminal_type", ("dumb", "unknown"))
def test_plugin_create_rejects_unsupported_terminal_without_changing_settings(terminal_type):
    process, master, slave, original = _interactive_plugin_create_process(
        terminal_type=terminal_type
    )
    try:
        assert process.wait(timeout=10) == 1
        assert termios.tcgetattr(slave) == original
        output = _read_pty_available(master)
        assert b"Unsupported terminal" in output
        assert f"TERM={terminal_type}".encode() in output
        assert b"ANSI cursor support" in output
    finally:
        _stop_process(process)
        os.close(master)
        os.close(slave)


def _read_pty_until(master: int, needle: bytes, timeout: float = 10) -> bytes:
    output = bytearray()
    deadline = time.monotonic() + timeout
    while needle not in output and time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], 0.1)
        if not ready:
            continue
        try:
            output.extend(os.read(master, 4096))
        except OSError:
            break
    assert needle in output, bytes(output)
    return bytes(output)


def _read_pty_available(master: int, timeout: float = 0.5) -> bytes:
    output = bytearray()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([master], [], [], 0.05)
        if not ready:
            continue
        try:
            output.extend(os.read(master, 4096))
        except OSError:
            break
    return bytes(output)


def _stop_process(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is None:
        process.kill()
        process.wait(timeout=5)


@pytest.mark.parametrize(
    "signum",
    [
        getattr(signal, name)
        for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGQUIT")
        if hasattr(signal, name)
    ],
)
def test_plugin_create_catchable_signal_restores_exact_terminal_settings(signum):
    process, master, slave, original = _interactive_plugin_create_process()
    try:
        output = _read_pty_until(master, b"Target name")
        assert termios.tcgetattr(slave) != original

        process.send_signal(signum)

        assert process.wait(timeout=10) == 130
        assert termios.tcgetattr(slave) == original
        output += _read_pty_available(master)
        normalized = output.replace(b"\r\n", b"\n")
        assert b"Scaffold cancelled" in normalized
        assert b"No plugin was created" in normalized
        assert normalized.endswith(b"\n\n")
    finally:
        _stop_process(process)
        os.close(master)
        os.close(slave)


@pytest.mark.parametrize("cancel_key", [b"\x04", b"\x1b"], ids=("ctrl-d", "escape"))
def test_plugin_create_keyboard_cancel_restores_terminal_settings(cancel_key):
    process, master, slave, original = _interactive_plugin_create_process()
    try:
        output = _read_pty_until(master, b"Target name")
        os.write(master, cancel_key)

        assert process.wait(timeout=10) == 0
        assert termios.tcgetattr(slave) == original
        output += _read_pty_available(master)
        normalized = output.replace(b"\r\n", b"\n")
        assert b"Scaffold cancelled" in normalized
        assert b"No plugin was created" in normalized
        assert normalized.endswith(b"\n\n")
    finally:
        _stop_process(process)
        os.close(master)
        os.close(slave)


def test_plugin_create_incomplete_utf8_cannot_trap_the_wizard():
    process, master, slave, original = _interactive_plugin_create_process()
    try:
        _read_pty_until(master, b"Target name")
        os.write(master, b"\xce")
        _read_pty_until(master, b"Target name")
        os.write(master, b"\x1b")

        assert process.wait(timeout=10) == 0
        assert termios.tcgetattr(slave) == original
    finally:
        _stop_process(process)
        os.close(master)
        os.close(slave)


def test_plugin_create_suspend_and_resume_preserve_terminal_mode():
    process, master, slave, original = _interactive_plugin_create_process()
    try:
        _read_pty_until(master, b"Target name")
        process.send_signal(signal.SIGTSTP)

        deadline = time.monotonic() + 1
        stopped = False
        while time.monotonic() < deadline:
            waited, status = os.waitpid(process.pid, os.WNOHANG | os.WUNTRACED)
            if waited == process.pid and os.WIFSTOPPED(status):
                stopped = True
                break
            if waited == process.pid and not os.WIFSTOPPED(status):
                raise AssertionError(f"wizard exited during suspension: {status}")
            time.sleep(0.05)

        if stopped:
            assert termios.tcgetattr(slave) == original
        else:
            # POSIX permits an orphaned process group to ignore SIGTSTP. The
            # wizard must then keep running in cbreak mode, not canonical mode.
            current = termios.tcgetattr(slave)
            assert current != original
            assert current[3] & termios.ICANON == 0

        process.send_signal(signal.SIGCONT)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            resumed = termios.tcgetattr(slave)
            if resumed != original and resumed[3] & termios.ICANON == 0:
                break
            time.sleep(0.05)
        else:
            raise AssertionError("wizard did not restore cbreak mode after SIGCONT")

        # Cbreak mode is restored inside the SIGCONT handler, so observing it
        # does not prove that control has returned to the wizard's input loop.
        # Complete one input/redraw cycle before testing a second signal.
        os.write(master, b"qzv")
        _read_pty_until(master, b"qzv")

        process.send_signal(signal.SIGTERM)
        assert process.wait(timeout=10) == 130
        assert termios.tcgetattr(slave) == original
    finally:
        _stop_process(process)
        os.close(master)
        os.close(slave)


@pytest.fixture
def plugin_create_checkout(tmp_path: Path) -> Path:
    checkout = tmp_path / "checkout"
    shutil.copytree(ROOT / "src", checkout / "src")
    shutil.copytree(ROOT / "scripts/lib", checkout / "scripts/lib")
    (checkout / "scripts/dev").mkdir(parents=True)
    shutil.copy2(ROOT / "scripts/dev/plugin-create.sh", checkout / "scripts/dev/plugin-create.sh")
    (checkout / "tests/plugins").mkdir(parents=True)
    return checkout


def _run_plugin_create(
    checkout: Path, *args: str, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(checkout / "scripts/dev/plugin-create.sh"), *args],
        cwd=checkout,
        text=True,
        capture_output=True,
        env=env or os.environ.copy(),
    )


def _plugin_create_args(target: str = "acme_store") -> list[str]:
    return [
        target,
        "--display-name",
        "Acme Store With Spaces",
        "--domain",
        "store.example",
        "--url-prefix",
        "/products/",
        "--result-type",
        "price",
        "--default-interval",
        "1h",
        "--transport",
        "bare",
        "--with-tests",
    ]


def test_plugin_create_success_uses_sectioned_tui_and_preserves_spaced_values(
    plugin_create_checkout,
):
    result = _run_plugin_create(plugin_create_checkout, *_plugin_create_args())

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout == (
        "\n"
        "[+] Target scaffold\n"
        "    [i] Creating the target scaffold...\n"
        "    [v] [acme_store] Created the target source package.\n"
        "    [v] [acme_store] Created the target test package.\n"
        "\n"
        "[+] Next steps\n"
        "    [i] Run ./scripts/dev/setup.sh --acme_store.\n"
        "    [i] Run ./scripts/dev/plugin-check.sh --acme_store.\n"
        "    [i] Run ./scripts/dev/check.sh --debug before submitting.\n"
        "\n"
        "[+] Scaffold result\n"
        "    [v] [acme_store] Target scaffold created.\n"
        "\n"
    )
    plugin = plugin_create_checkout / "src/core/scrapers/plugins/acme_store/plugin.py"
    assert "Acme Store With Spaces" in plugin.read_text(encoding="utf-8")


@pytest.mark.parametrize("debug_index", [0, 1, 3, 5, 7, 9])
def test_plugin_create_accepts_debug_between_complete_arguments(
    plugin_create_checkout, debug_index
):
    args = _plugin_create_args(target=f"acme_{debug_index}")
    args.insert(debug_index, "--debug")

    result = _run_plugin_create(plugin_create_checkout, *args)

    assert result.returncode == 0, result.stdout + result.stderr
    assert_task_status(result.stdout, "v", f"[acme_{debug_index}] Target scaffold created.")
    assert f"scaffold\t1\tacme_{debug_index}" in result.stderr


def test_plugin_create_debug_alone_preserves_parser_status_and_exposes_diagnostics():
    result = _run("scripts/dev/plugin-create.sh", "--debug")

    assert result.returncode == 2
    assert "non-interactive mode requires" in result.stderr
    assert_task_status(result.stdout, "x", "Target scaffold could not be created.")
    assert result.stdout.startswith("\n")
    assert result.stdout.endswith("\n\n")


def test_plugin_create_normal_diagnostics_strip_terminal_control_characters():
    result = _run("scripts/dev/plugin-create.sh", "--unknown-\x1b[31m")

    assert result.returncode == 2
    assert "\x1b" not in result.stdout
    assert "\x1b" not in result.stderr
    assert_task_status(result.stdout, "x", "Target scaffold could not be created.")


@pytest.mark.parametrize(
    "args",
    (
        ("--help", "--debug"),
        ("bad", "--help"),
        ("--debug", "--help", "bad"),
        ("--help", "--help"),
    ),
)
def test_plugin_create_help_has_precedence_in_every_position(args):
    result = _run("scripts/dev/plugin-create.sh", *args)

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("\nUsage:")
    assert result.stdout.endswith("\n\n")


def test_plugin_create_accepts_duplicate_debug_without_forwarding_it(plugin_create_checkout):
    result = _run_plugin_create(
        plugin_create_checkout,
        "--debug",
        *_plugin_create_args(),
        "--debug",
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr.count("scaffold\t1\tacme_store\t1") == 1


def test_plugin_create_preserves_duplicate_option_and_invalid_argument_semantics(
    plugin_create_checkout,
):
    duplicate_option = _run_plugin_create(
        plugin_create_checkout,
        *_plugin_create_args(),
        "--display-name",
        "Last Store Name",
    )
    invalid = _run("scripts/dev/plugin-create.sh", "--unknown")
    duplicate_target = _run(
        "scripts/dev/plugin-create.sh",
        *_plugin_create_args(),
        "second_target",
    )

    assert duplicate_option.returncode == 0
    plugin = plugin_create_checkout / "src/core/scrapers/plugins/acme_store/plugin.py"
    assert "Last Store Name" in plugin.read_text(encoding="utf-8")
    assert invalid.returncode == 2
    assert duplicate_target.returncode == 2
    assert "unrecognized arguments: --unknown" in invalid.stdout
    assert "unrecognized arguments" not in invalid.stderr
    assert_task_status(invalid.stdout, "x", "Target scaffold could not be created.")
    assert_task_status(duplicate_target.stdout, "x", "Target scaffold could not be created.")


def _noisy_python(tmp_path: Path, scaffold_status: int | None = None) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(parents=True)
    fake_python = bin_dir / "python3"
    scaffold_failure = ""
    if scaffold_status is not None:
        scaffold_failure = (
            'case "$*" in\n'
            '  *"core.scrapers.tooling.scaffold"*)\n'
            '    printf "%s\\n" "injected scaffold noise" >&2\n'
            f"    exit {scaffold_status}\n"
            "    ;;\n"
            "esac\n"
        )
    fake_python.write_text(
        "#!/bin/sh\n"
        'printf "%s\\n" "injected python noise" >&2\n'
        f"{scaffold_failure}"
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return env


def _protocol_python(tmp_path: Path, output: str) -> dict[str, str]:
    bin_dir = tmp_path / "protocol-bin"
    bin_dir.mkdir(parents=True)
    fake_python = bin_dir / "python3"
    fake_python.write_text(
        "#!/bin/sh\n"
        'case "$*" in\n'
        '  *"core.scrapers.tooling.scaffold"*)\n'
        "    printf '%s' \"$SCAFFOLD_PROTOCOL_OUTPUT\"\n"
        "    exit 0\n"
        "    ;;\n"
        "esac\n"
        f'exec "{sys.executable}" "$@"\n',
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["SCAFFOLD_PROTOCOL_OUTPUT"] = output
    return env


@pytest.mark.parametrize(
    "output",
    (
        "scaffold\t1\tacme_store",
        "scaffold\t1\tacme_store\t2",
        "scaffold\t2\tacme_store\t1",
        "scaffold\t1\tbad-target\t1",
        "scaffold\t1\tacme_store\t1\textra",
        "noise\nscaffold\t1\tacme_store\t1",
    ),
)
def test_plugin_create_rejects_malformed_backend_protocol(tmp_path, output):
    result = _run(
        "scripts/dev/plugin-create.sh",
        *_plugin_create_args(),
        env=_protocol_python(tmp_path, output),
    )

    assert result.returncode == 1
    assert_task_status(result.stdout, "x", "Target scaffold returned an invalid result.")


def test_plugin_create_normal_hides_subprocess_noise(tmp_path, plugin_create_checkout):
    result = _run_plugin_create(
        plugin_create_checkout,
        *_plugin_create_args(),
        env=_noisy_python(tmp_path),
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "injected python noise" not in result.stdout
    assert "injected python noise" not in result.stderr


def test_plugin_create_normal_failure_hides_raw_noise_and_preserves_status(tmp_path):
    result = _run(
        "scripts/dev/plugin-create.sh",
        *_plugin_create_args(),
        env=_noisy_python(tmp_path, scaffold_status=23),
    )

    assert result.returncode == 23
    assert "injected python noise" not in result.stdout
    assert "injected python noise" not in result.stderr
    assert "injected scaffold noise" not in result.stdout
    assert "injected scaffold noise" not in result.stderr
    assert_task_status(result.stdout, "x", "Target scaffold could not be created.")
    assert_task_status(
        result.stdout,
        "i",
        "Run ./scripts/dev/plugin-create.sh --debug to inspect the failure.",
    )


def test_plugin_create_debug_exposes_noise_and_preserves_command_failure(tmp_path):
    result = _run(
        "scripts/dev/plugin-create.sh",
        "--debug",
        *_plugin_create_args(),
        env=_noisy_python(tmp_path, scaffold_status=23),
    )

    assert result.returncode == 23
    assert "injected python noise" in result.stderr
    assert "injected scaffold noise" in result.stderr
    assert_task_status(result.stdout, "x", "Target scaffold could not be created.")
    assert "injected scaffold noise" not in result.stdout
    assert_task_status(
        result.stdout,
        "i",
        "Review the underlying diagnostic above, then retry.",
    )


@pytest.mark.parametrize("script", HELP_SCRIPTS)
def test_shell_help_is_useful_and_framed_by_blank_lines(script):
    result = _run(script, "--help")

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    assert result.stdout.startswith("\nUsage:")
    assert result.stdout.endswith("\n\n")
    assert len([line for line in result.stdout.splitlines() if line]) >= 2


def test_plugin_check_help_needs_no_venv():
    result = _run("scripts/dev/plugin-check.sh", "--help")
    assert result.returncode == 0, result.stderr
    assert "Usage: ./scripts/dev/plugin-check.sh [-h] [--debug] --<target>" in result.stdout
    assert "target to verify (for example, --" in result.stdout
    assert "--debug" in result.stdout


def test_dev_setup_help_has_no_install_side_effect():
    result = _run("scripts/dev/setup.sh", "--help")
    assert result.returncode == 0, result.stderr
    assert "without systemd" in result.stdout
    expected_targets = {plugin.target for plugin in PluginCatalog.discover().plugins}
    listed_targets = {
        line.strip().split(maxsplit=1)[0].removeprefix("--")
        for line in result.stdout.splitlines()
        if line.startswith("  --") and not line.startswith("  --debug")
    }
    assert listed_targets == expected_targets


def test_check_help_needs_no_venv_and_describes_full_gate():
    result = _run("scripts/dev/check.sh", "--help")
    assert result.returncode == 0, result.stderr
    assert "With no check mode, run the complete local pre-push gate." in result.stdout
    assert "Usage: ./scripts/dev/check.sh [-h] [--debug] [full|static|shell|tests]" in (
        result.stdout
    )
    assert "--debug" in result.stdout


def _fake_check_tools(tmp_path: Path) -> dict[str, str]:
    fake_python = tmp_path / "python3"
    fake_python.write_text(
        """#!/bin/sh
set -eu
case "${1:-}" in
    -c)
        case "${2:-}" in
            *print*) printf '%s\\n' "3.12.0" ;;
        esac
        exit 0
        ;;
    -m)
        shift
        case "$*" in
            "ruff check "*) stage=lint ;;
            "ruff format --check "*) stage=format ;;
            "basedpyright src") stage=type ;;
            "pip check") stage=dependencies ;;
            "pytest") stage=tests ;;
            *) stage=unknown ;;
        esac
        if [ "$stage" = "tests" ] &&
           [ "${REQUIRE_NEUTRAL_TEST_DEBUG:-0}" = "1" ]; then
            [ "${DEBUG_MODE:-0}" = "0" ] || exit 91
            [ "${SCROOGE_INTERNAL_DEBUG:-0}" = "0" ] || exit 92
        fi
        printf '%s: %s\\n' "injected check stdout" "$stage"
        printf '%s: %s\\n' "injected check stderr" "$stage" >&2
        if [ "$stage" = "tests" ]; then
            printf '%s' "${TEST_PASSED:-813} passed"
            [ "${TEST_FAILED:-0}" -eq 0 ] ||
                printf ', %s failed' "$TEST_FAILED"
            [ "${TEST_WARNINGS:-0}" -eq 0 ] ||
                printf ', %s warnings' "$TEST_WARNINGS"
            [ "${TEST_ERRORS:-0}" -eq 0 ] ||
                printf ', %s errors' "$TEST_ERRORS"
            printf '%s\\n' " in 1.00s"
        fi
        if [ "${FAIL_STAGE:-}" = "$stage" ]; then
            exit "${FAIL_STATUS:-23}"
        fi
        ;;
esac
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    fake_shellcheck = tmp_path / "shellcheck"
    fake_shellcheck.write_text(
        """#!/bin/sh
printf '%s\\n' "injected shellcheck stdout"
printf '%s\\n' "injected shellcheck stderr" >&2
[ "${FAIL_STAGE:-}" != "shellcheck" ] || exit "${FAIL_STATUS:-23}"
""",
        encoding="utf-8",
    )
    fake_shellcheck.chmod(0o755)
    env = os.environ.copy()
    env["SCROOGE_CHECK_PYTHON"] = str(fake_python)
    env["SCROOGE_SHELLCHECK"] = str(fake_shellcheck)
    return env


def test_check_full_success_uses_required_sections_and_spacing(tmp_path):
    result = _run("scripts/dev/check.sh", env=_fake_check_tools(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout == (
        "\n"
        "[+] Static analysis\n"
        "    [i] Running Ruff lint...\n"
        "    [v] Ruff lint passed.\n"
        "    [i] Checking Ruff formatting...\n"
        "    [v] Ruff formatting passed.\n"
        "    [i] Running basedpyright...\n"
        "    [v] basedpyright passed.\n"
        "\n"
        "[+] Shell validation\n"
        "    [i] Running ShellCheck and POSIX syntax checks...\n"
        "    [v] ShellCheck passed.\n"
        "    [v] POSIX syntax checks passed.\n"
        "\n"
        "[+] Dependencies\n"
        "    [i] Checking installed dependencies...\n"
        "    [v] Installed dependencies are consistent.\n"
        "\n"
        "[+] Tests\n"
        "    [i] Running the full test suite...\n"
        "    [v] 813 tests passed.\n"
        "\n"
        "[+] Check result\n"
        "    [v] All requested checks passed.\n"
        "\n"
    )
    assert "injected" not in result.stdout


@pytest.mark.parametrize(
    "args",
    (
        ("--debug",),
        ("--debug", "full"),
        ("full", "--debug"),
        ("--debug", "static"),
        ("static", "--debug"),
        ("--debug", "shell"),
        ("shell", "--debug"),
        ("--debug", "tests"),
        ("tests", "--debug"),
    ),
)
def test_check_accepts_debug_alone_and_with_every_mode(tmp_path, args):
    result = _run("scripts/dev/check.sh", *args, env=_fake_check_tools(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert "injected check stdout" in combined or "injected shellcheck stdout" in combined
    assert "injected check stderr" in result.stderr or "injected shellcheck stderr" in (
        result.stderr
    )
    assert_task_status(result.stdout, "v", "All requested checks passed.")


def test_check_shell_debug_prints_enumerated_paths_one_per_line(tmp_path):
    result = _run("scripts/dev/check.sh", "shell", "--debug", env=_fake_check_tools(tmp_path))

    assert result.returncode == 0, result.stdout + result.stderr
    # git ls-files -z separates paths with NUL, so dumping it verbatim rendered
    # the whole enumeration as one unreadable line and, having no terminating
    # newline, absorbed the ShellCheck status that follows it.
    assert "\0" not in result.stderr
    dumped = result.stderr.splitlines()
    assert "scripts/dev/check.sh" in dumped
    assert "scripts/lib/common.sh" in dumped
    assert_task_status(result.stdout, "v", "ShellCheck passed.")
    assert_task_status(result.stdout, "v", "POSIX syntax checks passed.")


@pytest.mark.parametrize(
    "args",
    (
        ("--help", "--debug"),
        ("--debug", "--help"),
        ("unknown", "--help"),
        ("static", "tests", "--help"),
        ("--debug", "--debug", "--help"),
    ),
)
def test_check_help_has_precedence_in_every_position(args):
    result = _run("scripts/dev/check.sh", *args)

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("\nUsage:")
    assert result.stdout.endswith("\n\n")


@pytest.mark.parametrize(
    ("args", "message"),
    (
        (("unknown",), "Invalid argument: unknown"),
        (("static", "tests"), "Select at most one check mode."),
        (("static", "static"), "Select at most one check mode."),
        (("--debug", "--debug"), "Specify --debug at most once."),
        (("--",), "Invalid argument: --"),
    ),
)
def test_check_invalid_and_duplicate_flags_keep_usage_status(args, message):
    result = _run("scripts/dev/check.sh", *args)

    assert result.returncode == 2
    assert result.stderr == ""
    assert result.stdout.startswith("\n[+] Check arguments\n")
    assert message in result.stdout
    assert result.stdout.endswith("\n\n")


def test_check_normal_hides_tool_noise_and_preserves_failure_status(tmp_path):
    env = _fake_check_tools(tmp_path)
    env["FAIL_STAGE"] = "format"
    env["FAIL_STATUS"] = "23"

    result = _run("scripts/dev/check.sh", "static", env=env)

    assert result.returncode == 23
    assert "injected check" not in result.stdout
    assert "injected check" not in result.stderr
    assert_task_status(result.stdout, "v", "Ruff lint passed.")
    assert_task_status(result.stdout, "x", "Ruff formatting failed.")
    assert_task_status(result.stdout, "x", "Requested checks failed.")
    assert result.stdout.startswith("\n")
    assert result.stdout.endswith("\n\n")


def test_check_debug_exposes_same_noise_and_preserves_command_status(tmp_path):
    env = _fake_check_tools(tmp_path)
    env["FAIL_STAGE"] = "tests"
    env["FAIL_STATUS"] = "23"

    result = _run("scripts/dev/check.sh", "tests", "--debug", env=env)

    assert result.returncode == 23
    assert "injected check stdout: tests" in result.stderr
    assert "injected check stderr: tests" in result.stderr
    assert "injected check" not in result.stdout
    assert_task_status(result.stdout, "x", "Tests failed.")
    assert_task_status(result.stdout, "x", "Requested checks failed.")


def test_check_debug_does_not_force_pytest_children_into_debug_mode(tmp_path):
    env = _fake_check_tools(tmp_path)
    env["REQUIRE_NEUTRAL_TEST_DEBUG"] = "1"

    result = _run("scripts/dev/check.sh", "--debug", "tests", env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert "injected check stdout: tests" in result.stderr
    assert_task_status(result.stdout, "v", "813 tests passed.")


def test_check_reports_nonzero_pytest_warning_count(tmp_path):
    env = _fake_check_tools(tmp_path)
    env["TEST_WARNINGS"] = "2"

    result = _run("scripts/dev/check.sh", "tests", env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert_task_status(result.stdout, "v", "813 tests passed.")
    assert_task_status(result.stdout, "!", "2 test warnings.")


def test_check_reports_pytest_failure_and_error_counts(tmp_path):
    env = _fake_check_tools(tmp_path)
    env.update(
        {
            "FAIL_STAGE": "tests",
            "TEST_PASSED": "810",
            "TEST_FAILED": "2",
            "TEST_WARNINGS": "3",
            "TEST_ERRORS": "1",
        }
    )

    result = _run("scripts/dev/check.sh", "tests", env=env)

    assert result.returncode == 23
    assert_task_status(result.stdout, "v", "810 tests passed.")
    assert_task_status(result.stdout, "!", "3 test warnings.")
    assert_task_status(result.stdout, "x", "2 tests failed.")
    assert_task_status(result.stdout, "x", "1 test error.")
    assert "    [x] Tests failed.\n" not in result.stdout


def test_command_entrypoints_are_executable_and_libraries_are_not():
    commands = [
        ROOT / "scrooge-alert",
        ROOT / "scripts/install.sh",
        ROOT / "scripts/update.sh",
        *(ROOT / "scripts").glob("*.sh"),
        *(ROOT / "scripts/dev").glob("*.sh"),
        ROOT / ".githooks/pre-push",
    ]
    libraries = list((ROOT / "scripts/lib").glob("*.sh"))
    assert commands
    assert libraries
    assert all(os.access(path, os.X_OK) for path in commands)
    assert all(not os.access(path, os.X_OK) for path in libraries)


def test_update_help_runs_through_direct_executable_entrypoint():
    result = subprocess.run(
        [str(ROOT / "scripts/update.sh"), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
    assert "Usage: update.sh" in result.stdout


def test_shellcheck_ci_job_provisions_and_selects_supported_python():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    shellcheck_job = workflow.split("  shellcheck:\n", 1)[1].split("  typecheck:\n", 1)[0]
    assert "actions/setup-python@v6" in shellcheck_job
    assert "python-version: '3.10'" in shellcheck_job
    assert "pip install -r scripts/dev/requirements-shell.txt" in shellcheck_job
    assert "SCROOGE_CHECK_PYTHON=python" in shellcheck_job
    assert 'SCROOGE_SHELLCHECK="$(command -v shellcheck)"' in shellcheck_job
    check_invocations = [
        line.strip() for line in workflow.splitlines() if "./scripts/dev/check.sh" in line
    ]
    assert len(check_invocations) == 3
    assert all("./scripts/dev/check.sh --debug" in line for line in check_invocations)


def test_indirect_signal_handler_has_cross_version_shellcheck_suppression():
    update = (ROOT / "scripts/update.sh").read_text(encoding="utf-8")
    assert "# shellcheck disable=SC2317,SC2329" in update


def test_shell_gate_checks_tracked_and_new_nonignored_scripts(tmp_path):
    project = tmp_path / "project"
    dev_scripts = project / "scripts" / "dev"
    dev_scripts.mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "dev" / "check.sh", dev_scripts / "check.sh")
    lib = project / "scripts" / "lib"
    lib.mkdir()
    shutil.copy(ROOT / "scripts" / "lib" / "common.sh", lib / "common.sh")
    shutil.copy(ROOT / "scripts" / "lib" / "preflight.sh", lib / "preflight.sh")

    tracked = project / "tracked script.sh"
    new = project / "new script.sh"
    hook = project / ".githooks" / "pre-push"
    ignored = project / "ignored script.sh"
    deleted = project / "deleted script.sh"
    hook.parent.mkdir()
    for script in (tracked, new, hook, ignored, deleted):
        script.write_text("#!/bin/sh\n", encoding="utf-8")
    (project / ".gitignore").write_text("ignored script.sh\n", encoding="utf-8")

    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(
        [
            "git",
            "add",
            ".gitignore",
            "scripts/dev/check.sh",
            "scripts/lib/common.sh",
            "scripts/lib/preflight.sh",
            tracked.name,
            str(hook.relative_to(project)),
            deleted.name,
        ],
        cwd=project,
        check=True,
    )
    deleted.unlink()

    capture = tmp_path / "shellcheck-arguments.txt"
    fake_shellcheck = tmp_path / "shellcheck"
    fake_shellcheck.write_text(
        '#!/bin/sh\nset -eu\nprintf "%s\\n" "$@" >> "$SHELLCHECK_CAPTURE"\n',
        encoding="utf-8",
    )
    fake_shellcheck.chmod(0o755)

    env = os.environ.copy()
    env["SCROOGE_SHELLCHECK"] = str(fake_shellcheck)
    env["SCROOGE_CHECK_PYTHON"] = sys.executable
    env["SHELLCHECK_CAPTURE"] = str(capture)
    result = subprocess.run(
        ["sh", str(dev_scripts / "check.sh"), "shell"],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0, result.stderr
    arguments = capture.read_text(encoding="utf-8").splitlines()
    assert "-x" in arguments
    assert "--exclude=SC2086,SC2046" not in arguments
    assert "scripts/dev/check.sh" in arguments
    assert tracked.name in arguments
    assert new.name in arguments
    assert str(hook.relative_to(project)) in arguments
    assert ignored.name not in arguments
    assert deleted.name not in arguments


def _shell_gate_project(tmp_path, bad_name: str, bad_body: str) -> Path:
    """A minimal worktree whose shell gate has exactly one failing script."""
    project = tmp_path / "project"
    dev_scripts = project / "scripts" / "dev"
    dev_scripts.mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "dev" / "check.sh", dev_scripts / "check.sh")
    lib = project / "scripts" / "lib"
    lib.mkdir()
    shutil.copy(ROOT / "scripts" / "lib" / "common.sh", lib / "common.sh")
    shutil.copy(ROOT / "scripts" / "lib" / "preflight.sh", lib / "preflight.sh")

    # Sorts between "a_bad.sh" and "zz_bad.sh", so it is enumerated after one
    # and before the other. It must pass, since a failing file that follows the
    # real failure is what used to overwrite the recorded stage.
    (project / "m_good.sh").write_text('#!/bin/sh\nprintf "%s\\n" ok\n', encoding="utf-8")
    (project / bad_name).write_text(bad_body, encoding="utf-8")

    subprocess.run(["git", "init", "-q", str(project)], check=True)
    subprocess.run(["git", "add", "-A"], cwd=project, check=True)
    return project


def _run_shell_gate(project: Path, shellcheck: Path):
    env = os.environ.copy()
    env["SCROOGE_SHELLCHECK"] = str(shellcheck)
    env["SCROOGE_CHECK_PYTHON"] = sys.executable
    return subprocess.run(
        ["sh", str(project / "scripts" / "dev" / "check.sh"), "shell"],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )


# Fails ShellCheck (SC2050/SC3014) but parses cleanly, so it reaches the failure
# through ShellCheck alone rather than through the syntax pass behind it.
_LINT_ONLY_FAILURE = '#!/bin/sh\nif [ "a" == "a" ]; then\n  echo hi\nfi\n'


@pytest.mark.parametrize("bad_name", ("a_bad.sh", "zz_bad.sh"))
def test_check_shell_failure_report_does_not_depend_on_enumeration_order(tmp_path, bad_name):
    shellcheck = Path(os.environ.get("SCROOGE_SHELLCHECK") or ROOT / "venv/bin/shellcheck")
    if not shellcheck.is_file():
        resolved = shutil.which("shellcheck")
        if resolved is None:
            pytest.skip("ShellCheck is unavailable")
        shellcheck = Path(resolved)

    project = _shell_gate_project(tmp_path, bad_name, _LINT_ONLY_FAILURE)

    result = _run_shell_gate(project, shellcheck)

    # xargs keeps going after a failing child, so any per-file record of the
    # stage reached ends up describing the last file rather than the failing
    # one. That made an identical ShellCheck failure report itself as a POSIX
    # syntax failure whenever a passing file was enumerated after it.
    assert result.returncode != 0
    assert_task_status(result.stdout, "x", "ShellCheck or POSIX syntax checks failed.")
    assert_task_status(result.stdout, "x", "Requested checks failed.")
    # Neither single-tool verdict may be asserted, in either enumeration order.
    tasks = logical_task_lines(result.stdout)
    assert "[x] POSIX syntax checks failed." not in tasks
    assert "[x] ShellCheck failed." not in tasks


def test_check_shell_reports_failure_from_the_syntax_pass(tmp_path):
    # A passing ShellCheck isolates the failure to `sh -n`, which is the branch
    # the removed stage file happened to classify correctly.
    shellcheck = tmp_path / "shellcheck"
    shellcheck.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shellcheck.chmod(0o755)
    project = _shell_gate_project(
        tmp_path, "a_bad.sh", "#!/bin/sh\nif [ 1 = 1 ]; then\n  echo hi\n"
    )

    result = _run_shell_gate(project, shellcheck)

    assert result.returncode != 0
    assert_task_status(result.stdout, "x", "ShellCheck or POSIX syntax checks failed.")
    assert_task_status(result.stdout, "x", "Requested checks failed.")


def test_check_shell_gate_leaves_no_temporary_files_behind(tmp_path):
    shellcheck = tmp_path / "shellcheck"
    shellcheck.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shellcheck.chmod(0o755)
    project = _shell_gate_project(tmp_path, "a_bad.sh", '#!/bin/sh\nprintf "%s\\n" ok\n')
    scratch = tmp_path / "scratch"
    scratch.mkdir()

    env = os.environ.copy()
    env["SCROOGE_SHELLCHECK"] = str(shellcheck)
    env["SCROOGE_CHECK_PYTHON"] = sys.executable
    env["TMPDIR"] = str(scratch)
    result = subprocess.run(
        ["sh", str(project / "scripts" / "dev" / "check.sh"), "shell"],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert list(scratch.glob("scrooge-shell-*")) == []


def test_check_shell_gate_is_anchored_to_the_repository_root(tmp_path):
    # git ls-files lists only what is below the current directory, so a gate
    # enumerating from the caller's directory narrows itself silently instead of
    # failing: invoked from a subdirectory it used to check nothing above it and
    # still report a pass. The failing script sits at the repository root, above
    # the directory the gate is invoked from.
    shellcheck = tmp_path / "shellcheck"
    shellcheck.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    shellcheck.chmod(0o755)
    project = _shell_gate_project(
        tmp_path, "a_bad.sh", "#!/bin/sh\nif [ 1 = 1 ]; then\n  echo hi\n"
    )

    env = os.environ.copy()
    env["SCROOGE_SHELLCHECK"] = str(shellcheck)
    env["SCROOGE_CHECK_PYTHON"] = sys.executable
    result = subprocess.run(
        ["sh", str(project / "scripts" / "dev" / "check.sh"), "shell"],
        cwd=project / "scripts" / "dev",
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode != 0, result.stdout + result.stderr
    assert_task_status(result.stdout, "x", "ShellCheck or POSIX syntax checks failed.")


def test_dev_setup_contains_no_service_or_user_data_operations():
    contents = (ROOT / "scripts/dev/setup.sh").read_text(encoding="utf-8")
    assert "systemctl" not in contents
    assert "config/" not in contents
    assert "state/" not in contents
    assert '"$PROJECT_ROOT/scripts/dev/install-hooks.sh"' in contents


def test_dependency_installers_upgrade_all_requirement_sets_on_rerun():
    install = (ROOT / "scripts/install.sh").read_text(encoding="utf-8")
    setup = (ROOT / "scripts/dev/setup.sh").read_text(encoding="utf-8")

    assert 'pip_install -r "$REQUIREMENTS_FILE"' in install
    assert 'pip_install -r "$req_path"' in install
    assert '-m pip install -q --upgrade "$@"' in install
    assert '-m pip install --upgrade "$@"' in install
    assert '-m pip install --upgrade -r "$PROJECT_ROOT/requirements.txt"' in setup
    assert '-m pip install --upgrade -r "$requirement"' in setup


def test_hook_installer_sets_only_repository_local_hooks_path(tmp_path):
    project = tmp_path / "project"
    dev_scripts = project / "scripts" / "dev"
    dev_scripts.mkdir(parents=True)
    shutil.copy(
        ROOT / "scripts" / "dev" / "install-hooks.sh",
        dev_scripts / "install-hooks.sh",
    )
    lib = project / "scripts" / "lib"
    lib.mkdir()
    shutil.copy(ROOT / "scripts" / "lib" / "common.sh", lib / "common.sh")
    hooks = project / ".githooks"
    hooks.mkdir()
    shutil.copy(ROOT / ".githooks" / "pre-push", hooks / "pre-push")
    subprocess.run(["git", "init", "-q", str(project)], check=True)

    result = subprocess.run(
        ["sh", str(dev_scripts / "install-hooks.sh")],
        cwd=project,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stderr
    configured = subprocess.run(
        ["git", "-C", str(project), "config", "--local", "--get", "core.hooksPath"],
        check=True,
        text=True,
        capture_output=True,
    )
    assert configured.stdout.strip() == ".githooks"


def _hook_project(tmp_path: Path, hook_text: str | None):
    project = tmp_path / "project"
    dev_scripts = project / "scripts" / "dev"
    dev_scripts.mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "dev" / "install-hooks.sh", dev_scripts / "install-hooks.sh")
    lib = project / "scripts" / "lib"
    lib.mkdir()
    shutil.copy(ROOT / "scripts" / "lib" / "common.sh", lib / "common.sh")
    if hook_text is not None:
        hooks = project / ".githooks"
        hooks.mkdir()
        (hooks / "pre-push").write_text(hook_text, encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(project)], check=True)
    return project, dev_scripts / "install-hooks.sh"


def test_hook_installer_refuses_missing_hook_before_configuring(tmp_path):
    project, installer = _hook_project(tmp_path, None)
    result = subprocess.run(["sh", str(installer)], text=True, capture_output=True)
    assert result.returncode != 0
    assert_task_status(result.stdout, "x", "Cannot enable hooks; .githooks/pre-push is missing.")
    configured = subprocess.run(
        ["git", "-C", str(project), "config", "--local", "--get", "core.hooksPath"],
        text=True,
        capture_output=True,
    )
    assert configured.returncode != 0


def test_hook_installer_refuses_invalid_hook_before_configuring(tmp_path):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nif\n")
    result = subprocess.run(["sh", str(installer)], text=True, capture_output=True)
    assert result.returncode != 0
    assert_task_status(result.stdout, "x", ".githooks/pre-push has invalid POSIX shell syntax.")
    configured = subprocess.run(
        ["git", "-C", str(project), "config", "--local", "--get", "core.hooksPath"],
        text=True,
        capture_output=True,
    )
    assert configured.returncode != 0


def test_hook_installer_repairs_nonexecutable_hook(tmp_path):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nexit 0\n")
    hook = project / ".githooks" / "pre-push"
    hook.chmod(0o644)
    result = subprocess.run(["sh", str(installer)], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert os.access(hook, os.X_OK)


def test_hook_installer_refuses_symlink_without_chmodding_target(tmp_path):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nexit 0\n")
    hook = project / ".githooks" / "pre-push"
    external = tmp_path / "external-hook"
    external.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    external.chmod(0o644)
    hook.unlink()
    hook.symlink_to(external)

    result = subprocess.run(["sh", str(installer)], text=True, capture_output=True)

    assert result.returncode != 0
    assert external.stat().st_mode & 0o111 == 0


@pytest.mark.parametrize(
    "args",
    (
        ("--debug",),
        ("--debug", "--debug"),
    ),
)
def test_hook_installer_accepts_debug_and_duplicate_debug(tmp_path, args):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nexit 0\n")
    result = subprocess.run(
        ["sh", str(installer), *args],
        cwd=project,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert_task_status(result.stdout, "v", "Repository-local pre-push checks are enabled.")
    assert "true" in result.stderr
    assert ".githooks" in result.stderr


@pytest.mark.parametrize(
    "args",
    (
        ("--help", "--debug"),
        ("bad", "--help"),
        ("--debug", "--help", "bad"),
        ("--help", "--help"),
    ),
)
def test_hook_installer_help_has_precedence_in_every_position(tmp_path, args):
    project, installer = _hook_project(tmp_path, None)
    result = subprocess.run(
        ["sh", str(installer), *args],
        cwd=project,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("\nUsage:")
    assert result.stdout.endswith("\n\n")
    assert "--debug" in result.stdout


@pytest.mark.parametrize("argument", ("bad", "--", "--unknown"))
def test_hook_installer_rejects_invalid_arguments_with_framed_ui(tmp_path, argument):
    project, installer = _hook_project(tmp_path, None)
    result = subprocess.run(
        ["sh", str(installer), argument],
        cwd=project,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert result.stderr == ""
    assert result.stdout.startswith("\n[+]")
    assert result.stdout.endswith("\n\n")
    assert_task_status(result.stdout, "x", f"Invalid argument: {argument}")


def _noisy_git(tmp_path: Path):
    fake_bin = tmp_path / "noisy-bin"
    fake_bin.mkdir()
    git = fake_bin / "git"
    real_git = shutil.which("git")
    assert real_git is not None
    git.write_text(
        f"""#!/bin/sh
printf '%s\\n' 'injected git stderr' >&2
case " $* " in
    *" config "*)
        case " $* " in
            *" --get "*) ;;
            *)
                printf '%s\\n' 'injected git stdout'
                [ "${{NOISY_GIT_FAIL_CONFIG:-0}}" != "1" ] || exit 23 ;;
        esac ;;
esac
exec {real_git} "$@"
""",
        encoding="utf-8",
    )
    git.chmod(0o755)
    return fake_bin


def test_hook_installer_debug_exposes_noise_hidden_by_normal_mode(tmp_path):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nexit 0\n")
    fake_bin = _noisy_git(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"

    normal = subprocess.run(
        ["sh", str(installer)],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )
    debug = subprocess.run(
        ["sh", str(installer), "--debug"],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert normal.returncode == debug.returncode == 0
    assert "injected git" not in normal.stdout + normal.stderr
    assert "injected git stdout" in debug.stdout + debug.stderr
    assert "injected git stderr" in debug.stdout + debug.stderr


def test_hook_installer_debug_preserves_noisy_command_failure_status(tmp_path):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nexit 0\n")
    fake_bin = _noisy_git(tmp_path)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    env["NOISY_GIT_FAIL_CONFIG"] = "1"

    normal = subprocess.run(
        ["sh", str(installer)],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )
    debug = subprocess.run(
        ["sh", str(installer), "--debug"],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert normal.returncode == debug.returncode == 23
    assert "injected git" not in normal.stdout + normal.stderr
    assert "injected git stdout" in debug.stdout + debug.stderr
    assert "injected git stderr" in debug.stdout + debug.stderr
    assert_task_status(normal.stdout, "x", "Could not configure the repository-local hooks path.")
    assert_task_status(debug.stdout, "x", "Could not configure the repository-local hooks path.")


def test_hook_installer_no_worktree_is_a_framed_no_op(tmp_path):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nexit 0\n")
    shutil.rmtree(project / ".git")
    result = subprocess.run(
        ["sh", str(installer)],
        cwd=project,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert result.stdout.startswith("\n[+]")
    assert result.stdout.endswith("\n\n")
    assert_task_status(result.stdout, "i", "Git hooks were not configured (no Git worktree found).")


@pytest.mark.parametrize("columns", ("40", "80", "100", "160"))
def test_hook_task_semantics_are_width_independent_and_rendering_is_responsive(tmp_path, columns):
    project, installer = _hook_project(tmp_path, "#!/bin/sh\nexit 0\n")
    argument = "invalid-" + "-".join(("long",) * 18)
    env = os.environ.copy()
    env["COLUMNS"] = columns

    result = subprocess.run(
        ["sh", str(installer), argument],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    assert_task_status(result.stdout, "x", f"Invalid argument: {argument}")
    continuation_present = any(line.startswith("        ") for line in result.stdout.splitlines())
    assert continuation_present is (int(columns) < 160)


def test_versioned_pre_push_hook_runs_the_canonical_gate():
    hook = ROOT / ".githooks" / "pre-push"
    assert os.access(hook, os.X_OK)
    assert 'exec "$PROJECT_ROOT/scripts/dev/check.sh"' in hook.read_text(encoding="utf-8")


def test_plugin_check_binds_static_analysis_to_selected_venv():
    contents = (ROOT / "scripts/dev/plugin-check.sh").read_text(encoding="utf-8")
    assert 'plugin_check_venv_parent="$(dirname -- "$plugin_check_venv_dir")"' in contents
    assert "-m basedpyright" in contents
    assert '--venvpath "$plugin_check_venv_parent"' in contents
    check = (ROOT / "scripts/dev/check.sh").read_text(encoding="utf-8")
    assert "-m basedpyright src" in check
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'venvPath = "."' in pyproject
    assert 'venv = "venv"' in pyproject


def _fake_plugin_check_python(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    python = tmp_path / "isolation" / "venv" / "bin" / "python3"
    python.parent.mkdir(parents=True)
    python.write_text(
        """#!/bin/sh
set -eu
case "${1:-}" in
    -c)
        case "${2:-}" in
            *print*) printf '%s\\n' "3.12.0" ;;
        esac
        exit 0
        ;;
    -m)
        case "${2:-}" in
            core.scrapers.tooling.cli) stage=source ;;
            pytest) stage=tests ;;
            basedpyright) stage=type ;;
            ruff)
                case "${3:-}" in
                    check) stage=lint ;;
                    format) stage=format ;;
                esac
                ;;
        esac
        printf '%s: %s\\n' "injected verification stdout" "$stage"
        printf '%s: %s\\n' "injected verification stderr" "$stage" >&2
        if [ "$stage" = "source" ]; then
            printf '%s\\n' "ok\tcontributor files"
            printf '%s\\n' "tests\t${HAS_TESTS:-1}"
            if [ "${HAS_TESTS:-1}" = "0" ]; then
                printf '%s\\n' "warning\tplugin 'skroutz' has no target tests; behavior is unverified"
            fi
        fi
        if [ "$stage" = "source" ] && [ -n "${SOURCE_DIAGNOSTIC:-}" ]; then
            printf '%s\\n' "$SOURCE_DIAGNOSTIC" >&2
        fi
        if [ "${FAIL_STAGE:-}" = "$stage" ]; then
            exit "${FAIL_STATUS:-23}"
        fi
        ;;
esac
""",
        encoding="utf-8",
    )
    python.chmod(0o755)
    env = os.environ.copy()
    env["SCROOGE_PLUGIN_CHECK_PYTHON"] = str(python)
    return python, env


def test_plugin_check_success_uses_required_sections_and_spacing(tmp_path):
    _, env = _fake_plugin_check_python(tmp_path)

    result = _run("scripts/dev/plugin-check.sh", "--skroutz", env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stderr == ""
    assert result.stdout == (
        "\n"
        "[+] Target verification\n"
        "    [i] [skroutz] Checking the source and dependency contract...\n"
        "    [v] [skroutz] Source and dependency contract passed.\n"
        "\n"
        "[+] Target tests\n"
        "    [i] [skroutz] Running target tests...\n"
        "    [v] [skroutz] Tests passed.\n"
        "\n"
        "[+] Static analysis\n"
        "    [i] [skroutz] Running type checking...\n"
        "    [v] [skroutz] Type checking passed.\n"
        "    [i] [skroutz] Running Ruff lint...\n"
        "    [v] [skroutz] Ruff lint passed.\n"
        "    [i] [skroutz] Checking Ruff formatting...\n"
        "    [v] [skroutz] Ruff formatting passed.\n"
        "\n"
        "[+] Verification result\n"
        "    [v] [skroutz] Target verification complete.\n"
        "    [i] Run ./scripts/dev/check.sh --debug before submitting.\n"
        "\n"
    )
    assert "injected verification" not in result.stdout


def test_plugin_check_missing_tests_warns_and_continues_with_source_checks(tmp_path):
    _, env = _fake_plugin_check_python(tmp_path)
    env["HAS_TESTS"] = "0"

    result = _run("scripts/dev/plugin-check.sh", "--skroutz", env=env)

    assert result.returncode == 0, result.stdout + result.stderr
    assert_task_status(
        result.stdout,
        "!",
        "[skroutz] plugin 'skroutz' has no target tests; behavior is unverified",
    )
    assert_task_status(
        result.stdout,
        "!",
        "[skroutz] No target tests to run; continuing with source checks.",
    )
    assert "injected verification stdout: tests" not in result.stdout
    assert_task_status(result.stdout, "!", "[skroutz] Target verification complete with warnings.")
    assert "Type checking passed" in result.stdout


@pytest.mark.parametrize(
    "args",
    (
        ("--debug", "--skroutz"),
        ("--skroutz", "--debug"),
    ),
)
def test_plugin_check_accepts_debug_in_either_order(tmp_path, args):
    _, env = _fake_plugin_check_python(tmp_path)

    result = _run("scripts/dev/plugin-check.sh", *args, env=env)

    assert result.returncode == 0
    assert "injected verification stdout: source" in result.stderr
    assert "injected verification stderr: format" in result.stderr
    assert_task_status(result.stdout, "v", "[skroutz] Target verification complete.")


@pytest.mark.parametrize(
    "args",
    (
        ("--help", "--debug"),
        ("--debug", "--help"),
        ("--debug", "-h"),
        ("--help", "--help", "--debug"),
        ("--skroutz", "--help", "invalid"),
        ("invalid", "--help", "--debug"),
    ),
)
def test_plugin_check_help_has_precedence_in_every_position(args):
    result = _run("scripts/dev/plugin-check.sh", *args)

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("\nUsage:")
    assert result.stdout.endswith("\n\n")


@pytest.mark.parametrize(
    ("args", "message"),
    (
        ((), "Select exactly one target."),
        (("--debug",), "Select exactly one target."),
        (("skroutz",), "Invalid argument: skroutz"),
        (("--skroutz", "--insomnia"), "Select exactly one target."),
        (("--skroutz", "--skroutz"), "Select exactly one target."),
        (("--debug", "--debug", "--skroutz"), "Specify --debug at most once."),
        (("--bad-target",), "Invalid target 'bad-target'"),
    ),
)
def test_plugin_check_invalid_and_duplicate_flags_keep_usage_status(args, message):
    result = _run("scripts/dev/plugin-check.sh", *args)

    assert result.returncode == 2
    assert result.stderr == ""
    assert result.stdout.startswith("\n[+] Verification arguments\n")
    assert message in result.stdout
    assert result.stdout.endswith("\n\n")


def test_plugin_check_normal_hides_tool_noise_and_preserves_failure_status(tmp_path):
    _, env = _fake_plugin_check_python(tmp_path)
    env["FAIL_STAGE"] = "lint"
    env["FAIL_STATUS"] = "23"

    result = _run("scripts/dev/plugin-check.sh", "--skroutz", env=env)

    assert result.returncode == 23
    assert "injected verification" not in result.stdout
    assert "injected verification" not in result.stderr
    assert_task_status(result.stdout, "x", "[skroutz] Ruff lint failed.")
    assert_task_status(result.stdout, "x", "[skroutz] Target verification failed.")
    assert result.stdout.startswith("\n")
    assert result.stdout.endswith("\n\n")


def test_plugin_check_normal_shows_only_the_source_contract_diagnostic(tmp_path):
    _, env = _fake_plugin_check_python(tmp_path)
    env.update(
        {
            "FAIL_STAGE": "source",
            "FAIL_STATUS": "23",
            "SOURCE_DIAGNOSTIC": "Plugin check failed: invalid plugin domain",
        }
    )

    result = _run("scripts/dev/plugin-check.sh", "--skroutz", env=env)

    assert result.returncode == 23
    assert "injected verification" not in result.stdout
    assert "injected verification" not in result.stderr
    assert_task_status(result.stdout, "x", "[skroutz] Source and dependency contract failed.")
    assert_task_status(result.stdout, "i", "Plugin check failed: invalid plugin domain")
    assert "--debug" not in result.stdout


def test_plugin_check_normal_keeps_debug_guidance_without_a_known_diagnostic(tmp_path):
    _, env = _fake_plugin_check_python(tmp_path)
    env.update({"FAIL_STAGE": "source", "FAIL_STATUS": "23"})

    result = _run("scripts/dev/plugin-check.sh", "--skroutz", env=env)

    assert result.returncode == 23
    assert "injected verification" not in result.stdout
    assert "injected verification" not in result.stderr
    assert_task_status(
        result.stdout,
        "!",
        "Run ./scripts/dev/plugin-check.sh --debug --skroutz to inspect the failure.",
    )


def test_plugin_check_debug_exposes_the_source_contract_diagnostic_and_noise(tmp_path):
    _, env = _fake_plugin_check_python(tmp_path)
    env.update(
        {
            "FAIL_STAGE": "source",
            "FAIL_STATUS": "23",
            "SOURCE_DIAGNOSTIC": "Plugin check failed: invalid plugin domain",
        }
    )

    result = _run("scripts/dev/plugin-check.sh", "--debug", "--skroutz", env=env)

    assert result.returncode == 23
    assert "injected verification stdout: source" in result.stderr
    assert "injected verification stderr: source" in result.stderr
    assert "Plugin check failed: invalid plugin domain" in result.stderr
    assert result.stdout.count("Plugin check failed: invalid plugin domain") == 0
    assert_task_status(result.stdout, "!", "Review the underlying diagnostic above, then retry.")


def test_plugin_check_debug_exposes_same_tool_noise_and_preserves_failure_status(tmp_path):
    _, env = _fake_plugin_check_python(tmp_path)
    env["FAIL_STAGE"] = "tests"
    env["FAIL_STATUS"] = "23"

    result = _run("scripts/dev/plugin-check.sh", "--debug", "--skroutz", env=env)

    assert result.returncode == 23
    assert "injected verification stdout: tests" in result.stdout
    assert "injected verification stderr: tests" in result.stderr
    assert_task_status(result.stdout, "x", "[skroutz] Tests failed.")
    assert "Static analysis" not in result.stdout


def test_plugin_check_runs_from_external_venv_without_root_venv(tmp_path):
    project = tmp_path / "clean project"
    for relative in (
        "scripts/dev/plugin-check.sh",
        "scripts/lib/common.sh",
        "scripts/lib/preflight.sh",
        "pyproject.toml",
    ):
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, destination)
    shutil.copytree(ROOT / "src", project / "src")
    shutil.copytree(ROOT / "tests/plugins/insomnia", project / "tests/plugins/insomnia")
    shutil.copy(ROOT / "tests/support.py", project / "tests/support.py")

    external_venv = tmp_path / "isolation/venv"
    external_venv.parent.mkdir(parents=True)
    external_venv.symlink_to(sys.prefix, target_is_directory=True)
    external_python = external_venv / Path(sys.executable).relative_to(sys.prefix)
    env = os.environ.copy()
    env["SCROOGE_PLUGIN_CHECK_PYTHON"] = str(external_python)
    result = subprocess.run(
        ["sh", str(project / "scripts/dev/plugin-check.sh"), "--insomnia"],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not (project / "venv").exists()


def test_plugin_isolation_ci_invokes_plugin_check_in_debug_mode():
    workflow = (ROOT / ".github/workflows/tests.yml").read_text(encoding="utf-8")
    isolation = workflow.split("  plugin-isolation:\n", 1)[1]
    assert './scripts/dev/plugin-check.sh --debug "--$target"' in isolation


def test_dev_setup_rejects_invalid_argument_before_installing():
    result = _run("scripts/dev/setup.sh", "target")
    assert result.returncode == 1
    assert result.stderr == ""
    assert result.stdout.startswith("\n[+] Setup arguments\n")
    assert_task_status(result.stdout, "x", "Invalid argument: target")
    assert result.stdout.endswith("\n\n")


def test_dev_setup_rejects_multiple_targets_before_installing():
    result = _run("scripts/dev/setup.sh", "--skroutz", "--insomnia")
    assert result.returncode == 1
    assert result.stderr == ""
    assert_task_status(result.stdout, "x", "Select at most one target.")


def test_dev_setup_rejects_unknown_target_before_pip_work():
    result = _run("scripts/dev/setup.sh", "--does_not_exist")
    assert result.returncode == 1
    assert result.stderr == ""
    assert_task_status(result.stdout, "x", "Unknown target 'does_not_exist'.")
    assert "Requirement already satisfied" not in result.stdout


def test_dev_setup_rejects_project_venv_symlink_before_python_work(tmp_path):
    project = tmp_path / "project"
    for relative in (
        "scripts/dev/setup.sh",
        "scripts/lib/common.sh",
        "scripts/lib/preflight.sh",
    ):
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, destination)
    external = tmp_path / "external-venv"
    external.mkdir()
    (project / "venv").symlink_to(external, target_is_directory=True)

    result = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh")],
        text=True,
        capture_output=True,
    )

    assert result.returncode != 0
    assert result.stderr == ""
    assert_task_status(result.stdout, "x", "The development venv path is a symlink.")
    assert (project / "venv").is_symlink()


def _symlinked_venv_project(tmp_path, *relatives):
    """A project copy whose ./venv is a symlink, for the venv guard tests."""
    project = tmp_path / "project"
    for relative in relatives:
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, destination)
    external = tmp_path / "external-venv"
    (external / "bin").mkdir(parents=True)
    (project / "venv").symlink_to(external, target_is_directory=True)
    return project, external


@pytest.mark.parametrize(
    ("script", "args", "override"),
    (
        ("scripts/dev/check.sh", ("static",), "SCROOGE_CHECK_PYTHON"),
        ("scripts/dev/plugin-check.sh", ("--skroutz",), "SCROOGE_PLUGIN_CHECK_PYTHON"),
        ("scripts/dev/plugin-create.sh", (), "SCROOGE_PLUGIN_CREATE_PYTHON"),
    ),
)
def test_dev_checks_reject_project_venv_symlink_when_the_override_names_it(
    tmp_path, script, args, override
):
    """An override pointing back at the project venv must not skip the guard."""
    project, _ = _symlinked_venv_project(
        tmp_path, script, "scripts/lib/common.sh", "scripts/lib/preflight.sh"
    )
    env = os.environ.copy()
    env[override] = str(project / "venv/bin/python3")

    result = subprocess.run(
        ["sh", str(project / script), *args],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode != 0
    assert "is a symlink" in result.stdout + result.stderr
    assert (project / "venv").is_symlink()


@pytest.mark.parametrize(
    ("script", "args", "override"),
    (
        ("scripts/dev/check.sh", ("static",), "SCROOGE_CHECK_PYTHON"),
        ("scripts/dev/plugin-check.sh", ("--skroutz",), "SCROOGE_PLUGIN_CHECK_PYTHON"),
    ),
)
def test_dev_checks_allow_an_external_interpreter_past_the_venv_symlink_guard(
    tmp_path, script, args, override
):
    """CI and plugin isolation select an interpreter outside the project venv."""
    project, external = _symlinked_venv_project(
        tmp_path, script, "scripts/lib/common.sh", "scripts/lib/preflight.sh"
    )
    outside = tmp_path / "outside-venv/bin"
    outside.mkdir(parents=True)
    interpreter = outside / "python3"
    interpreter.write_text("#!/bin/sh\nexit 3\n", encoding="utf-8")
    interpreter.chmod(0o755)
    assert external.exists()
    env = os.environ.copy()
    env[override] = str(interpreter)

    result = subprocess.run(
        ["sh", str(project / script), *args],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    # The run still fails on the stub interpreter, but never on the venv guard.
    assert "is a symlink" not in result.stdout + result.stderr
    assert (project / "venv").is_symlink()


@pytest.mark.parametrize("override", (None, "external", "relative"))
def test_plugin_create_wizard_rejects_a_symlinked_venv_under_every_override(tmp_path, override):
    """The wizard guards unconditionally, so no override spelling reaches it."""
    project, _ = _symlinked_venv_project(
        tmp_path,
        "scripts/dev/plugin-create.sh",
        "scripts/lib/common.sh",
        "scripts/lib/preflight.sh",
    )
    env = os.environ.copy()
    if override == "external":
        env["SCROOGE_PLUGIN_CREATE_PYTHON"] = sys.executable
    elif override == "relative":
        # The lexical comparison in reject_project_venv_symlink_for would miss
        # this spelling; the wizard's unconditional guard must not.
        env["SCROOGE_PLUGIN_CREATE_PYTHON"] = "venv/bin/python3"

    result = subprocess.run(
        ["sh", str(project / "scripts/dev/plugin-create.sh")],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    assert_task_status(result.stdout, "x", "The development venv path is a symlink.")
    assert (project / "venv").is_symlink()


@pytest.fixture
def python39(tmp_path):
    fake = tmp_path / "python3"
    fake.write_text(
        """#!/bin/sh
case "${1:-}" in
    -c)
        case "${2:-}" in
            *print*) echo 3.9.18 ;;
            *) exit 1 ;;
        esac ;;
esac
""",
        encoding="utf-8",
    )
    fake.chmod(0o755)
    return fake


@pytest.mark.parametrize(
    ("script", "args", "override"),
    (
        ("scripts/dev/setup.sh", (), None),
        ("scripts/dev/check.sh", ("static",), "SCROOGE_CHECK_PYTHON"),
        ("scripts/dev/plugin-check.sh", ("--skroutz",), "SCROOGE_PLUGIN_CHECK_PYTHON"),
    ),
)
def test_development_wrappers_reject_python39(python39, script, args, override):
    env = os.environ.copy()
    env["PATH"] = f"{python39.parent}:{env['PATH']}"
    if override is not None:
        env[override] = str(python39)
    result = subprocess.run(
        ["sh", str(ROOT / script), *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode != 0
    output = result.stdout + result.stderr
    if script == "scripts/dev/setup.sh":
        assert "System Python 3.10 or newer is required." in output
    elif script == "scripts/dev/plugin-check.sh":
        assert_task_status(result.stdout, "x", "[skroutz] Python 3.10 or newer is unavailable.")
        assert result.stderr == ""
    else:
        assert_task_status(
            result.stdout,
            "x",
            "Python 3.10 or newer is unavailable. Run ./scripts/dev/setup.sh first.",
        )
        assert result.stderr == ""
    assert "3.10 or newer" in output


def test_dev_setup_installs_core_dev_and_all_plugin_requirements(tmp_path):
    project = tmp_path / "project"
    for relative in (
        "scripts/dev/setup.sh",
        "scripts/dev/install-hooks.sh",
        "scripts/lib/common.sh",
        "scripts/lib/preflight.sh",
        ".githooks/pre-push",
        "requirements.txt",
        "scripts/dev/requirements-dev.txt",
    ):
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, destination)
    subprocess.run(["git", "init", "-q", str(project)], check=True)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / "pip-calls"
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        """#!/bin/sh
set -eu
case "${1:-}" in
    -c)
        case "${2:-}" in *print*) echo 3.12.0 ;; esac
        exit 0 ;;
    -m)
        case "${2:-}" in
            core.scrapers.tooling.cli)
                printf 'alpha\\t%s\\nbeta\\t%s\\n' "$ALPHA_REQ" "$BETA_REQ" ;;
            venv)
                mkdir -p "$3/bin"
                cp "$0" "$3/bin/python3"
                chmod 755 "$3/bin/python3" ;;
            pip)
                printf '%s\\n' "$*" >> "$PIP_CAPTURE" ;;
        esac ;;
esac
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    alpha = tmp_path / "alpha requirements.txt"
    beta = tmp_path / "beta requirements.txt"
    alpha.touch()
    beta.touch()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "ALPHA_REQ": str(alpha),
            "BETA_REQ": str(beta),
            "PIP_CAPTURE": str(capture),
        }
    )
    result = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh")],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    calls = capture.read_text(encoding="utf-8")
    assert str(project / "requirements.txt") in calls
    assert str(project / "scripts/dev/requirements-dev.txt") in calls
    assert str(alpha) in calls
    assert str(beta) in calls
    assert "[+] Git hook setup" not in result.stdout
    assert_task_status(result.stdout, "v", "Repository-local pre-push checks are enabled.")


def test_dev_setup_rejects_existing_python39_venv_with_supported_system_python(tmp_path):
    project = tmp_path / "project"
    for relative in (
        "scripts/dev/setup.sh",
        "scripts/lib/common.sh",
        "scripts/lib/preflight.sh",
    ):
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, destination)

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    system_python = fake_bin / "python3"
    system_python.write_text(
        """#!/bin/sh
case "${1:-}" in
    -c)
        case "${2:-}" in *print*) echo 3.12.0 ;; esac
        exit 0 ;;
esac
exit 99
""",
        encoding="utf-8",
    )
    system_python.chmod(0o755)
    venv_python = project / "venv/bin/python3"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text(
        """#!/bin/sh
case "${1:-}" in
    -c)
        case "${2:-}" in
            *print*) echo 3.9.18; exit 0 ;;
            *) exit 1 ;;
        esac ;;
esac
""",
        encoding="utf-8",
    )
    venv_python.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}:{env['PATH']}"
    result = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh")],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )
    assert result.returncode != 0
    assert result.stderr == ""
    assert "existing development venv uses an unsupported Python" in result.stdout


def _setup_project(tmp_path: Path):
    project = tmp_path / "project"
    for relative in (
        "scripts/dev/setup.sh",
        "scripts/dev/install-hooks.sh",
        "scripts/lib/common.sh",
        "scripts/lib/preflight.sh",
        ".githooks/pre-push",
        "requirements.txt",
        "scripts/dev/requirements-dev.txt",
    ):
        destination = project / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / relative, destination)
    subprocess.run(["git", "init", "-q", str(project)], check=True)

    fake_bin = tmp_path / "setup-bin"
    fake_bin.mkdir()
    fake_python = fake_bin / "python3"
    fake_python.write_text(
        """#!/bin/sh
set -eu
if [ "${SETUP_NOISE:-0}" = "1" ]; then
    printf '%s\\n' 'injected python stdout'
    printf '%s\\n' 'injected python stderr' >&2
fi
case "${1:-}" in
    -c)
        case "${2:-}" in *print*) printf '%s\\n' '3.12.0' ;; esac
        exit 0 ;;
    -m)
        case "${2:-}" in
            core.scrapers.tooling.cli)
                printf 'alpha\\t%s\\nbeta\\t\\n' "$ALPHA_REQ"
                exit 0 ;;
            venv)
                mkdir -p "$3/bin"
                cp "$0" "$3/bin/python3"
                chmod 755 "$3/bin/python3"
                exit 0 ;;
            pip)
                stage=packaging
                case " $* " in
                    *" check "*) stage=check ;;
                    *" requirements.txt "*) stage=requirements ;;
                    *" $ALPHA_REQ "*) stage=target ;;
                esac
                [ "${SETUP_FAIL_STAGE:-}" != "$stage" ] || exit 23
                exit 0 ;;
        esac ;;
esac
exit 0
""",
        encoding="utf-8",
    )
    fake_python.chmod(0o755)
    alpha = tmp_path / "alpha-private.req"
    alpha.touch()
    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "ALPHA_REQ": str(alpha),
        }
    )
    return project, env


@pytest.mark.parametrize(
    "args",
    (
        ("--debug",),
        ("--debug", "--debug"),
        ("--alpha", "--debug"),
        ("--debug", "--alpha"),
    ),
)
def test_dev_setup_accepts_debug_in_supported_positions(tmp_path, args):
    project, env = _setup_project(tmp_path)
    result = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh"), *args],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.startswith("\n[+] Environment checks\n")
    assert result.stdout.endswith("\n\n")
    assert "[+] Setup complete" in result.stdout
    if "--debug" in args:
        assert "alpha\t" not in result.stdout
        assert "alpha\t" in result.stderr


@pytest.mark.parametrize(
    "args",
    (
        ("--help", "--debug"),
        ("bad", "--help"),
        ("--debug", "--help", "bad"),
        ("--help", "--help"),
    ),
)
def test_dev_setup_help_has_precedence_in_every_position(tmp_path, args):
    project, env = _setup_project(tmp_path)
    result = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh"), *args],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.startswith("\nUsage:")
    assert result.stdout.endswith("\n\n")
    assert "--debug" in result.stdout


@pytest.mark.parametrize(
    "args",
    (
        ("--",),
        ("bad",),
        ("--alpha", "--alpha"),
        ("--debug", "--alpha", "--beta"),
    ),
)
def test_dev_setup_invalid_and_duplicate_flags_keep_exit_one(tmp_path, args):
    project, env = _setup_project(tmp_path)
    result = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh"), *args],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert result.returncode == 1
    assert result.stderr == ""
    assert result.stdout.startswith("\n[+] Setup arguments\n")
    assert result.stdout.endswith("\n\n")


def test_dev_setup_normal_hides_noise_and_debug_exposes_it(tmp_path):
    project, env = _setup_project(tmp_path)
    env["SETUP_NOISE"] = "1"

    normal = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh")],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )
    debug = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh"), "--debug"],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert normal.returncode == debug.returncode == 0
    assert "injected python" not in normal.stdout + normal.stderr
    assert "injected python stdout" in debug.stdout + debug.stderr
    assert "injected python stderr" in debug.stdout + debug.stderr


def test_dev_setup_debug_preserves_noisy_command_failure_status(tmp_path):
    project, env = _setup_project(tmp_path)
    env.update({"SETUP_NOISE": "1", "SETUP_FAIL_STAGE": "target"})

    normal = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh")],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )
    debug = subprocess.run(
        ["sh", str(project / "scripts/dev/setup.sh"), "--debug"],
        cwd=project,
        text=True,
        capture_output=True,
        env=env,
    )

    assert normal.returncode == debug.returncode == 23
    assert "injected python" not in normal.stdout + normal.stderr
    assert "injected python stdout" in debug.stdout + debug.stderr
    assert "injected python stderr" in debug.stdout + debug.stderr
    message = "    [x] Private dependencies for the alpha target could not be installed."
    assert message in normal.stdout
    assert message in debug.stdout
    assert normal.stdout.startswith("\n[+] Environment checks\n")
    assert normal.stdout.endswith("\n\n")


def test_developer_requirements_are_visible_without_unignoring_arbitrary_text():
    requirement_results = [
        subprocess.run(
            ["git", "check-ignore", "--quiet", "--no-index", relative],
            cwd=ROOT,
        )
        for relative in (
            "scripts/dev/requirements-dev.txt",
            "scripts/dev/requirements-shell.txt",
        )
    ]
    arbitrary_text = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", "scripts/dev/notes.txt"],
        cwd=ROOT,
    )

    assert all(result.returncode == 1 for result in requirement_results)
    assert arbitrary_text.returncode == 0
