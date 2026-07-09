"""Integration tests for the project-wide settings: resolving ``config/general.json``
through the shared settings machinery (``plugin=None``) against real temp files, and the
state write-back round-trip (user content preserved, only ``last_reminder`` written).
"""

import datetime
import json
import shutil
import tempfile
import unittest
from unittest import mock

from core.general import ReminderService, general_config_path, resolve_general_settings
from core.general.settings import (
    GENERAL_SETTING_SPECS, KEY_REMINDER, KEY_REMINDER_DAY, KEY_REMINDER_TIME,
)
from core.general.vocab import (
    DEFAULT_REMINDER, DEFAULT_REMINDER_DAY, DEFAULT_REMINDER_TIME,
)
from core.general.reminder import LAST_REMINDER_FIELD
from core.settings import (
    STATUS_OK, STATUS_DEFAULT, STATUS_INVALID, STATUS_NOCFG,
)

from support import write_general as _write_general


class TestResolveGeneralSettings(unittest.TestCase):
    def setUp(self):
        self.cfg_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.cfg_dir, ignore_errors=True)

    def test_valid_value_resolves_ok(self):
        _write_general(self.cfg_dir, {"settings": {"reminder": "3 months"}})
        resolved = resolve_general_settings(self.cfg_dir)
        self.assertEqual(resolved.value(KEY_REMINDER), "3m")
        self.assertEqual(resolved.status(KEY_REMINDER), STATUS_OK)

    def test_unset_key_uses_default(self):
        _write_general(self.cfg_dir, {"settings": {}})
        resolved = resolve_general_settings(self.cfg_dir)
        self.assertEqual(resolved.value(KEY_REMINDER), DEFAULT_REMINDER)
        self.assertEqual(resolved.status(KEY_REMINDER), STATUS_DEFAULT)

    def test_invalid_value_warns_and_uses_default(self):
        _write_general(self.cfg_dir, {"settings": {"reminder": "fortnightly"}})
        resolved = resolve_general_settings(self.cfg_dir)
        self.assertEqual(resolved.value(KEY_REMINDER), DEFAULT_REMINDER)
        self.assertEqual(resolved.status(KEY_REMINDER), STATUS_INVALID)
        self.assertEqual(resolved.resolved(KEY_REMINDER).raw, "fortnightly")

    def test_missing_file_uses_default(self):
        resolved = resolve_general_settings(self.cfg_dir)
        self.assertEqual(resolved.value(KEY_REMINDER), DEFAULT_REMINDER)
        self.assertEqual(resolved.status(KEY_REMINDER), STATUS_NOCFG)

    def test_non_dict_settings_block_sets_warning_and_defaults(self):
        _write_general(self.cfg_dir, {"settings": "1 month"})
        resolved = resolve_general_settings(self.cfg_dir)
        self.assertIsNotNone(resolved.block_warning)
        self.assertEqual(resolved.value(KEY_REMINDER), DEFAULT_REMINDER)

    def test_reminder_day_and_time_resolve(self):
        _write_general(self.cfg_dir, {
            "settings": {"reminder_day": "monday", "reminder_time": "9am"},
        })
        resolved = resolve_general_settings(self.cfg_dir)
        self.assertEqual(resolved.value(KEY_REMINDER_DAY), "Monday")
        self.assertEqual(resolved.status(KEY_REMINDER_DAY), STATUS_OK)
        self.assertEqual(resolved.value(KEY_REMINDER_TIME), "09:00")
        self.assertEqual(resolved.status(KEY_REMINDER_TIME), STATUS_OK)

    def test_reminder_day_and_time_default_when_unset(self):
        _write_general(self.cfg_dir, {"settings": {"reminder": "1 week"}})
        resolved = resolve_general_settings(self.cfg_dir)
        self.assertEqual(resolved.value(KEY_REMINDER_DAY), DEFAULT_REMINDER_DAY)
        self.assertEqual(resolved.status(KEY_REMINDER_DAY), STATUS_DEFAULT)
        self.assertEqual(resolved.value(KEY_REMINDER_TIME), DEFAULT_REMINDER_TIME)
        self.assertEqual(resolved.status(KEY_REMINDER_TIME), STATUS_DEFAULT)

    def test_invalid_reminder_day_and_time_fall_back(self):
        _write_general(self.cfg_dir, {
            "settings": {"reminder_day": "funday", "reminder_time": "25:00"},
        })
        resolved = resolve_general_settings(self.cfg_dir)
        self.assertEqual(resolved.value(KEY_REMINDER_DAY), DEFAULT_REMINDER_DAY)
        self.assertEqual(resolved.status(KEY_REMINDER_DAY), STATUS_INVALID)
        self.assertEqual(resolved.value(KEY_REMINDER_TIME), DEFAULT_REMINDER_TIME)
        self.assertEqual(resolved.status(KEY_REMINDER_TIME), STATUS_INVALID)

    def test_views_follow_the_declared_spec_order(self):
        _write_general(self.cfg_dir, {"settings": {"reminder": "1 week"}})
        labels = [view.label for view in resolve_general_settings(self.cfg_dir).views()]
        self.assertEqual(labels, [spec.label for spec in GENERAL_SETTING_SPECS])


class TestStateWriteBackRoundTrip(unittest.TestCase):
    def test_persist_slot_touches_only_the_state_field(self):
        cfg_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
        original = {
            "settings": {"reminder": "1 week", "future_setting": 42},
            LAST_REMINDER_FIELD: "27-06-2026 13:00:00",
            "unknown_top_level": {"nested": ["kept", "as-is"]},
        }
        _write_general(cfg_dir, original)

        service = ReminderService(cfg_dir, notifier=mock.Mock())
        service._logger = mock.Mock()  # no real log dir
        slot = datetime.datetime(2026, 7, 4, 13, 0, 0)
        # The read + write-back is one sequence: _read_state hands the parsed file to
        # _persist_slot, which mutates only the state field and rewrites it.
        data, _, _ = service._read_state()
        self.assertTrue(service._persist_slot(data, slot))

        with open(general_config_path(cfg_dir)) as f:
            data = json.load(f)
        expected = dict(original)
        expected[LAST_REMINDER_FIELD] = "04-07-2026 13:00:00"
        self.assertEqual(data, expected)


if __name__ == "__main__":
    unittest.main()
