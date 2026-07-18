from types import SimpleNamespace
from unittest import mock

import core.status
from core.settings import SettingStatus


def test_status_main_renders_installed_missing_and_orphan_panels():
    console = mock.MagicMock()
    registry = mock.MagicMock()
    interval_spec = object()
    alpha = SimpleNamespace(
        display_name="Alpha",
        config_filename="alpha.json",
        setting=lambda key: interval_spec,
    )
    beta = SimpleNamespace(
        display_name="Beta",
        config_filename="beta.json",
        setting=lambda key: interval_spec,
    )
    load = SimpleNamespace(
        target="alpha", count=2, faulty_indices=(1,), error=None
    )
    interval = SimpleNamespace(status=SettingStatus.OK)
    resolved = mock.MagicMock()
    resolved.resolved.return_value = interval
    registry.settings_for.return_value = resolved

    registry_type = mock.MagicMock(return_value=registry)
    registry_type.registered_targets.return_value = ("alpha", "beta")
    registry_type.get_plugin.side_effect = lambda target: alpha if target == "alpha" else beta
    registry_type.expected_on_calendar.return_value = "hourly"

    def systemd_properties(unit, _properties):
        return {"ActiveState": "active"} if unit.startswith("alpha-") else {}

    service_panel = mock.MagicMock()
    orphan_panel = mock.MagicMock()
    not_installed = object()

    with mock.patch("core.status.install_interrupt_handler"), \
         mock.patch("core.status.setup_global_logging"), \
         mock.patch("core.status.Console", return_value=console), \
         mock.patch("core.status.ScraperRegistry", registry_type), \
         mock.patch("core.status.load_targets", return_value=(load,)), \
         mock.patch("core.status.render_config_panel"), \
         mock.patch("core.status.signal.signal"), \
         mock.patch("core.status.get_systemd_properties",
                    side_effect=systemd_properties), \
         mock.patch("core.status.read_timer_oncalendar", return_value="daily"), \
         mock.patch("core.status.config_view", return_value="config-view"), \
         mock.patch("core.status.build_service_panel", return_value=service_panel) as build_service, \
         mock.patch("core.status.build_not_installed_panel",
                    return_value=not_installed) as build_missing, \
         mock.patch("core.status.get_installed_plugin_units",
                    return_value={"alpha": {"timer"}, "orphan": {"service"}}), \
         mock.patch("core.status.build_orphan_panel",
                    return_value=orphan_panel) as build_orphan:
        core.status.main()

    registry_type.expected_on_calendar.assert_called_once_with(alpha, interval)
    build_service.assert_called_once()
    service_panel.render.assert_called_once_with(console)
    build_missing.assert_called_once_with("beta", "Beta")
    assert not_installed in [call.args[0] for call in console.print.call_args_list if call.args]
    build_orphan.assert_called_once_with("orphan")
    orphan_panel.render.assert_called_once_with(console)
