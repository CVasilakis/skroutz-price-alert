"""Local shellcheck gate: every tracked .sh script must lint clean in POSIX-sh mode.

Mirrors the CI shellcheck job (same flags: ``-x`` to follow the ``shellcheck source=``
directives into ``scripts/lib/common.sh``; SC2086/SC2046 excluded as the library's
documented word-splitting idiom), so a bashism or quoting bug fails ``pytest`` locally
instead of first surfacing on push. Every script's shebang is ``#!/bin/sh``, so
shellcheck lints in POSIX mode and its SC3xxx checks are what enforce "verified
against dash".

The script list comes from ``git ls-files '*.sh'`` — drift-proof: a new shell script
anywhere in the repo is covered the moment it is tracked, with no list to maintain
(and the venv is never scanned, since it is untracked).

Skips cleanly when shellcheck is not installed (it ships in ``requirements-dev.txt``
via the ``shellcheck-py`` wheel, so a dev-toolchain install has it; the venv's own
``bin`` is probed too, so no shell activation is needed).
"""

import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SHELLCHECK_ARGS = ["-x", "--exclude=SC2086,SC2046"]


def find_shellcheck() -> str | None:
    """Returns the shellcheck executable: PATH first, then the running venv's bin."""
    found = shutil.which("shellcheck")
    if found:
        return found
    venv_shellcheck = Path(sys.executable).with_name("shellcheck")
    return str(venv_shellcheck) if venv_shellcheck.is_file() else None


def tracked_shell_scripts() -> list[str]:
    """Every git-tracked ``*.sh`` path, relative to the repo root."""
    out = subprocess.run(
        ["git", "ls-files", "--", "*.sh"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


class TestShellcheck(unittest.TestCase):
    def test_all_tracked_scripts_lint_clean(self):
        shellcheck = find_shellcheck()
        if shellcheck is None:  # pragma: no cover - toolchain not installed
            self.skipTest("shellcheck not installed (pip install -r requirements-dev.txt)")

        scripts = tracked_shell_scripts()
        # Silent-green guard: the repo ships 9 scripts today; an empty or shrunken
        # enumeration means the listing broke, not that the scripts vanished.
        self.assertGreaterEqual(len(scripts), 9, scripts)

        # cwd=REPO_ROOT so the relative "shellcheck source=scripts/lib/common.sh"
        # directives resolve exactly as in the CI job.
        result = subprocess.run(
            [shellcheck, *SHELLCHECK_ARGS, *scripts],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        self.assertEqual(
            result.returncode, 0,
            msg=f"shellcheck found issues:\n{result.stdout}{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
