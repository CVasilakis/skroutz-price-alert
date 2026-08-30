"""Behavioral tests for the shared target-flag plumbing in scripts/lib/runtime.sh.

``run.sh`` and ``status.sh`` both validate ``--<target>`` flags and forward the accepted
ones into the venv Python. That plumbing lives in the shared library so the two wrappers
cannot drift apart, and these tests pin it directly: the queue's ordering (POSIX sh has
no arrays, so it is re-expanded from numbered variables) and the accept/reject decision.
The rendered failure transcripts themselves are covered by the shell snapshot surfaces.
"""

import os
import subprocess
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMON_SH = REPO_ROOT / "scripts" / "lib" / "common.sh"
RUNTIME_SH = REPO_ROOT / "scripts" / "lib" / "runtime.sh"

# The exec is the one side effect worth observing; replacing it keeps the tests in-process.
# The stub exits rather than returns, because the real exec never returns either - the
# queue re-expansion relies on that to stop recursing.
STUB_EXEC = 'exec_runtime_entrypoint() { printf "%s\\n" "$@"; exit 0; }'


def run_sh(script: str):
    """Runs `script` under `sh -eu` with common.sh and runtime.sh sourced."""
    full = f'BASE_DIR="{REPO_ROOT}"\n. "{COMMON_SH}"\n. "{RUNTIME_SH}"\n{STUB_EXEC}\n{script}'
    return subprocess.run(
        ["sh", "-eu", "-c", full],
        capture_output=True,
        text=True,
        env=os.environ.copy(),
        timeout=30,
    )


class TestArgumentForwarding(unittest.TestCase):
    def test_queued_arguments_reach_the_entry_point_in_order(self):
        result = run_sh(
            "runtime_forward_arg --quiet\n"
            "runtime_forward_arg --skroutz\n"
            "runtime_forward_arg --amazon\n"
            "runtime_exec_forwarded run.py"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["run.py", "--quiet", "--skroutz", "--amazon"])

    def test_an_empty_queue_dispatches_the_bare_entry_point(self):
        result = run_sh("runtime_exec_forwarded status.py")

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["status.py"])


class TestTargetFlagValidation(unittest.TestCase):
    def test_a_known_target_is_queued_for_forwarding(self):
        result = run_sh(
            'runtime_target_flag status --ghost "skroutz\nghost" 1\n'
            "runtime_exec_forwarded status.py"
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.split(), ["status.py", "--ghost"])

    def test_an_unknown_target_is_rejected_with_the_command_specific_hint(self):
        result = run_sh('runtime_target_flag status --ghost "skroutz" 1')

        self.assertEqual(result.returncode, 1)
        self.assertIn("Unknown target flag: --ghost.", result.stdout)
        self.assertIn("./scrooge-alert status --help", result.stdout)

    def test_a_non_snake_case_flag_is_rejected_before_the_catalog_is_consulted(self):
        result = run_sh(
            'catalog_cli() { printf "unexpected\\n"; }\nruntime_target_flag run --Skroutz "" 0'
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn(
            "Invalid target flag: --Skroutz (expected --<snake_case target>).", result.stdout
        )
        self.assertNotIn("unexpected", result.stdout)

    def test_an_unloadable_catalog_is_diagnosed_instead_of_blaming_the_flag(self):
        result = run_sh(
            'catalog_cli() { printf "  PluginDiscoveryError: boom\\n"; return 1; }\n'
            'runtime_target_flag run --skroutz "" 0'
        )

        self.assertEqual(result.returncode, 1)
        self.assertIn("The target catalog could not be loaded.", result.stdout)
        self.assertIn("PluginDiscoveryError: boom", result.stdout)
        self.assertNotIn("Unknown target flag", result.stdout)
