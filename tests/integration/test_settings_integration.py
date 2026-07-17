"""Integration tests for the settings wiring: single-read resolution, the
``ResolvedSettings`` accessor, the timer-directive translation boundary, discovery-time
spec validation, the per-target injection into client/storage, and the ``update_item``
field guard.

The registry-facing tests run against the *real* skroutz plugin, but registered inside
a ``registry_sandbox`` — so they never depend on (or dirty) the process-wide registry
state other tests see.
"""

import builtins
import contextlib
import os
import unittest
from unittest import mock

from core.scrapers.base.settings import (
    SettingSpec, ResolvedSettings, BASE_SETTING_SPECS,
    resolve_all, oncalendar_for,
    KEY_INTERVAL, KEY_RETENTION, KEY_NOTIFY,
    STATUS_OK, DEFAULT_LOG_RETENTION_DAYS,
)
from core.scrapers.base.client import BaseScraperClient
from core.scrapers.registry import ScraperRegistry
from core.scrapers.skroutz import plugin as skroutz_plugin
from core.exceptions import PluginDiscoveryError

from support import fake_plugin, registry_sandbox, write_settings_config


class _SkroutzConfigCase(unittest.TestCase):
    """Base: temp skroutz.json configs (auto-cleaned) + helpers shared below."""

    def _cfg_path(self, settings):
        return write_settings_config(self, settings, filename="skroutz.json")

    def _cfg_dir(self, settings):
        return os.path.dirname(self._cfg_path(settings))


class _SandboxedSkroutzCase(_SkroutzConfigCase):
    """Adds a clean registry holding exactly the real skroutz plugin."""

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(registry_sandbox(skroutz_plugin))


class TestResolveAllSingleRead(_SkroutzConfigCase):
    def test_reads_config_once_for_all_specs(self):
        path = self._cfg_path({"execution_interval": "2h"})

        real_open = builtins.open
        opens = {"n": 0}

        def counting_open(file, *args, **kwargs):
            if file == path:
                opens["n"] += 1
            return real_open(file, *args, **kwargs)

        with mock.patch("builtins.open", counting_open):
            resolved = resolve_all(BASE_SETTING_SPECS, path, fake_plugin())

        self.assertIsInstance(resolved, ResolvedSettings)
        # Three base specs, but the file is opened exactly once.
        self.assertEqual(opens["n"], 1)


class TestResolvedSettingsAccessor(_SkroutzConfigCase):
    def test_value_get_status_and_views(self):
        resolved = resolve_all(BASE_SETTING_SPECS,
                               self._cfg_path({"log_retention_days": 10}), fake_plugin())

        self.assertEqual(resolved.value(KEY_RETENTION), 10)
        self.assertEqual(resolved.status(KEY_RETENTION), STATUS_OK)
        self.assertEqual(resolved.value(KEY_NOTIFY), True)          # default
        self.assertEqual(resolved.get("does_not_exist", "fallback"), "fallback")
        labels = [v.label for v in resolved.views()]
        self.assertEqual(labels, ["Execution Interval", "Log Retention", "Notify On Errors"])

    def test_unknown_keys_are_sorted_ignored_and_known_values_survive(self):
        resolved = resolve_all(
            BASE_SETTING_SPECS,
            self._cfg_path({"z_future": 1, "log_retention_days": 10, "a_typo": True}),
            fake_plugin(),
        )
        self.assertEqual(resolved.value(KEY_RETENTION), 10)
        self.assertEqual(resolved.unknown_keys, ("a_typo", "z_future"))
        self.assertEqual(
            resolved.unknown_warning,
            "Unknown setting key(s) ignored: a_typo, z_future",
        )

    def test_malformed_block_takes_precedence_over_unknown_key_detection(self):
        resolved = resolve_all(BASE_SETTING_SPECS, self._cfg_path(["future"]), fake_plugin())
        self.assertIsNotNone(resolved.block_warning)
        self.assertEqual(resolved.unknown_keys, ())


