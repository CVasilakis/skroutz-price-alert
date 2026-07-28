"""Suite-wide pytest fixtures.

``_isolate_process_environment`` gives tests and their direct subprocesses a stable
terminal, locale, user configuration, and temporary-file environment. Tests remain free
to override any value explicitly after the fixture has run.

``_isolate_logs_dir`` is the single, unconditional guarantee that no test - not even one
that reaches an error path - writes into the real repository ``logs/`` directory.

Every in-process write the app makes to ``logs/`` flows through two modules:
``core.infrastructure.logging`` (``setup_global_logging`` / ``get_target_logger`` / ``save_traceback``)
and ``core.infrastructure.locking`` (``acquire_lock``). Both build their paths from the ``LOGS_DIR`` name
bound in their own namespace by ``from core.constants import LOGS_DIR``, and both read it
at call time, so redirecting those two names to a per-test temp dir covers all of them -
regardless of whether an individual test remembers to mock the logger or the traceback
helper. ``core.constants.LOGS_DIR`` is deliberately left pointing at the real path so a
regression test can assert the redirect is actually in effect.

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
def _isolate_logs_dir(monkeypatch, tmp_path):
    """Points the app's ``LOGS_DIR`` at a fresh per-test temp dir, for every test."""
    logs_dir = tmp_path / "logs"
    logs_dir.mkdir()
    monkeypatch.setattr("core.infrastructure.logging.LOGS_DIR", str(logs_dir))
    monkeypatch.setattr("core.infrastructure.locking.LOGS_DIR", str(logs_dir))
