"""Contracts for the suite-wide direct-subprocess environment."""

import os
import subprocess


def test_direct_subprocess_environment_is_machine_independent(tmp_path):
    env = os.environ.copy()

    assert env["COLUMNS"] == "100"
    assert env["NO_COLOR"] == "1"
    assert env["LC_ALL"] == env["LANG"] == "C"
    assert "CLICOLOR_FORCE" not in env
    assert env["GIT_CONFIG_GLOBAL"] == "/dev/null"
    assert env["GIT_CONFIG_NOSYSTEM"] == "1"
    assert env["HOME"] != os.path.expanduser("~") or env["HOME"].startswith(str(tmp_path))
    assert env["XDG_CONFIG_HOME"].startswith(str(tmp_path))
    assert env["TMPDIR"].startswith(str(tmp_path))

    child = subprocess.run(
        ["sh", "-c", 'printf "%s\\n" "$COLUMNS|$LC_ALL|$HOME|$XDG_CONFIG_HOME|$TMPDIR"'],
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert child.stdout.startswith("100|C|")


def test_tests_can_explicitly_override_stable_environment(monkeypatch):
    monkeypatch.setenv("COLUMNS", "37")
    monkeypatch.setenv("CLICOLOR_FORCE", "1")

    result = subprocess.run(
        ["sh", "-c", 'printf "%s:%s" "$COLUMNS" "$CLICOLOR_FORCE"'],
        env=os.environ.copy(),
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout == "37:1"
