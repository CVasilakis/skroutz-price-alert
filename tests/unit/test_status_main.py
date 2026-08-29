from types import SimpleNamespace
from unittest import mock

import core.status
from core.infrastructure.updates import SoftwareVersionStatus
from core.settings import SettingStatus


# Patched as a decorator rather than inside the `with` block below: CPython caps a
# function at 20 statically nested blocks, and the context-manager group is already
# at that limit.
@mock.patch("core.status.inspect_user_lingering", return_value=True)
def test_status_main_renders_installed_missing_and_orphan_panels(_lingering):
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

    with (
        mock.patch("core.status.install_interrupt_handler"),
        mock.patch("core.status.setup_global_logging"),
        mock.patch("core.status.Console", return_value=console),
        mock.patch("core.status.PluginCatalog.discover", return_value=catalog),
        mock.patch("core.status.oncalendar_for", return_value="hourly") as oncalendar,
        mock.patch("core.status.load_target_configs", return_value=(load,)),
        mock.patch("core.status.load_general_config") as load_general,
        mock.patch(
            "core.status.record_general_diagnostic",
            side_effect=lambda general: general,
        ),
        mock.patch(
            "core.status.record_target_load_diagnostic",
            return_value=True,
        ),
        mock.patch("core.status._check_for_updates", return_value=version_status),
        mock.patch("core.status.render_config_panel") as render_config,
        mock.patch("core.status.signal.signal"),
        mock.patch("core.status.get_systemd_properties", side_effect=systemd_properties),
        mock.patch("core.status.read_timer_oncalendar", return_value="daily"),
        mock.patch("core.status.config_view", return_value="config-view"),
        mock.patch("core.status.JsonStateRepository") as state_repository,
        mock.patch("core.status.build_service_panel", return_value=service_panel) as build_service,
        mock.patch(
            "core.status.build_not_installed_panel", return_value=not_installed
        ) as build_missing,
        mock.patch(
            "core.status.get_installed_plugin_units",
            return_value={"alpha": {"timer"}, "orphan": {"service"}},
        ),
        mock.patch("core.status.build_orphan_panel", return_value=orphan_panel) as build_orphan,
    ):
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
