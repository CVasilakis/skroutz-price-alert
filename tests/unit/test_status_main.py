"""Wiring test for the status entry point.

``main()`` is a composition root: it parses the dynamic ``--<target>`` flags, then
collects from the catalog, the config loader, the state repository, systemd, and the
update check before handing the results to the panel builders. Every one of those
collaborators is replaced here, so the tests assert the composition without discovering
plugins, touching the filesystem, or shelling out.

Replacement goes through pytest's ``monkeypatch`` (the suite's usual seam) rather than a
stack of ``mock.patch`` context managers. Beyond matching the surrounding tests, it keeps
each body flat: a nested ``with`` group needs one block per collaborator, and CPython
compiles at most 20 nested blocks per function — a ceiling this test had already reached.
``sys.argv`` is part of that replacement, because the parser reads the real one.
"""

from types import SimpleNamespace
from unittest import mock

import pytest

import core.status
from core.infrastructure.updates import SoftwareVersionStatus
from core.settings import SettingStatus

VERSION_STATUS = SoftwareVersionStatus("1.7.0", False)
INTERVAL_SPEC = object()


def _plugin(target: str, display_name: str) -> SimpleNamespace:
    return SimpleNamespace(
        target=target,
        display_name=display_name,
        config_filename=f"{target}.json",
        setting=lambda _key: INTERVAL_SPEC,
    )


def _load(target: str) -> SimpleNamespace:
    settings = mock.MagicMock()
    settings.resolved.return_value = SimpleNamespace(status=SettingStatus.OK, value="1h")
    return SimpleNamespace(
        target=target,
        count=2,
        faulty_indices=(1,),
        failure=None,
        row_diagnostic=None,
        settings=settings,
    )


def _wire(monkeypatch, argv, *, loads=("alpha",), installed=("alpha",), units=None):
    """Replace every collaborator of ``main()`` and return the inspectable spies."""
    alpha = _plugin("alpha", "Alpha")
    beta = _plugin("beta", "Beta")
    plugins = {"alpha": alpha, "beta": beta}
    catalog = mock.MagicMock()
    catalog.targets = ("alpha", "beta")
    catalog.plugins = (alpha, beta)
    catalog.get.side_effect = plugins.__getitem__

    installed_targets = set(installed)

    def systemd_properties(unit: str, _properties: str):
        return {"ActiveState": "active"} if unit.split("-", 1)[0] in installed_targets else {}

    spy = SimpleNamespace(
        console=mock.MagicMock(),
        catalog=catalog,
        alpha=alpha,
        beta=beta,
        service_panel=mock.MagicMock(),
        orphan_panel=mock.MagicMock(),
        not_installed=object(),
        oncalendar=mock.MagicMock(return_value="hourly"),
        load_general=mock.MagicMock(),
        render_config=mock.MagicMock(),
        state_repository=mock.MagicMock(),
        load_target_configs=mock.MagicMock(return_value=tuple(_load(target) for target in loads)),
    )
    spy.build_service = mock.MagicMock(return_value=spy.service_panel)
    spy.build_missing = mock.MagicMock(return_value=spy.not_installed)
    spy.build_orphan = mock.MagicMock(return_value=spy.orphan_panel)

    monkeypatch.setattr(core.status.sys, "argv", list(argv))

    # Collaborators the assertions inspect.
    monkeypatch.setattr(core.status, "oncalendar_for", spy.oncalendar)
    monkeypatch.setattr(core.status, "load_general_config", spy.load_general)
    monkeypatch.setattr(core.status, "render_config_panel", spy.render_config)
    monkeypatch.setattr(core.status, "JsonStateRepository", spy.state_repository)
    monkeypatch.setattr(core.status, "load_target_configs", spy.load_target_configs)
    monkeypatch.setattr(core.status, "build_service_panel", spy.build_service)
    monkeypatch.setattr(core.status, "build_not_installed_panel", spy.build_missing)
    monkeypatch.setattr(core.status, "build_orphan_panel", spy.build_orphan)

    # Collaborators that only need to stop doing the real thing.
    monkeypatch.setattr(core.status, "install_interrupt_handler", lambda: None)
    monkeypatch.setattr(core.status, "setup_global_logging", lambda: None)
    monkeypatch.setattr(core.status, "Console", lambda: spy.console)
    monkeypatch.setattr(core.status.PluginCatalog, "discover", lambda: catalog)
    monkeypatch.setattr(core.status, "record_general_diagnostic", lambda general: general)
    monkeypatch.setattr(core.status, "record_target_load_diagnostic", lambda _load: True)
    monkeypatch.setattr(core.status, "_check_for_updates", lambda: VERSION_STATUS)
    monkeypatch.setattr(core.status, "inspect_user_lingering", lambda: True)
    monkeypatch.setattr(core.status.signal, "signal", lambda *_: None)
    monkeypatch.setattr(core.status, "get_systemd_properties", systemd_properties)
    monkeypatch.setattr(core.status, "read_timer_oncalendar", lambda _target: "daily")
    monkeypatch.setattr(core.status, "config_view", lambda *_args, **_kwargs: "config-view")
    monkeypatch.setattr(
        core.status,
        "get_installed_plugin_units",
        lambda: dict(units if units is not None else {"alpha": {"timer"}, "orphan": {"service"}}),
    )
    return spy


