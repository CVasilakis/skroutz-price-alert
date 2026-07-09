"""Unit tests for the registry's registration validation gate.

``ScraperRegistry.register`` is the single gate every plugin passes through —
whether it arrives via ``discover()`` or a direct call — so a malformed
descriptor or an ambiguously-routed domain must be rejected here. These tests
register fake, import-light plugin descriptors (the shared ``support.fake_plugin``
factory) against a clean registry (the shared ``support.registry_sandbox``) and
assert each contract rule fires. No client/storage class is ever resolved, so no
transport library loads.
"""

import contextlib
import unittest
from typing import cast

from core.exceptions import PluginDiscoveryError
from core.scrapers.base.plugin import BasePlugin
from core.scrapers.registry import ScraperRegistry

from support import fake_plugin as _fake_plugin, registry_sandbox


class TestRegisterValidationGate(unittest.TestCase):
    def setUp(self):
        # Snapshot the process-wide registry state and start from a clean slate,
        # so fake registrations never leak into (or inherit from) other tests.
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(registry_sandbox())

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
