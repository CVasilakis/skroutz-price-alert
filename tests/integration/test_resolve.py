"""Tests for the generic settings resolver and its status state machine.

Covers each STATUS_* branch (ok/default/invalid/nocfg) and the unset-vs-invalid
distinction, driven through real temp config files. A setting is a single
``SettingSpec`` keyed by its JSON key; resolution reads the raw ``settings`` block by
key, with no parallel settings dataclass.
"""

import unittest
import os
import shutil
import tempfile

from core.scrapers.base.settings import (
    resolve_one, resolve_all,
    SPEC_RETENTION, SPEC_NOTIFY, SPEC_INTERVAL,
    BASE_SETTING_SPECS, KEY_RETENTION,
    STATUS_OK, STATUS_DEFAULT, STATUS_INVALID, STATUS_NOCFG,
    DEFAULT_LOG_RETENTION_DAYS,
)

from support import fake_plugin, write_settings_config


class _ResolveCase(unittest.TestCase):
    def _cfg(self, settings):
        """A temp config file with the given ``settings`` block (auto-cleaned)."""
        return write_settings_config(self, settings)


class TestResolveRetention(_ResolveCase):
    def test_ok(self):
        r = resolve_one(SPEC_RETENTION, self._cfg({"log_retention_days": 4}))
        self.assertEqual((r.value, r.status, r.raw), (4, STATUS_OK, 4))

    def test_default_when_unset(self):
        r = resolve_one(SPEC_RETENTION, self._cfg({}))
        self.assertEqual((r.value, r.status), (DEFAULT_LOG_RETENTION_DAYS, STATUS_DEFAULT))

    def test_invalid_keeps_default_and_raw(self):
        r = resolve_one(SPEC_RETENTION, self._cfg({"log_retention_days": 99}))
        self.assertEqual((r.value, r.status, r.raw), (DEFAULT_LOG_RETENTION_DAYS, STATUS_INVALID, 99))

    def test_nocfg(self):
        r = resolve_one(SPEC_RETENTION, "/no/such/file.json")
        self.assertEqual((r.value, r.status), (DEFAULT_LOG_RETENTION_DAYS, STATUS_NOCFG))

    def test_non_dict_settings_block_is_default(self):
        # A user who wrote `"settings": "oops"` (not an object) gets defaults, not a crash.
        r = resolve_one(SPEC_RETENTION, self._cfg("oops"))
        self.assertEqual((r.value, r.status), (DEFAULT_LOG_RETENTION_DAYS, STATUS_DEFAULT))


class TestResolveNotify(_ResolveCase):
    def test_explicit_false_is_ok_not_invalid(self):
        # A valid `false` must resolve OK (the resolver tests normalize() is None,
        # not falsiness) so it actually silences the push.
        r = resolve_one(SPEC_NOTIFY, self._cfg({"notify_scraping_errors": False}))
        self.assertEqual((r.value, r.status), (False, STATUS_OK))

    def test_default_true_when_unset(self):
        r = resolve_one(SPEC_NOTIFY, self._cfg({}))
        self.assertEqual((r.value, r.status), (True, STATUS_DEFAULT))

    def test_invalid_defaults_to_true(self):
        r = resolve_one(SPEC_NOTIFY, self._cfg({"notify_scraping_errors": "maybe"}))
        self.assertEqual((r.value, r.status, r.raw), (True, STATUS_INVALID, "maybe"))


class TestResolveInterval(_ResolveCase):
    # support.fake_plugin keeps the BasePlugin default cadence ("hourly"), which is
    # exactly the plugin-aware default these tests need.
    PLUGIN = fake_plugin()

    def test_ok_resolves_to_canonical_key(self):
        # The settings layer speaks the user's vocabulary: the value is the canonical
        # interval key, not a systemd OnCalendar (that translation lives at the timer
        # boundary). Many spellings fold onto the same key.
        r = resolve_one(SPEC_INTERVAL, self._cfg({"execution_interval": "120m"}),
                        plugin=self.PLUGIN)
        self.assertEqual((r.value, r.status, r.raw), ("2h", STATUS_OK, "120m"))

    def test_default_uses_plugin_cadence_as_key(self):
        # The plugin default "hourly" is shown as the canonical key "1h".
        r = resolve_one(SPEC_INTERVAL, self._cfg({}), plugin=self.PLUGIN)
        self.assertEqual((r.value, r.status), ("1h", STATUS_DEFAULT))

    def test_empty_string_is_unset_not_invalid(self):
        r = resolve_one(SPEC_INTERVAL, self._cfg({"execution_interval": ""}),
                        plugin=self.PLUGIN)
        self.assertEqual((r.value, r.status), ("1h", STATUS_DEFAULT))

    def test_unsupported_value_is_invalid(self):
        r = resolve_one(SPEC_INTERVAL, self._cfg({"execution_interval": "3h"}),
                        plugin=self.PLUGIN)
        self.assertEqual((r.value, r.status, r.raw), ("1h", STATUS_INVALID, "3h"))


class TestSettingsReadErrors(unittest.TestCase):
    def test_invalid_utf8_uses_defaults_and_sets_block_warning(self):
        cfg_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
        path = os.path.join(cfg_dir, "x.json")
        with open(path, "wb") as file:
            file.write(b"\xff")

        resolved = resolve_all(BASE_SETTING_SPECS, path, plugin=fake_plugin())

        self.assertEqual(resolved.value(KEY_RETENTION), DEFAULT_LOG_RETENTION_DAYS)
        self.assertIn("could not be read", resolved.block_warning)


if __name__ == "__main__":
    unittest.main()
