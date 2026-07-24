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


def test_install_does_not_invoke_configuration_migration():
    install = (REPO_ROOT / "install.sh").read_text(encoding="utf-8")
    assert "catalog_cli migration" not in install


def run_sh(script: str, base_dir=REPO_ROOT, xdg_config_home=None, extra_env=None):
    """Runs `script` under `sh -eu` with common.sh sourced (the caller contract).

    Args:
        script: The shell snippet to run after the library is sourced.
        base_dir: The BASE_DIR to expose (the sourcing contract requires it).
        xdg_config_home: When set, exported so SYSTEMD_USER_DIR lands in a tmp dir.
        extra_env: Overrides applied on top of the inherited environment before
            sourcing; a value of None *removes* the variable (so the color-guard
            tests are immune to NO_COLOR/CLICOLOR_FORCE in the developer's shell).

    Returns:
        subprocess.CompletedProcess: With captured text stdout/stderr.
    """
    env = os.environ.copy()
    if xdg_config_home is not None:
        env["XDG_CONFIG_HOME"] = str(xdg_config_home)
    for key, value in (extra_env or {}).items():
        if value is None:
            env.pop(key, None)
        else:
            env[key] = value
    full = f'BASE_DIR="{base_dir}"\n. "{COMMON_SH}"\n{script}'
    return subprocess.run(
        ["sh", "-eu", "-c", full],
        capture_output=True,
        text=True,
        env=env,
    )


class TestColorGuard(unittest.TestCase):
    """The color variables are set at source time: on for a TTY (or when forced),
    empty when piped or when NO_COLOR is set - so redirected output (logs, tee,
    the systemd journal) never captures escape sequences."""

    RED_SEQ = r"\033[0;31m"  # the literal characters common.sh assigns

    def _red(self, **env):
        # capture_output pipes stdout, so [ -t 1 ] is false in every test here.
        return run_sh(
            'printf %s "$RED"',
            extra_env={
                "NO_COLOR": None,
                "CLICOLOR_FORCE": None,
                **env,
            },
        ).stdout

    def test_piped_output_is_colorless(self):
        self.assertEqual(self._red(), "")

    def test_clicolor_force_keeps_colors_on_a_pipe(self):
        # The snapshot harness relies on this to capture colored transcripts.
        self.assertEqual(self._red(CLICOLOR_FORCE="1"), self.RED_SEQ)

    def test_no_color_wins_over_force(self):
        self.assertEqual(self._red(NO_COLOR="1", CLICOLOR_FORCE="1"), "")


class TestNamingHelpers(unittest.TestCase):
    def test_unit_name(self):
        result = run_sh("unit_name skroutz timer")
        self.assertEqual(result.stdout, "skroutz-scraper.timer")

    def test_stream_contains_hit_and_miss(self):
        self.assertEqual(run_sh('stream_contains b "a\nb\nc"').returncode, 0)
        self.assertEqual(run_sh('stream_contains z "a\nb\nc"').returncode, 1)

    def test_stream_contains_with_empty_list(self):
        self.assertEqual(run_sh('stream_contains z ""').returncode, 1)

    def test_stream_add_unique_preserves_order_and_deduplicates(self):
        result = run_sh('items="alpha\nbeta"\nstream_add_unique "$items" beta')
        self.assertEqual(result.stdout.splitlines(), ["alpha", "beta"])

    def test_plugin_stream_value_preserves_spaces(self):
        result = run_sh(
            "rows=\"$(printf 'foo\\tcustom feed.json\\nbar\\tother.json')\"\n"
            'plugin_stream_value foo "$rows"'
        )
        self.assertEqual(result.stdout, "custom feed.json")


