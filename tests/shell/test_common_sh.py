"""Behavioral tests for the POSIX helpers in scripts/lib/common.sh.

The shell layer carries real logic now — the tab-stream parsing behind the
systemd unit generation, the unit-file round-trip, the teardown target union —
so it gets the same regression safety as the Python code. Each test runs a
small `sh -eu` script in a subprocess that sources the library (exactly as the
management scripts do), optionally redefines the enumeration helpers as stubs
(plain POSIX function redefinition), and asserts on output/exit code. No
systemd and no network are involved; `SYSTEMD_USER_DIR` is redirected into a
tmp directory via ``XDG_CONFIG_HOME``.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COMMON_SH = REPO_ROOT / "scripts" / "lib" / "common.sh"


def run_sh(script: str, base_dir=REPO_ROOT, xdg_config_home=None):
    """Runs `script` under `sh -eu` with common.sh sourced (the caller contract).

    Args:
        script: The shell snippet to run after the library is sourced.
        base_dir: The BASE_DIR to expose (the sourcing contract requires it).
        xdg_config_home: When set, exported so SYSTEMD_USER_DIR lands in a tmp dir.

    Returns:
        subprocess.CompletedProcess: With captured text stdout/stderr.
    """
    env = os.environ.copy()
    if xdg_config_home is not None:
        env["XDG_CONFIG_HOME"] = str(xdg_config_home)
    full = f'BASE_DIR="{base_dir}"\n. "{COMMON_SH}"\n{script}'
    return subprocess.run(
        ["sh", "-eu", "-c", full], capture_output=True, text=True, env=env,
    )


class TestNamingHelpers(unittest.TestCase):
    def test_unit_name(self):
        result = run_sh('unit_name skroutz timer')
        self.assertEqual(result.stdout, "skroutz-scraper.timer")

    def test_plugin_in_list_hit_and_miss(self):
        self.assertEqual(run_sh('plugin_in_list b a b c').returncode, 0)
        self.assertEqual(run_sh('plugin_in_list z a b c').returncode, 1)

    def test_plugin_in_list_with_empty_list(self):
        self.assertEqual(run_sh('plugin_in_list z').returncode, 1)


class TestPluginTimerBlock(unittest.TestCase):
    """The tab-separated "<plugin>\\t<Key>=<Value>" stream -> per-plugin [Timer] block."""

    STREAM = (
        r'skroutz\tOnCalendar=hourly'
        r'\nskroutz\tRandomizedDelaySec=99'
        r'\nskroutz\tPersistent=false'
        r'\namazon\tOnCalendar=*-*-* 00/2:00:00'
        r'\namazon\tAccuracySec=1m'
    )

    def _block_for(self, plugin):
        return run_sh(
            f'all="$(printf \'{self.STREAM}\')"\n'
            f'plugin_timer_block {plugin} "$all"'
        ).stdout

    def test_selects_only_the_named_plugins_directives(self):
        self.assertEqual(self._block_for("skroutz"), "OnCalendar=hourly")

    def test_framework_managed_keys_are_dropped(self):
        block = self._block_for("skroutz")
        self.assertNotIn("RandomizedDelaySec", block)
        self.assertNotIn("Persistent", block)

    def test_preserves_values_with_spaces_and_multiple_directives(self):
        self.assertEqual(
            self._block_for("amazon"),
            "OnCalendar=*-*-* 00/2:00:00\nAccuracySec=1m",
        )

    def test_unknown_plugin_yields_empty_block(self):
        self.assertEqual(self._block_for("ghost"), "")


class TestUnitFileRoundTrip(unittest.TestCase):
    """write_plugin_units renders both units; read_timer_block recovers the trigger."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.unit_dir = self.tmp / "systemd" / "user"

    def _write(self, plugin, block):
        return run_sh(
            f'mkdir -p "$SYSTEMD_USER_DIR"\n'
            f'write_plugin_units {plugin} "{block}"',
            xdg_config_home=self.tmp,
        )

    def test_round_trip_recovers_the_trigger_block(self):
        block = "OnCalendar=*-*-* 00/2:00:00"
        self.assertEqual(self._write("foo", block).returncode, 0)
        read = run_sh('read_timer_block foo', xdg_config_home=self.tmp)
        self.assertEqual(read.stdout, block)

    def test_framework_keys_are_appended_but_not_read_back(self):
        self._write("foo", "OnCalendar=hourly")
        timer_text = (self.unit_dir / "foo-scraper.timer").read_text()
        self.assertIn("RandomizedDelaySec=180s", timer_text)
        self.assertIn("Persistent=true", timer_text)
        # read_timer_block normalizes them away, mirroring plugin_timer_block.
        read = run_sh('read_timer_block foo', xdg_config_home=self.tmp)
        self.assertEqual(read.stdout, "OnCalendar=hourly")

    def test_service_dispatches_through_run_sh(self):
        self._write("foo", "OnCalendar=hourly")
        service_text = (self.unit_dir / "foo-scraper.service").read_text()
        self.assertIn(f'ExecStart="{REPO_ROOT}/scripts/run.sh" --quiet --foo', service_text)

    def test_read_timer_block_missing_unit_is_empty_success(self):
        read = run_sh('read_timer_block ghost', xdg_config_home=self.tmp)
        self.assertEqual((read.returncode, read.stdout), (0, ""))


