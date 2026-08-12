"""Suite-wide pytest fixtures.

``_isolate_process_environment`` gives tests and their direct subprocesses a stable
terminal, locale, user configuration, and temporary-file environment. Tests remain free
to override any value explicitly after the fixture has run.

``_isolate_logs_dir`` is the single, unconditional guarantee that no test - not even one
that reaches an error path - writes into the real repository ``logs/`` directory.

Logging reads its module-bound ``LOGS_DIR`` at call time, so redirecting it covers every
in-process log write. Locks are derived from the explicitly injected state roots owned by
their callers, so lock tests and application tests isolate them without a global patch.

Subprocess-based suites (the shell / UI harness) run the app inside their own copied
sandbox with their own ``BASE_DIR``, so they never touch the repo ``logs/`` and are
unaffected by this in-process redirect.
"""

import pytest


@pytest.fixture(autouse=True)
def _isolate_process_environment(monkeypatch, tmp_path):
    """Keep inherited developer and machine state out of test subprocesses."""
    home = tmp_path / "home"
    xdg_config_home = tmp_path / "xdg"
    temp_dir = tmp_path / "tmp"
    for directory in (home, xdg_config_home, temp_dir):
        directory.mkdir()

    stable_environment = {
        "COLUMNS": "100",
        "TERM": "xterm-256color",
        "NO_COLOR": "1",
        "LC_ALL": "C",
        "LANG": "C",
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg_config_home),
        "TMPDIR": str(temp_dir),
        "GIT_CONFIG_GLOBAL": "/dev/null",
        "GIT_CONFIG_NOSYSTEM": "1",
    }
    for name, value in stable_environment.items():
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("CLICOLOR_FORCE", raising=False)


@pytest.fixture(autouse=True)
def _isolate_logs_dir(monkeypatch, tmp_path):
    """Point runtime logs at a fresh per-test directory for every test."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    monkeypatch.setattr("core.infrastructure.logging.LOGS_DIR", str(logs_dir))
