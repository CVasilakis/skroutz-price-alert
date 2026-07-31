"""Suite-wide pytest fixtures.

``_isolate_process_environment`` gives tests and their direct subprocesses a stable
terminal, locale, user configuration, and temporary-file environment. Tests remain free
to override any value explicitly after the fixture has run.

``_isolate_runtime_paths`` is the single, unconditional guarantee that no test - not even
one that reaches an error path - writes into the real repository ``logs/`` or
``state/locks/`` directories.

Logging builds paths from its module-bound ``LOGS_DIR`` and locking uses its module-bound
``LOCKS_DIR``. Both read those values at call time, so redirecting them to per-test temp
directories covers every in-process write regardless of whether an individual test mocks
the logger or lock helper. The constants module deliberately retains the production paths.

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
def _isolate_runtime_paths(monkeypatch, tmp_path):
    """Point runtime logs and locks at fresh per-test directories for every test."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    monkeypatch.setattr("core.infrastructure.logging.LOGS_DIR", str(logs_dir))
    monkeypatch.setattr("core.infrastructure.locking.LOCKS_DIR", str(tmp_path / "state/locks"))
