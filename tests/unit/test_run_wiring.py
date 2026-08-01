"""Wiring test for ``run``: the liveness reminder is checked once per invocation and
*before* the scraping orchestrator, so an aborted scrape can never suppress the heartbeat.

Everything main() touches is patched with ``autospec=True``, so this asserts the wiring
(that the reminder is constructed and run, in the right order) *and* that every
constructor/function call still matches the real signatures — a parameter added to
``ScrapingOrchestrator``, ``ReminderService``, ``AppriseNotifier``, ``load_target_configs`` or
``validate_notification_preflight`` fails here instead of passing silently. It does not test any scraping
behavior.
"""

import sys
import unittest
from unittest import mock

import core.run
from core.infrastructure.updates import SoftwareVersionStatus


class TestRunWiring(unittest.TestCase):
    def test_reminder_runs_once_before_the_orchestrator(self):
        order = []

        with (
            mock.patch.object(sys, "argv", ["run", "--quiet"]),
            mock.patch("core.run.setup_global_logging", autospec=True),
            mock.patch("core.run.PluginCatalog", autospec=True) as Catalog,
            mock.patch("core.run.ClientLoader", autospec=True),
            mock.patch("core.run.load_target_configs", autospec=True, return_value=[]),
            mock.patch("core.run.load_general_config", autospec=True) as load_general,
            mock.patch(
                "core.run.record_general_diagnostic",
                autospec=True,
                side_effect=lambda general: general,
            ),
            mock.patch(
                "core.run.validate_notification_preflight", autospec=True, return_value=None
            ),
            mock.patch("core.run.AppriseNotifier", autospec=True) as notifier_type,
            mock.patch("core.run.ReminderStateRepository", autospec=True) as StateRepository,
            mock.patch("core.run.StateLockManager", autospec=True) as LockManager,
            mock.patch("core.run.ReminderService", autospec=True) as ReminderService,
            mock.patch("core.run.ScrapingOrchestrator", autospec=True) as Orchestrator,
        ):
            catalog = Catalog.discover.return_value
            catalog.targets = ("skroutz",)
            general = load_general.return_value
            general.notifications.valid_urls = ("json://localhost",)
            general.settings_error = None
            reminder = ReminderService.return_value
            reminder.run_once.side_effect = lambda: order.append("reminder")
            orchestrator = Orchestrator.return_value
            orchestrator.run.side_effect = lambda: (order.append("orchestrator"), 0)[1]
            with self.assertRaises(SystemExit) as caught:
                core.run.main()

        self.assertEqual(caught.exception.code, 0)
        reminder.run_once.assert_called_once()
        self.assertEqual(order, ["reminder", "orchestrator"])
        load_general.assert_called_once_with(core.run.CONFIG_DIR)
        self.assertEqual(ReminderService.call_args.args[2], notifier_type.return_value)
        self.assertEqual(ReminderService.call_args.args[1], StateRepository.return_value)
        LockManager.assert_called_once_with(core.run.STATE_DIR)
        self.assertIs(
            ReminderService.call_args.kwargs["acquire_lock_fn"],
            LockManager.return_value.acquire,
        )
        notifier_type.assert_called_once_with(("json://localhost",))

    def test_reminder_not_run_when_preflight_aborts(self):
        # A fatal preflight (e.g. missing notifications in service mode) exits before the
        # reminder/orchestrator phase, so no heartbeat is attempted on an unusable config.
        failed_load = mock.MagicMock()
        failed_load.target = "skroutz"
        failed_load.settings.__getitem__.return_value = 7
        with (
            mock.patch.object(sys, "argv", ["run", "--quiet"]),
            mock.patch("core.run.setup_global_logging", autospec=True),
            mock.patch("core.run.PluginCatalog", autospec=True) as Catalog,
            mock.patch("core.run.ClientLoader", autospec=True),
            mock.patch(
                "core.run.load_target_configs",
                autospec=True,
                return_value=[failed_load],
            ),
            mock.patch("core.run.load_general_config", autospec=True),
            mock.patch(
                "core.run.record_general_diagnostic",
                autospec=True,
                side_effect=lambda general: general,
            ),
            mock.patch(
                "core.run.record_target_load_diagnostic",
                autospec=True,
            ) as record_diagnostic,
            mock.patch("core.run.validate_notification_preflight", autospec=True, return_value=3),
            mock.patch("core.run.AppriseNotifier", autospec=True),
            mock.patch("core.run.ReminderStateRepository", autospec=True),
            mock.patch("core.run.ReminderService", autospec=True) as ReminderService,
            mock.patch("core.run.ScrapingOrchestrator", autospec=True) as Orchestrator,
        ):
            Catalog.discover.return_value.targets = ("skroutz",)
            with self.assertRaises(SystemExit) as caught:
                core.run.main()

        self.assertEqual(caught.exception.code, 3)
        record_diagnostic.assert_called_once_with(failed_load)
        ReminderService.return_value.run_once.assert_not_called()
        Orchestrator.assert_not_called()

    def test_interactive_mode_installs_handler_and_uses_interactive_strategy(self):
        version_status = SoftwareVersionStatus("1.7.0", False)
        with (
            mock.patch.object(sys, "argv", ["run", "--skroutz"]),
            mock.patch("core.run.setup_global_logging"),
            mock.patch("core.run.PluginCatalog") as Catalog,
            mock.patch("core.run.ClientLoader") as ClientLoader,
            mock.patch("core.run.load_target_configs", return_value=[]) as load_target_configs,
            mock.patch("core.run.load_general_config") as load_general,
            mock.patch(
                "core.run.record_general_diagnostic",
                side_effect=lambda general: general,
            ),
            mock.patch("core.run.install_interrupt_handler") as install_handler,
            mock.patch("core.run.inspect_software_version", return_value=version_status),
            mock.patch("core.run.render_config_panel") as render_config,
            mock.patch("core.run.Console") as Console,
            mock.patch("core.run.signal.signal"),
            mock.patch("core.run.InteractiveRunReporter") as reporter_type,
            mock.patch("core.run.AppriseNotifier"),
            mock.patch("core.run.ReminderStateRepository"),
            mock.patch("core.run.ReminderService"),
            mock.patch("core.run.ScrapingOrchestrator") as Orchestrator,
        ):
            plugin = mock.MagicMock(display_name="Skroutz")
            catalog = Catalog.discover.return_value
            catalog.targets = ("skroutz", "insomnia")
            catalog.get.return_value = plugin
            load_general.return_value.notifications.valid_urls = ("json://localhost",)
            Orchestrator.return_value.run.return_value = 0
            with self.assertRaises(SystemExit) as caught:
                core.run.main()

        self.assertEqual(caught.exception.code, 0)
        load_target_configs.assert_called_once_with([plugin], core.run.CONFIG_DIR)
        render_config.assert_called_once_with(
            Console.return_value, load_general.return_value, version_status
        )
        install_handler.assert_called_once()
        Orchestrator.assert_called_once_with(
            [],
            ClientLoader.return_value,
            mock.ANY,
            False,
            reporter_type.return_value,
            state_dir=core.run.STATE_DIR,
        )


if __name__ == "__main__":
    unittest.main()