class TestManifestSnapshot(unittest.TestCase):
    def test_all_manifest_projections_share_one_acquisition(self):
        temp_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, temp_dir, ignore_errors=True)
        counter = temp_dir / "calls"
        script = (
            f'catalog_cli() {{ printf x >> "{counter}"; '
            "printf 'alpha\\tAlpha Store\\t/example.json\\t/req.txt\\thourly\\tok\\n'; }\n"
            "load_plugin_manifest\n"
            "list_plugins >/dev/null\n"
            "plugin_display_name alpha >/dev/null\n"
            "list_plugin_examples >/dev/null\n"
            "list_plugin_requirements >/dev/null\n"
            "list_plugin_schedules >/dev/null\n"
            "list_interval_status >/dev/null\n"
        )
        result = run_sh(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(counter.read_text(), "x")


class TestUnitFileRoundTrip(unittest.TestCase):
    """The unit writer accepts and recovers one framework-owned calendar value."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.unit_dir = self.tmp / "systemd" / "user"

    def _write(self, plugin, calendar):
        return run_sh(
            f'mkdir -p "$SYSTEMD_USER_DIR"\nwrite_plugin_units {plugin} "{calendar}"',
            xdg_config_home=self.tmp,
        )

    def test_round_trip_recovers_the_trigger_block(self):
        calendar = "*-*-* 00/2:00:00"
        self.assertEqual(self._write("foo", calendar).returncode, 0)
        read = run_sh("read_timer_oncalendar foo", xdg_config_home=self.tmp)
        self.assertEqual(read.stdout, calendar)

    def test_framework_keys_are_appended_but_not_read_back(self):
        self._write("foo", "hourly")
        timer_text = (self.unit_dir / "foo-scraper.timer").read_text()
        self.assertIn("RandomizedDelaySec=180s", timer_text)
        self.assertIn("Persistent=true", timer_text)
        self.assertIn("Unit=foo-scraper.service", timer_text)
        read = run_sh("read_timer_oncalendar foo", xdg_config_home=self.tmp)
        self.assertEqual(read.stdout, "hourly")

    def test_service_dispatches_through_run_sh(self):
        self._write("foo", "hourly")
        service_text = (self.unit_dir / "foo-scraper.service").read_text()
        self.assertIn(f'ExecStart="{REPO_ROOT}/scripts/run.sh" --quiet --foo', service_text)

    def test_read_timer_calendar_missing_unit_is_empty_success(self):
        read = run_sh("read_timer_oncalendar ghost", xdg_config_home=self.tmp)
        self.assertEqual((read.returncode, read.stdout), (0, ""))

    def test_failed_render_does_not_masquerade_as_success_when_old_files_exist(self):
        self.assertEqual(self._write("foo", "hourly").returncode, 0)
        service = self.unit_dir / "foo-scraper.service"
        timer = self.unit_dir / "foo-scraper.timer"
        old_service = service.read_text()
        old_timer = timer.read_text()

        failed = run_sh(
            'render_plugin_service() { return 1; }\nwrite_plugin_units foo "daily"',
            xdg_config_home=self.tmp,
        )
        self.assertNotEqual(failed.returncode, 0)
        self.assertEqual(service.read_text(), old_service)
        self.assertEqual(timer.read_text(), old_timer)

    def test_timer_only_update_does_not_rewrite_service(self):
        self.assertEqual(self._write("foo", "hourly").returncode, 0)
        service = self.unit_dir / "foo-scraper.service"
        service.write_text(service.read_text() + "# preserved\n")

        updated = run_sh(
            'write_plugin_timer_unit foo "daily"',
            xdg_config_home=self.tmp,
        )
        self.assertEqual(updated.returncode, 0)
        self.assertTrue(service.read_text().endswith("# preserved\n"))
        self.assertEqual(
            run_sh("read_timer_oncalendar foo", xdg_config_home=self.tmp).stdout,
            "daily",
        )


class TestKnownTargets(unittest.TestCase):
    """The teardown validation set: registered ∪ installed, de-duplicated."""

    STUBS = (
        "list_plugins() { printf '%s\\n' skroutz amazon; }\n"
        "list_installed_plugins() { printf '%s\\n' amazon ghost; }\n"
    )

    def test_union_preserves_first_seen_order_and_dedups(self):
        result = run_sh(self.STUBS + "known_targets timer")
        self.assertEqual(result.stdout.split(), ["skroutz", "amazon", "ghost"])

    def test_orphan_unit_is_a_known_target(self):
        # A unit whose plugin was removed upstream must stay tear-downable.
        self.assertEqual(run_sh(self.STUBS + "is_known_target ghost timer").returncode, 0)

    def test_registered_but_uninstalled_is_a_known_target(self):
        self.assertEqual(run_sh(self.STUBS + "is_known_target skroutz timer").returncode, 0)

    def test_typo_is_rejected(self):
        self.assertEqual(run_sh(self.STUBS + "is_known_target skrutz timer").returncode, 1)

    def test_installed_target_union_includes_service_only_unit(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        unit_dir = tmp / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        (unit_dir / "timeronly-scraper.timer").touch()
        (unit_dir / "serviceonly-scraper.service").touch()
        result = run_sh("list_installed_targets", xdg_config_home=tmp)
        self.assertEqual(result.stdout.split(), ["timeronly", "serviceonly"])

    def test_malformed_installed_unit_name_is_diagnosed_and_ignored(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        unit_dir = tmp / "systemd" / "user"
        unit_dir.mkdir(parents=True)
        (unit_dir / "bad target-scraper.timer").touch()
        result = run_sh("list_installed_plugins timer", xdg_config_home=tmp)
        self.assertEqual(result.stdout, "")
        self.assertIn("malformed installed unit name", result.stderr)


class TestCatalogDiagnose(unittest.TestCase):
    """catalog_diagnose distinguishes a missing venv from failed plugin discovery."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def test_missing_venv_reports_reinstall_hint(self):
        # A BASE_DIR with no venv at all -> the reinstall hint, not a discovery error.
        result = run_sh("catalog_diagnose", base_dir=self.tmp)
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
        broken = self.tmp / "src" / "core" / "scrapers" / "plugins" / "zzzbroken"
        broken.mkdir()
        (broken / "__init__.py").write_text('raise RuntimeError("boom")\n')

        result = run_sh("catalog_diagnose", base_dir=self.tmp)
        self.assertEqual(result.returncode, 1)
        self.assertIn("discovery failed", result.stderr)
        self.assertIn("PluginDiscoveryError", result.stderr)
        self.assertIn("zzzbroken", result.stderr)


class TestListSupportedIntervals(unittest.TestCase):
    def test_matches_the_settings_vocabulary(self):
        venv_python = REPO_ROOT / "venv" / "bin" / "python3"
        if not venv_python.exists():  # pragma: no cover - core-only checkout
            self.skipTest("project venv not available")
        from core.scrapers.framework.settings import SUPPORTED_INTERVALS

        result = run_sh("list_supported_intervals")
        self.assertEqual(result.stdout.strip(), ", ".join(SUPPORTED_INTERVALS))


class TestRealRegistryBridge(unittest.TestCase):
    """The list_* helpers' real Python bodies, run through sh against the real venv.

    The UI shell snapshots fake the venv responder (canned answers), so these are
    the only place the actual Python inside common.sh's heredocs executes via the
    shell — a break in the shell<->Python contract (the tab-stream shape, the
    name/filename pairing) fails here instead of hiding behind the shim. The bridge runs
    against an empty temporary config directory so private user configuration cannot
    affect its result.
    """

    def setUp(self):
        if not (REPO_ROOT / "venv" / "bin" / "python3").exists():  # pragma: no cover
            self.skipTest("project venv not available")
        self.base_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.base_dir, ignore_errors=True)
        (self.base_dir / "venv").symlink_to(REPO_ROOT / "venv", target_is_directory=True)
        (self.base_dir / "src").symlink_to(REPO_ROOT / "src", target_is_directory=True)
        (self.base_dir / "config").mkdir()

    def test_list_plugins_yields_registered_names(self):
        result = run_sh("list_plugins", base_dir=self.base_dir)
        self.assertIn("skroutz", result.stdout.split())

    def test_list_plugin_examples_pairs_names_with_package_paths(self):
        result = run_sh("list_plugin_examples", base_dir=self.base_dir)
        pairs = dict(line.split("\t", 1) for line in result.stdout.splitlines())
        self.assertTrue(pairs.get("skroutz", "").endswith("/skroutz/config.example.json"))

    def test_list_plugin_schedules_is_a_value_stream(self):
        result = run_sh("list_plugin_schedules", base_dir=self.base_dir)
        lines = result.stdout.splitlines()
        self.assertTrue(lines)
        for line in lines:
            plugin, tab, calendar = line.partition("\t")
            self.assertEqual(tab, "\t", f"no tab separator in {line!r}")
            self.assertTrue(plugin)
            self.assertTrue(calendar)
        self.assertTrue(any(line.startswith("skroutz\t") for line in lines))

    def test_list_interval_status_reports_a_known_status(self):
        result = run_sh("list_interval_status", base_dir=self.base_dir)
        statuses = dict(line.split("\t") for line in result.stdout.splitlines())
        self.assertEqual(statuses.get("skroutz"), "nocfg")