def test_status_main_renders_installed_missing_and_orphan_panels(monkeypatch):
    spy = _wire(monkeypatch, ["status"])

    core.status.main()

    spy.oncalendar.assert_called_once_with("1h")
    spy.state_repository.return_value.load.assert_called_once()
    spy.load_general.assert_called_once_with(core.status.CONFIG_DIR)
    spy.render_config.assert_called_once_with(
        spy.console, spy.load_general.return_value, VERSION_STATUS, True
    )
    spy.load_target_configs.assert_called_once_with([spy.alpha, spy.beta], core.status.CONFIG_DIR)
    spy.build_service.assert_called_once()
    spy.service_panel.render.assert_called_once_with(spy.console)
    spy.build_missing.assert_called_once_with("beta", "Beta")
    printed = [call.args[0] for call in spy.console.print.call_args_list if call.args]
    assert spy.not_installed in printed
    spy.build_orphan.assert_called_once_with("orphan")
    spy.orphan_panel.render.assert_called_once_with(spy.console)


def test_a_target_flag_reports_only_that_scraper(monkeypatch):
    spy = _wire(
        monkeypatch,
        ["status", "--beta"],
        loads=("beta",),
        installed=("alpha", "beta"),
    )

    core.status.main()

    # Only the selected target's config is read, and the orphan stays out of the report.
    spy.load_target_configs.assert_called_once_with([spy.beta], core.status.CONFIG_DIR)
    spy.build_service.assert_called_once()
    assert spy.build_service.call_args.args[0] == "beta"
    spy.build_missing.assert_not_called()
    spy.build_orphan.assert_not_called()
    # The target-neutral panel is not part of the narrowing.
    spy.render_config.assert_called_once()


def test_an_orphan_flag_reports_only_that_orphan(monkeypatch):
    spy = _wire(monkeypatch, ["status", "--orphan"], loads=())

    core.status.main()

    spy.load_target_configs.assert_called_once_with([], core.status.CONFIG_DIR)
    spy.build_service.assert_not_called()
    spy.build_missing.assert_not_called()
    spy.build_orphan.assert_called_once_with("orphan")


def test_an_unselectable_orphan_name_is_still_reported(monkeypatch):
    # Unit filenames on disk are arbitrary: a name that cannot become a flag must
    # keep its panel in the unfiltered report rather than vanish from it.
    units = {"alpha": {"timer"}, "not-a-target": {"service"}}
    spy = _wire(monkeypatch, ["status"], units=units)

    core.status.main()

    spy.build_orphan.assert_called_once_with("not-a-target")


def test_an_unknown_target_flag_is_rejected(monkeypatch):
    _wire(monkeypatch, ["status", "--nope"])

    with pytest.raises(SystemExit) as exit_info:
        core.status.main()

    assert exit_info.value.code == 2
