"""Wiring test for the status entry point.

``main()`` is a composition root: it collects from the catalog, the config loader, the
state repository, systemd, and the update check, then hands the results to the panel
builders. Every one of those collaborators is replaced here, so the test asserts the
composition without discovering plugins, touching the filesystem, or shelling out.

Replacement goes through pytest's ``monkeypatch`` (the suite's usual seam) rather than a
stack of ``mock.patch`` context managers. Beyond matching the surrounding tests, it keeps
the body flat: a nested ``with`` group needs one block per collaborator, and CPython
compiles at most 20 nested blocks per function — a ceiling this test had already reached.
"""

from types import SimpleNamespace
from unittest import mock

import core.status
from core.infrastructure.updates import SoftwareVersionStatus
from core.settings import SettingStatus


def test_status_main_renders_installed_missing_and_orphan_panels(monkeypatch):
    version_status = SoftwareVersionStatus("1.7.0", False)
    console = mock.MagicMock()
    catalog = mock.MagicMock()
    interval_spec = object()
    alpha = SimpleNamespace(
        target="alpha",
        display_name="Alpha",
        config_filename="alpha.json",
        setting=lambda _key: interval_spec,
    )
    beta = SimpleNamespace(
        target="beta",
        display_name="Beta",
        config_filename="beta.json",
        setting=lambda _key: interval_spec,
    )
    load = SimpleNamespace(
        target="alpha",
        count=2,
        faulty_indices=(1,),
        failure=None,
        row_diagnostic=None,
        settings=mock.MagicMock(),
    )
    interval = SimpleNamespace(status=SettingStatus.OK, value="1h")
    resolved = load.settings
    resolved.resolved.return_value = interval
    catalog.targets = ("alpha", "beta")
    catalog.plugins = (alpha, beta)
    catalog.get.side_effect = lambda target: alpha if target == "alpha" else beta

    def systemd_properties(unit, _properties):
        return {"ActiveState": "active"} if unit.startswith("alpha-") else {}

    service_panel = mock.MagicMock()
    orphan_panel = mock.MagicMock()
    not_installed = object()

    # Collaborators the assertions below inspect.
    oncalendar = mock.MagicMock(return_value="hourly")
    load_general = mock.MagicMock()
    render_config = mock.MagicMock()
    state_repository = mock.MagicMock()
    build_service = mock.MagicMock(return_value=service_panel)
    build_missing = mock.MagicMock(return_value=not_installed)
    build_orphan = mock.MagicMock(return_value=orphan_panel)

    monkeypatch.setattr(core.status, "oncalendar_for", oncalendar)
    monkeypatch.setattr(core.status, "load_general_config", load_general)
    monkeypatch.setattr(core.status, "render_config_panel", render_config)
    monkeypatch.setattr(core.status, "JsonStateRepository", state_repository)
    monkeypatch.setattr(core.status, "build_service_panel", build_service)
    monkeypatch.setattr(core.status, "build_not_installed_panel", build_missing)
    monkeypatch.setattr(core.status, "build_orphan_panel", build_orphan)

    # Collaborators that only need to stop doing the real thing.
    monkeypatch.setattr(core.status, "install_interrupt_handler", lambda: None)
    monkeypatch.setattr(core.status, "setup_global_logging", lambda: None)
    monkeypatch.setattr(core.status, "Console", lambda: console)
    monkeypatch.setattr(core.status.PluginCatalog, "discover", lambda: catalog)
    monkeypatch.setattr(core.status, "load_target_configs", lambda *_: (load,))
    monkeypatch.setattr(core.status, "record_general_diagnostic", lambda general: general)
    monkeypatch.setattr(core.status, "record_target_load_diagnostic", lambda _load: True)
    monkeypatch.setattr(core.status, "_check_for_updates", lambda: version_status)
    monkeypatch.setattr(core.status, "inspect_user_lingering", lambda: True)
    monkeypatch.setattr(core.status.signal, "signal", lambda *_: None)
    monkeypatch.setattr(core.status, "get_systemd_properties", systemd_properties)
    monkeypatch.setattr(core.status, "read_timer_oncalendar", lambda _target: "daily")
    monkeypatch.setattr(core.status, "config_view", lambda *_args, **_kwargs: "config-view")
    monkeypatch.setattr(
        core.status,
        "get_installed_plugin_units",
        lambda: {"alpha": {"timer"}, "orphan": {"service"}},
    )

    core.status.main()

    oncalendar.assert_called_once_with("1h")
    state_repository.return_value.load.assert_called_once()
    load_general.assert_called_once_with(core.status.CONFIG_DIR)
    render_config.assert_called_once_with(console, load_general.return_value, version_status, True)
    build_service.assert_called_once()
    service_panel.render.assert_called_once_with(console)
    build_missing.assert_called_once_with("beta", "Beta")
    assert not_installed in [call.args[0] for call in console.print.call_args_list if call.args]
    build_orphan.assert_called_once_with("orphan")
    orphan_panel.render.assert_called_once_with(console)
