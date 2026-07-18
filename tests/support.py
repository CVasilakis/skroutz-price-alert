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
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from unittest import mock

from core.general import general_config_path
from core.notifier import Notifier
from core.scrapers.base.client import BaseScraperClient
from core.scrapers.base.plugin import ClassRef, PluginDefinition, RegisteredPlugin
from core.scrapers.base.storage import BaseDataManager
from core.scrapers.registry import ScraperRegistry
from core.ui.tui import ExecutionStrategy


@dataclass(frozen=True)
class PluginFixture:
    target: str
    definition: PluginDefinition

    @property
    def default_interval(self):
        return self.definition.default_interval

def _class_ref(bound_class, fallback: str) -> ClassRef:
    if bound_class is None:
        return ClassRef("support", fallback)
    module = sys.modules[bound_class.__module__]
    setattr(module, bound_class.__name__, bound_class)
    return ClassRef(bound_class.__module__, bound_class.__name__)


def fake_plugin(name="fakestore", domains=("fake-store.example",),
                display_name="Fake Store", specs=None,
                client_class=None, storage_class=None, default_interval="1h") -> PluginFixture:
    """Build an import-light plugin fixture for tests."""
    definition = PluginDefinition(
        display_name=display_name,
        domains=tuple(domains),
        client=_class_ref(client_class, "MissingFakeClient"),
        storage=_class_ref(storage_class, "MissingFakeStorage"),
        default_interval=default_interval,
        setting_specs=tuple(specs or ()),
    )
    return PluginFixture(name, definition)


@contextlib.contextmanager
def registry_sandbox(*plugins: PluginFixture | RegisteredPlugin, frozen: bool = True):
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
            if isinstance(plugin, PluginFixture):
                ScraperRegistry.register(plugin.definition, target=plugin.target)
            else:
                ScraperRegistry._plugins[plugin.target] = plugin
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


def write_settings_config(case, settings, filename="x.json", products=()) -> str:
    """Writes a temp config file with the given ``settings`` block; returns its path.

    The shared fixture behind the settings suites (resolve/view/integration), so the
    "fresh temp dir + one JSON config" idiom lives in one place *and* is cleaned up:
    the temp dir is registered on ``case`` (a ``TestCase``) via ``addCleanup``.

    Args:
        case: The owning TestCase (its addCleanup removes the temp dir).
        settings: The raw ``settings`` block (any JSON-serializable value, so
            malformed-block tests can pass a string or list).
        filename: The config filename — pass the plugin's real name (e.g.
            ``skroutz.json``) when the path is resolved through the registry.
        products: The ``products`` list to include.

    Returns:
        str: The absolute path of the written config file.
    """
    cfg_dir = tempfile.mkdtemp()
    case.addCleanup(shutil.rmtree, cfg_dir, ignore_errors=True)
    path = os.path.join(cfg_dir, filename)
    with open(path, "w") as f:
        json.dump({"settings": settings, "products": list(products)}, f)
    return path


def write_general(cfg_dir, data) -> None:
    """Writes ``data`` as JSON to ``general.json`` inside ``cfg_dir``."""
    with open(general_config_path(str(cfg_dir)), "w") as f:
        json.dump(data, f)


def read_general(cfg_dir):
    """Reads and returns the parsed ``general.json`` inside ``cfg_dir``."""
    with open(general_config_path(str(cfg_dir))) as f:
        return json.load(f)