class TestTimerDirectiveTranslation(_SandboxedSkroutzCase):
    """resolve_timer_directives owns the canonical-key -> OnCalendar translation."""

    def test_valid_interval_translates_to_oncalendar(self):
        cfg_dir = self._cfg_dir({"execution_interval": "2h"})
        directives = ScraperRegistry.resolve_timer_directives("skroutz", cfg_dir)
        self.assertEqual(directives["OnCalendar"], oncalendar_for("2h"))

    def test_invalid_interval_falls_back_to_plugin_default(self):
        cfg_dir = self._cfg_dir({"execution_interval": "3h"})  # unsupported
        directives = ScraperRegistry.resolve_timer_directives("skroutz", cfg_dir)
        # Skroutz keeps the BasePlugin default cadence.
        self.assertEqual(directives["OnCalendar"], "hourly")

    def test_unset_interval_falls_back_to_plugin_default(self):
        cfg_dir = self._cfg_dir({})
        directives = ScraperRegistry.resolve_timer_directives("skroutz", cfg_dir)
        self.assertEqual(directives["OnCalendar"], "hourly")


# --- Discovery-time spec validation ---------------------------------------------

def _spec(key, label="X"):
    return SettingSpec(key=key, label=label, normalize=lambda r: r,
                       display=str, warning="w", default=None)


def _SpecPlugin(specs, directives=None):
    """A plugin descriptor whose only interesting behavior is its setting specs.

    Built on the shared ``support.fake_plugin`` factory; ``directives=None`` keeps
    the canonical ``BasePlugin`` default cadence.
    """
    return fake_plugin(name="specfake", domains=("specfake.example",),
                       config="specfake.json", display_name="SpecFake",
                       specs=specs, directives=directives)


class TestDiscoverySpecValidation(unittest.TestCase):
    def test_duplicate_keys_rejected(self):
        plugin = _SpecPlugin([_spec("dup", "A"), _spec("dup", "B")])
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry._validate_plugin_contract("specfake", plugin)
        self.assertIn("duplicate setting key", str(ctx.exception).lower())

    def test_empty_key_rejected(self):
        plugin = _SpecPlugin([_spec("  ")])
        with self.assertRaises(PluginDiscoveryError):
            ScraperRegistry._validate_plugin_contract("specfake", plugin)

    def test_non_spec_entry_rejected(self):
        plugin = _SpecPlugin([_spec("ok"), "not a spec"])
        with self.assertRaises(PluginDiscoveryError):
            ScraperRegistry._validate_plugin_contract("specfake", plugin)

    def test_base_specs_pass(self):
        plugin = _SpecPlugin(list(BASE_SETTING_SPECS))
        # Should not raise.
        ScraperRegistry._validate_plugin_contract("specfake", plugin)

    def test_missing_base_spec_rejected(self):
        # A plugin that REPLACES instead of EXTENDS drops the base settings the
        # framework reads with the strict accessor — rejected loudly at discovery.
        plugin = _SpecPlugin([_spec("region")])
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry._validate_plugin_contract("specfake", plugin)
        self.assertIn("missing", str(ctx.exception).lower())

    def test_base_plus_custom_passes(self):
        plugin = _SpecPlugin(list(BASE_SETTING_SPECS) + [_spec("region")])
        # Extending the base set is the supported shape; should not raise.
        ScraperRegistry._validate_plugin_contract("specfake", plugin)


class TestDiscoveryCadenceValidation(unittest.TestCase):
    """A plugin's default OnCalendar must be one of the canonical cadences."""

    def test_canonical_cadence_passes(self):
        plugin = _SpecPlugin(list(BASE_SETTING_SPECS), {"OnCalendar": "daily"})
        ScraperRegistry._validate_plugin_contract("specfake", plugin)  # no raise

    def test_non_canonical_cadence_rejected(self):
        plugin = _SpecPlugin(list(BASE_SETTING_SPECS), {"OnCalendar": "*-*-* 03:00:00"})
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry._validate_plugin_contract("specfake", plugin)
        self.assertIn("oncalendar", str(ctx.exception).lower())

    def test_missing_oncalendar_rejected(self):
        plugin = _SpecPlugin(list(BASE_SETTING_SPECS), {"OnBootSec": "5min"})
        with self.assertRaises(PluginDiscoveryError):
            ScraperRegistry._validate_plugin_contract("specfake", plugin)


