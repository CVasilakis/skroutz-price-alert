"""Tests for the settings view layer and the per-scraper extension mechanism.

Verifies that :func:`setting_view` maps a resolved setting to a presentation record,
that :class:`SettingView` exposes the shared icon/default-marker decision, that the
registry exposes one view per built-in setting, and - crucially - that a plugin can add
its own setting (a single :class:`SettingSpec`) and have it resolve and render with no
change to base framework code and no parallel settings class.
"""

import contextlib
import os
import unittest

from core.scrapers.base.settings import (
    SettingSpec, BASE_SETTING_SPECS,
    resolve_one, setting_view,
    SPEC_RETENTION, STATUS_OK, STATUS_INVALID, STATUS_DEFAULT,
)

from support import registry_sandbox, write_settings_config


class _ViewCase(unittest.TestCase):
    def _cfg(self, settings):
        """A temp config file with the given ``settings`` block (auto-cleaned)."""
        return write_settings_config(self, settings)


class TestSettingView(_ViewCase):
    def test_ok_has_no_footnote(self):
        resolved = resolve_one(SPEC_RETENTION, self._cfg({"log_retention_days": 4}))
        view = setting_view(SPEC_RETENTION, resolved)
        self.assertEqual(view.label, "Log Retention")
        self.assertEqual(view.display_value, "4 days")
        self.assertEqual(view.status, STATUS_OK)
        self.assertIsNone(view.footnote)
        self.assertEqual(view.icon, "✅")
        self.assertFalse(view.is_default)

    def test_singular_day_display(self):
        resolved = resolve_one(SPEC_RETENTION, self._cfg({"log_retention_days": 1}))
        self.assertEqual(setting_view(SPEC_RETENTION, resolved).display_value, "1 day")

    def test_default_marks_is_default(self):
        resolved = resolve_one(SPEC_RETENTION, self._cfg({}))
        view = setting_view(SPEC_RETENTION, resolved)
        self.assertEqual(view.status, STATUS_DEFAULT)
        self.assertTrue(view.is_default)
        self.assertEqual(view.icon, "✅")

    def test_invalid_carries_warning_footnote(self):
        resolved = resolve_one(SPEC_RETENTION, self._cfg({"log_retention_days": 99}))
        view = setting_view(SPEC_RETENTION, resolved)
        self.assertEqual(view.status, STATUS_INVALID)
        self.assertEqual(view.footnote, SPEC_RETENTION.warning)
        self.assertEqual(view.display_value, "7 days")  # the default it fell back to
        self.assertEqual(view.icon, "🟡")
        self.assertFalse(view.is_default)  # invalid is flagged, not marked "(default)"

    def test_has_warning_tracks_invalid_only(self):
        # has_warning is the single "this row needs its footnote" decision the render
        # sites share; True only for an invalid value, not for ok or default.
        invalid = setting_view(SPEC_RETENTION, resolve_one(SPEC_RETENTION, self._cfg({"log_retention_days": 99})))
        ok = setting_view(SPEC_RETENTION, resolve_one(SPEC_RETENTION, self._cfg({"log_retention_days": 4})))
        default = setting_view(SPEC_RETENTION, resolve_one(SPEC_RETENTION, self._cfg({})))
        self.assertTrue(invalid.has_warning)
        self.assertFalse(ok.has_warning)
        self.assertFalse(default.has_warning)


# --- Per-scraper extension: a plugin-defined setting, end to end ----------------
# A custom setting is exactly one SettingSpec - no settings dataclass, no from_dict.

def _normalize_pages(raw):
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int) and raw > 0:
        return raw
    return None


_SPEC_PAGES = SettingSpec(
    key="max_pages",
    label="Max Pages",
    normalize=_normalize_pages,
    display=lambda n: str(n),
    warning="Invalid max_pages. Using default.",
    default=3,
)


class TestPerScraperSetting(_ViewCase):
    def test_custom_setting_resolves_ok(self):
        resolved = resolve_one(_SPEC_PAGES, self._cfg({"max_pages": 5}))
        self.assertEqual((resolved.value, resolved.status), (5, STATUS_OK))
        self.assertEqual(setting_view(_SPEC_PAGES, resolved).display_value, "5")

    def test_custom_setting_default_when_unset(self):
        resolved = resolve_one(_SPEC_PAGES, self._cfg({}))
        self.assertEqual((resolved.value, resolved.status), (3, STATUS_DEFAULT))

    def test_custom_setting_invalid(self):
        resolved = resolve_one(_SPEC_PAGES, self._cfg({"max_pages": "lots"}))
        self.assertEqual((resolved.value, resolved.status), (3, STATUS_INVALID))
        self.assertEqual(setting_view(_SPEC_PAGES, resolved).footnote, _SPEC_PAGES.warning)

    def test_extended_spec_list_covers_base_plus_custom(self):
        specs = BASE_SETTING_SPECS + [_SPEC_PAGES]
        labels = [s.label for s in specs]
        self.assertIn("Execution Interval", labels)
        self.assertIn("Max Pages", labels)


class TestRegistryResolveSettings(unittest.TestCase):
    """resolve_all_settings().views() against the real skroutz plugin, in a sandbox."""

    def setUp(self):
        from core.scrapers.skroutz import plugin as skroutz_plugin
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(registry_sandbox(skroutz_plugin))

    def test_one_view_per_builtin_setting(self):
        from core.scrapers.registry import ScraperRegistry
        # The registry joins <config_dir>/<plugin config filename>, so the file must be
        # named for the plugin (skroutz.json), not the generic helper's x.json.
        cfg_dir = os.path.dirname(write_settings_config(
            self, {"execution_interval": "1 hour"}, filename="skroutz.json"))
        views = ScraperRegistry.resolve_all_settings("skroutz", cfg_dir).views()
        labels = [v.label for v in views]
        self.assertEqual(labels, ["Execution Interval", "Log Retention", "Notify On Errors"])
        self.assertEqual(views[0].display_value, "1h")
        self.assertEqual(views[0].status, STATUS_OK)


if __name__ == "__main__":
    unittest.main()
