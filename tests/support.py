"""Shared helpers for the test suite (importable: ``tests`` is on the pythonpath).

Holds the ``config/general.json`` read/write helpers (built on the production
``general_config_path`` so the filename lives in exactly one place) and the
autospec'd mock factories for the orchestrator's collaborators. The factories use
``mock.create_autospec`` so every mocked call is checked against the real class's
signature — a parameter added to (or reordered in) ``Notifier``, the UI strategy,
the registry, or the storage/client bases fails these mocks instead of passing
silently.
"""

import contextlib
import json
from unittest import mock

from core.general import general_config_path
from core.notifier import Notifier
from core.scrapers.base.client import BaseScraperClient
from core.scrapers.base.plugin import BasePlugin
from core.scrapers.base.storage import BaseDataManager
from core.scrapers.registry import ScraperRegistry
from core.ui.tui import ExecutionStrategy


def fake_plugin(name="fakestore", domains=("fake-store.example",), config="fakestore.json",
                display_name="Fake Store", specs=None, directives=None,
                client_class=None, storage_class=None) -> BasePlugin:
    """Builds a minimal import-light plugin descriptor for tests.

    By default the class getters *raise*, proving that registration and the other
    import-light paths never resolve them; pass ``client_class``/``storage_class``
    to make the plugin instantiable (e.g. for orchestrator-level tests). ``specs``
    and ``directives`` override :meth:`BasePlugin.get_setting_specs` /
    :meth:`BasePlugin.get_timer_directives` when given, so validation-gate tests
    can feed the registry malformed values.
    """
    class _Fake(BasePlugin):
        @staticmethod
        def get_name():
            return name

        @staticmethod
        def get_display_name():
            return display_name

        @staticmethod
        def get_supported_domains():
            return list(domains)

        @staticmethod
        def get_config_filename():
            return config

        @staticmethod
        def get_client_class():
            if client_class is None:  # pragma: no cover - import-light guard
                raise AssertionError("client class resolved unexpectedly")
            return client_class

        @staticmethod
        def get_storage_class():
            if storage_class is None:  # pragma: no cover - import-light guard
                raise AssertionError("storage class resolved unexpectedly")
            return storage_class

    if specs is not None:
        _Fake.get_setting_specs = lambda self: specs
    if directives is not None:
        _Fake.get_timer_directives = lambda self: directives
    return _Fake()


@contextlib.contextmanager
def registry_sandbox(*plugins: BasePlugin, frozen: bool = True):
    """Snapshots the process-wide plugin registry and yields a clean, isolated one.

    Resets ``ScraperRegistry`` (the test-only ``_reset`` hook), registers the given
    plugins, and restores the original class state on exit. With ``frozen=True``
    (the default) auto-discovery is suppressed afterwards, so the sandbox holds
    *exactly* the given plugins — a lookup can't surprise-register the real stores
    mid-test. Pass ``frozen=False`` to leave discovery armed (for tests exercising
    discovery itself).

    Usable directly as a ``with`` block, or from unittest via
    ``stack = contextlib.ExitStack(); self.addCleanup(stack.close);
    stack.enter_context(registry_sandbox())``.
    """
    saved_plugins = dict(ScraperRegistry._plugins)
    saved_discovered = ScraperRegistry._discovered
    ScraperRegistry._reset()
    try:
        for plugin in plugins:
            ScraperRegistry.register(plugin)
        if frozen:
            ScraperRegistry._discovered = True
        yield ScraperRegistry
    finally:
        ScraperRegistry._plugins = saved_plugins
        ScraperRegistry._discovered = saved_discovered


def mock_notifier(has_services: bool = False, delivery_ok: bool = True) -> mock.Mock:
    """An autospec'd Notifier double.

    Args:
        has_services (bool): The value of the ``has_services`` gate. Set explicitly
            because it is an instance attribute (created in ``__init__``), so it is
            absent from the class-level autospec.
        delivery_ok (bool): What ``notify_low_price`` reports back.
    """
    notifier = mock.create_autospec(Notifier, instance=True)
    notifier.has_services = has_services
    notifier.notify_low_price.return_value = delivery_ok
    return notifier


def mock_ui() -> mock.Mock:
    """An autospec'd ExecutionStrategy double (specs every UI-strategy method)."""
    return mock.create_autospec(ExecutionStrategy, instance=True)


def mock_registry() -> mock.Mock:
    """An autospec'd ScraperRegistry double."""
    return mock.create_autospec(ScraperRegistry, instance=True)


def mock_scraper() -> mock.Mock:
    """An autospec'd BaseScraperClient double."""
    return mock.create_autospec(BaseScraperClient, instance=True)


def mock_data_manager() -> mock.Mock:
    """An autospec'd BaseDataManager double."""
    return mock.create_autospec(BaseDataManager, instance=True)


def write_general(cfg_dir, data) -> None:
    """Writes ``data`` as JSON to ``general.json`` inside ``cfg_dir``."""
    with open(general_config_path(str(cfg_dir)), "w") as f:
        json.dump(data, f)


def read_general(cfg_dir):
    """Reads and returns the parsed ``general.json`` inside ``cfg_dir``."""
    with open(general_config_path(str(cfg_dir))) as f:
        return json.load(f)
