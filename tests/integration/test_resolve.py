"""Tests for the generic settings resolver and its status state machine.

Covers each STATUS_* branch (ok/default/invalid/nocfg) and the unset-vs-invalid
distinction, driven through real temp config files. A setting is a single
``SettingSpec`` keyed by its JSON key; resolution reads the raw ``settings`` block by
key, with no parallel settings dataclass.
"""

import json
import os
import tempfile
import unittest

from scrapers.base.settings import (
    resolve_one,
    SPEC_RETENTION, SPEC_NOTIFY, SPEC_INTERVAL,
    STATUS_OK, STATUS_DEFAULT, STATUS_INVALID, STATUS_NOCFG,
    DEFAULT_LOG_RETENTION_DAYS,
)


class _FakePlugin:
    """Minimal stand-in supplying the plugin-aware interval default."""
    def get_timer_directives(self):
        return {"OnCalendar": "hourly"}


def _write_config(settings):
    """Writes a temp config file with the given ``settings`` block; returns its path."""
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "x.json")
    with open(path, "w") as f:
        json.dump({"settings": settings, "products": []}, f)
    return path


class TestResolveRetention(unittest.TestCase):
    def test_ok(self):
        r = resolve_one(SPEC_RETENTION, _write_config({"log_retention_days": 4}))
        self.assertEqual((r.value, r.status, r.raw), (4, STATUS_OK, 4))

    def test_default_when_unset(self):
        r = resolve_one(SPEC_RETENTION, _write_config({}))
        self.assertEqual((r.value, r.status), (DEFAULT_LOG_RETENTION_DAYS, STATUS_DEFAULT))

    def test_invalid_keeps_default_and_raw(self):
        r = resolve_one(SPEC_RETENTION, _write_config({"log_retention_days": 99}))
        self.assertEqual((r.value, r.status, r.raw), (DEFAULT_LOG_RETENTION_DAYS, STATUS_INVALID, 99))

    def test_nocfg(self):
        r = resolve_one(SPEC_RETENTION, "/no/such/file.json")
        self.assertEqual((r.value, r.status), (DEFAULT_LOG_RETENTION_DAYS, STATUS_NOCFG))

    def test_non_dict_settings_block_is_default(self):
        # A user who wrote `"settings": "oops"` (not an object) gets defaults, not a crash.
        r = resolve_one(SPEC_RETENTION, _write_config("oops"))
        self.assertEqual((r.value, r.status), (DEFAULT_LOG_RETENTION_DAYS, STATUS_DEFAULT))


class TestResolveNotify(unittest.TestCase):
    def test_explicit_false_is_ok_not_invalid(self):
        # A valid `false` must resolve OK (the resolver tests normalize() is None,
        # not falsiness) so it actually silences the push.
        r = resolve_one(SPEC_NOTIFY, _write_config({"notify_scraping_errors": False}))
        self.assertEqual((r.value, r.status), (False, STATUS_OK))

    def test_default_true_when_unset(self):
        r = resolve_one(SPEC_NOTIFY, _write_config({}))
        self.assertEqual((r.value, r.status), (True, STATUS_DEFAULT))

    def test_invalid_defaults_to_true(self):
        r = resolve_one(SPEC_NOTIFY, _write_config({"notify_scraping_errors": "maybe"}))
        self.assertEqual((r.value, r.status, r.raw), (True, STATUS_INVALID, "maybe"))


class TestResolveInterval(unittest.TestCase):
    PLUGIN = _FakePlugin()

    def test_ok_resolves_to_canonical_key(self):
        # The settings layer speaks the user's vocabulary: the value is the canonical
        # interval key, not a systemd OnCalendar (that translation lives at the timer
        # boundary). Many spellings fold onto the same key.
        r = resolve_one(SPEC_INTERVAL, _write_config({"execution_interval": "120m"}),
                        plugin=self.PLUGIN)
        self.assertEqual((r.value, r.status, r.raw), ("2h", STATUS_OK, "120m"))

    def test_default_uses_plugin_cadence_as_key(self):
        # The plugin default "hourly" is shown as the canonical key "1h".
        r = resolve_one(SPEC_INTERVAL, _write_config({}), plugin=self.PLUGIN)
        self.assertEqual((r.value, r.status), ("1h", STATUS_DEFAULT))

    def test_empty_string_is_unset_not_invalid(self):
        r = resolve_one(SPEC_INTERVAL, _write_config({"execution_interval": ""}),
                        plugin=self.PLUGIN)
        self.assertEqual((r.value, r.status), ("1h", STATUS_DEFAULT))

    def test_unsupported_value_is_invalid(self):
        r = resolve_one(SPEC_INTERVAL, _write_config({"execution_interval": "3h"}),
                        plugin=self.PLUGIN)
        self.assertEqual((r.value, r.status, r.raw), ("1h", STATUS_INVALID, "3h"))


if __name__ == "__main__":
    unittest.main()