class TestKnownTargets(unittest.TestCase):
    """The teardown validation set: registered ∪ installed, de-duplicated."""

    STUBS = (
        'list_plugins() { printf \'%s\\n\' skroutz amazon; }\n'
        'list_installed_plugins() { printf \'%s\\n\' amazon ghost; }\n'
    )

    def test_union_preserves_first_seen_order_and_dedups(self):
        result = run_sh(self.STUBS + 'known_targets timer')
        self.assertEqual(result.stdout.split(), ["skroutz", "amazon", "ghost"])

    def test_orphan_unit_is_a_known_target(self):
        # A unit whose plugin was removed upstream must stay tear-downable.
        self.assertEqual(run_sh(self.STUBS + 'is_known_target ghost timer').returncode, 0)

    def test_registered_but_uninstalled_is_a_known_target(self):
        self.assertEqual(run_sh(self.STUBS + 'is_known_target skroutz timer').returncode, 0)

    def test_typo_is_rejected(self):
        self.assertEqual(run_sh(self.STUBS + 'is_known_target skrutz timer').returncode, 1)


class TestRegistryDiagnose(unittest.TestCase):
    """registry_diagnose distinguishes a missing venv from failed plugin discovery."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_missing_venv_reports_reinstall_hint(self):
        # A BASE_DIR with no venv at all -> the reinstall hint, not a discovery error.
        result = run_sh('registry_diagnose', base_dir=self.tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("missing or broken", result.stderr)
        self.assertIn("./install.sh", result.stderr)

    def test_broken_plugin_reports_the_real_discovery_error(self):
        # A real venv + a copied source tree containing one broken plugin package:
        # the diagnosis must surface the PluginDiscoveryError naming that package,
        # which the quiet list_* helpers would otherwise hide as an empty list.
        venv_python = REPO_ROOT / "venv" / "bin" / "python3"
        if not venv_python.exists():  # pragma: no cover - core-only checkout
            self.skipTest("project venv not available")

        shutil.copytree(REPO_ROOT / "src", self.tmp / "src")
        # Symlink the whole venv (not just the binary): venv activation needs the
        # adjacent pyvenv.cfg, or site-packages would be missing and discovery
        # would fail on the framework imports before reaching the broken plugin.
        os.symlink(REPO_ROOT / "venv", self.tmp / "venv")
        broken = self.tmp / "src" / "core" / "scrapers" / "zzzbroken"
        broken.mkdir()
        (broken / "__init__.py").write_text('raise RuntimeError("boom")\n')

        result = run_sh('registry_diagnose', base_dir=self.tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("discovery failed", result.stderr)
        self.assertIn("PluginDiscoveryError", result.stderr)
        self.assertIn("zzzbroken", result.stderr)


class TestListSupportedIntervals(unittest.TestCase):
    def test_matches_the_settings_vocabulary(self):
        venv_python = REPO_ROOT / "venv" / "bin" / "python3"
        if not venv_python.exists():  # pragma: no cover - core-only checkout
            self.skipTest("project venv not available")
        from scrapers.base.settings import SUPPORTED_INTERVALS

        result = run_sh('list_supported_intervals')
        self.assertEqual(result.stdout.strip(), ", ".join(SUPPORTED_INTERVALS))


if __name__ == "__main__":
    unittest.main()
