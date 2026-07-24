"""Unit tests for the systemd inspection adapter.

These functions bridge to the host (unit files on disk, ``systemctl show``);
here the filesystem is a temp ``XDG_CONFIG_HOME`` and the subprocess boundary is
mocked, so every parse/fallback branch runs without systemd. The panel that
*renders* their results is pinned by the ``status__*`` UI snapshots.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

from core.infrastructure.systemd import (
    SYSTEMCTL_QUERY_TIMEOUT_SECONDS,
    get_installed_plugin_units,
    get_systemd_properties,
    get_systemd_user_dir,
    read_timer_oncalendar,
    scraper_unit_name,
)


def test_scraper_unit_name_uses_conventional_timer_and_service_names():
    assert scraper_unit_name("skroutz", "timer") == "skroutz-scraper.timer"
    assert scraper_unit_name("skroutz", "service") == "skroutz-scraper.service"


class _UnitDirCase(unittest.TestCase):
    """A temp XDG_CONFIG_HOME so the systemd user dir lives in the sandbox."""

    def setUp(self):
        self.config_home = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.config_home, ignore_errors=True)
        patcher = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.config_home})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.unit_dir = os.path.join(self.config_home, "systemd", "user")
        os.makedirs(self.unit_dir)

    def _write_unit(self, filename, content=""):
        path = os.path.join(self.unit_dir, filename)
        with open(path, "w") as f:
            f.write(content)
        return path


class TestGetSystemdUserDir(unittest.TestCase):
    def test_honors_xdg_config_home(self):
        with mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": "/custom/cfg"}):
            self.assertEqual(get_systemd_user_dir(), "/custom/cfg/systemd/user")

    def test_falls_back_to_home_config(self):
        env = {k: v for k, v in os.environ.items() if k != "XDG_CONFIG_HOME"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.assertEqual(get_systemd_user_dir(), os.path.expanduser("~/.config/systemd/user"))


class TestGetInstalledPluginUnits(_UnitDirCase):
    def test_maps_each_plugin_to_its_unit_suffixes(self):
        # A full pair, a timer-only orphan, and an unrelated unit to ignore.
        self._write_unit("skroutz-scraper.timer")
        self._write_unit("skroutz-scraper.service")
        self._write_unit("ghost-scraper.timer")
        self._write_unit("unrelated.service")

        self.assertEqual(
            get_installed_plugin_units(),
            {
                "skroutz": {"timer", "service"},
                "ghost": {"timer"},
            },
        )

    def test_empty_dir_yields_no_units(self):
        self.assertEqual(get_installed_plugin_units(), {})


class TestReadTimerOncalendar(_UnitDirCase):
    def test_reads_the_oncalendar_value(self):
        self._write_unit(
            "skroutz-scraper.timer",
            "[Unit]\nDescription=x\n\n[Timer]\nOnCalendar=*-*-* 00/2:00:00\n"
            "RandomizedDelaySec=180s\n",
        )
        self.assertEqual(read_timer_oncalendar("skroutz"), "*-*-* 00/2:00:00")

    def test_missing_unit_reads_as_empty(self):
        self.assertEqual(read_timer_oncalendar("skroutz"), "")

    def test_unit_without_oncalendar_reads_as_empty(self):
        self._write_unit("skroutz-scraper.timer", "[Timer]\nPersistent=true\n")
        self.assertEqual(read_timer_oncalendar("skroutz"), "")


class TestGetSystemdProperties(_UnitDirCase):
    def _props(self, unit="skroutz-scraper.timer"):
        return get_systemd_properties(unit, "ActiveState,Result")

    def test_parses_key_value_output(self):
        self._write_unit("skroutz-scraper.timer", "[Timer]\nOnCalendar=hourly\n")
        with mock.patch.object(
            subprocess,
            "check_output",
            return_value=b"ActiveState=active\nResult=success\n",
        ) as check:
            self.assertEqual(self._props(), {"ActiveState": "active", "Result": "success"})
        check.assert_called_once_with(
            [
                "systemctl",
                "--user",
                "show",
                "skroutz-scraper.timer",
                "--property=ActiveState,Result",
            ],
            stderr=subprocess.DEVNULL,
            timeout=SYSTEMCTL_QUERY_TIMEOUT_SECONDS,
        )

    def test_missing_or_empty_unit_file_skips_the_query(self):
        # No unit on disk (or an empty file): systemctl is never even invoked.
        with mock.patch.object(subprocess, "check_output") as check:
            self.assertEqual(self._props(), {})
            self._write_unit("skroutz-scraper.timer", "")
            self.assertEqual(self._props(), {})
            check.assert_not_called()

    def test_systemctl_failure_degrades_to_empty(self):
        self._write_unit("skroutz-scraper.timer", "[Timer]\n")
        with mock.patch.object(
            subprocess, "check_output", side_effect=subprocess.CalledProcessError(1, "systemctl")
        ):
            self.assertEqual(self._props(), {})

    def test_systemctl_timeout_degrades_to_empty(self):
        self._write_unit("skroutz-scraper.timer", "[Timer]\n")
        with mock.patch.object(
            subprocess,
            "check_output",
            side_effect=subprocess.TimeoutExpired("systemctl", 10),
        ):
            self.assertEqual(self._props(), {})

    def test_empty_output_degrades_to_empty(self):
        self._write_unit("skroutz-scraper.timer", "[Timer]\n")
        with mock.patch.object(subprocess, "check_output", return_value=b"\n"):
            self.assertEqual(self._props(), {})


if __name__ == "__main__":
    unittest.main()
