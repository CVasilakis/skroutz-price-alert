"""Unit tests for the registry's registration validation gate.

``ScraperRegistry.register`` is the single gate every plugin passes through —
whether it arrives via ``discover()`` or a direct call — so a malformed
descriptor or an ambiguously-routed domain must be rejected here. These tests
register fake, import-light plugin descriptors against a reset registry
(``ScraperRegistry._reset``, the test-only hook) and assert each contract rule
fires. No client/storage class is ever resolved, so no transport library loads.
"""

import unittest
from typing import cast

from core.exceptions import PluginDiscoveryError
from core.scrapers.base.plugin import BasePlugin
from core.scrapers.registry import ScraperRegistry


def _fake_plugin(name="fakestore", domains=("fake-store.example",),
                 config="fakestore.json", specs=None, directives=None):
    """Builds a minimal import-light plugin descriptor for registration tests."""
    class _Fake(BasePlugin):
        @staticmethod
        def get_name():
            return name

        @staticmethod
        def get_display_name():
            return "Fake Store"

        @staticmethod
        def get_supported_domains():
            return list(domains)

        @staticmethod
        def get_config_filename():
            return config

        @staticmethod
        def get_client_class():  # pragma: no cover - registration must not resolve it
            raise AssertionError("client class resolved during registration")

        @staticmethod
        def get_storage_class():  # pragma: no cover - registration must not resolve it
            raise AssertionError("storage class resolved during registration")

    if specs is not None:
        _Fake.get_setting_specs = lambda self: specs
    if directives is not None:
        _Fake.get_timer_directives = lambda self: directives
    return _Fake()


class TestRegisterValidationGate(unittest.TestCase):
    def setUp(self):
        # Snapshot the process-wide registry state and start from a clean slate,
        # so fake registrations never leak into (or inherit from) other tests.
        self._saved_plugins = dict(ScraperRegistry._plugins)
        self._saved_discovered = ScraperRegistry._discovered
        ScraperRegistry._reset()

    def tearDown(self):
        ScraperRegistry._plugins = self._saved_plugins
        ScraperRegistry._discovered = self._saved_discovered

    def test_valid_plugin_registers_and_routes_its_domain(self):
        ScraperRegistry.register(_fake_plugin())
        plugin = ScraperRegistry.plugin_for_url("https://www.fake-store.example/item/1")
        assert plugin is not None, "expected the fake plugin to route its domain"
        self.assertEqual(plugin.get_name(), "fakestore")

    def test_non_plugin_instance_is_rejected(self):
        # cast() forges the wrong type on purpose: the gate must reject it at runtime.
        with self.assertRaises(PluginDiscoveryError):
            ScraperRegistry.register(cast(BasePlugin, object()))

    def test_duplicate_name_is_rejected(self):
        ScraperRegistry.register(_fake_plugin(domains=("fake-store.example",)))
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry.register(_fake_plugin(domains=("elsewhere.example",)))
        self.assertIn("Duplicate plugin name", str(ctx.exception))

    def test_hyphenated_name_is_rejected(self):
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry.register(_fake_plugin(name="my-store"))
        self.assertIn("letters, digits and underscores", str(ctx.exception))

    def test_equal_domain_conflict_is_rejected(self):
        ScraperRegistry.register(_fake_plugin(name="first"))
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry.register(_fake_plugin(name="second"))
        self.assertIn("Domain conflict", str(ctx.exception))

    def test_nested_subdomain_conflict_is_rejected(self):
        ScraperRegistry.register(_fake_plugin(name="first"))
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry.register(
                _fake_plugin(name="second", domains=("shop.fake-store.example",))
            )
        self.assertIn("Domain conflict", str(ctx.exception))

    def test_missing_base_setting_specs_is_rejected(self):
        # "return [my_spec]" instead of "BASE_SETTING_SPECS + [my_spec]" must fail
        # loudly at registration, not at the framework's first strict settings read.
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry.register(_fake_plugin(specs=[]))
        self.assertIn("Extend, don't replace", str(ctx.exception))

    def test_non_canonical_cadence_is_rejected(self):
        with self.assertRaises(PluginDiscoveryError) as ctx:
            ScraperRegistry.register(
                _fake_plugin(directives={"OnCalendar": "*-*-* 03:00:00"})
            )
        self.assertIn("canonical cadences", str(ctx.exception))

    def test_rejected_plugin_is_not_registered(self):
        with self.assertRaises(PluginDiscoveryError):
            ScraperRegistry.register(_fake_plugin(name="bad-name"))
        self.assertNotIn("bad-name", ScraperRegistry._plugins)


if __name__ == "__main__":
    unittest.main()