class TestVenvResponderMarkers(unittest.TestCase):
    """The shell-snapshot harness recognizes common.sh's inline Python heredocs by
    marker substrings. If a heredoc in common.sh is reworded past its marker, the
    fake venv responder would silently answer nothing and the transcripts would
    drift — so pin the coupling from both ends here."""

    def test_every_marker_appears_in_common_sh_and_in_the_shim(self):
        # Load the catalog package first: entering via ui.harness.shell directly
        # would re-enter it mid-import through the sh_* scenario modules.
        import ui.catalog  # noqa: F401
        from ui.harness.shell import _VENV_PYTHON_SHIM, VENV_RESPONDER_MARKERS

        common_text = COMMON_SH.read_text()
        for marker in VENV_RESPONDER_MARKERS:
            with self.subTest(marker=marker):
                self.assertIn(
                    marker,
                    common_text,
                    f"marker {marker!r} no longer appears in common.sh - update the "
                    f"venv responder in tests/ui/harness/shell.py (and this list) to "
                    f"match the reworded heredoc",
                )
                self.assertIn(
                    marker,
                    _VENV_PYTHON_SHIM,
                    f"marker {marker!r} is declared but the shim does not match it - "
                    f"keep VENV_RESPONDER_MARKERS and the case patterns in sync",
                )


if __name__ == "__main__":
    unittest.main()