class TestMalformedSettingsBlock(_SkroutzConfigCase):
    """A present-but-non-object settings block sets block_warning and uses defaults."""

    def test_non_dict_block_sets_warning_and_defaults(self):
        path = self._cfg_path("1h")  # a string, not an object
        resolved = resolve_all(BASE_SETTING_SPECS, path, fake_plugin())
        self.assertIsNotNone(resolved.block_warning)
        # Every setting still falls back to its default.
        self.assertEqual(resolved.value(KEY_RETENTION), DEFAULT_LOG_RETENTION_DAYS)
        self.assertEqual(resolved.value(KEY_NOTIFY), True)

    def test_well_formed_block_no_warning(self):
        path = self._cfg_path({"log_retention_days": 5})
        resolved = resolve_all(BASE_SETTING_SPECS, path, fake_plugin())
        self.assertIsNone(resolved.block_warning)


class TestSettingsInjection(_SandboxedSkroutzCase):
    def test_storage_manager_receives_resolved_settings(self):
        registry = ScraperRegistry(self._cfg_dir({"log_retention_days": 9}))
        manager = registry.get_manager("skroutz")
        settings = manager.settings
        assert isinstance(settings, ResolvedSettings)  # narrows the Optional
        self.assertEqual(settings.value(KEY_RETENTION), 9)

    def test_client_receives_resolved_settings(self):
        try:
            import tls_client  # noqa: F401  (the skroutz client's transport)
        except Exception:  # pragma: no cover - dependency not installed
            self.skipTest("tls_client not installed; client cannot be instantiated")
        registry = ScraperRegistry(self._cfg_dir({"execution_interval": "2h"}))
        try:
            client = registry.get_scraper("https://www.skroutz.gr/s/123/product.html")
            settings = client.settings
            assert isinstance(settings, ResolvedSettings)  # narrows the Optional
            self.assertEqual(settings.value(KEY_INTERVAL), "2h")
        finally:
            registry.close_all()

    def test_base_client_settings_default_none(self):
        # Settings arrive through the constructor (passed by the registry); a client
        # built without them — e.g. in a unit test — defaults to None.
        class _MinimalClient(BaseScraperClient):
            def scrape_product(self, product_url):  # pragma: no cover - never called
                raise NotImplementedError

        self.assertIsNone(_MinimalClient().settings)

    def test_base_client_settings_available_during_init(self):
        # The constructor stores settings before a subclass's own __init__ body runs,
        # so a client can shape its session/transport from a setting at construction.
        sentinel = ResolvedSettings([])
        seen_during_init = []

        class _InitReadingClient(BaseScraperClient):
            def __init__(self, settings=None):
                super().__init__(settings)
                seen_during_init.append(self.settings)

            def scrape_product(self, product_url):  # pragma: no cover - never called
                raise NotImplementedError

        _InitReadingClient(settings=sentinel)
        self.assertEqual(seen_during_init, [sentinel])


class TestUpdateItemFieldGuard(_SandboxedSkroutzCase):
    def test_unknown_update_key_raises(self):
        registry = ScraperRegistry(self._cfg_dir({}))
        manager = registry.get_manager("skroutz")
        url = "https://www.skroutz.gr/s/123/product.html"
        # A real MODEL field is accepted...
        manager.update_item(url, last_price=12.5)
        # ...a typo'd field is rejected loudly instead of silently persisted.
        with self.assertRaises(ValueError):
            manager.update_item(url, last_pirce=12.5)


if __name__ == "__main__":
    unittest.main()
