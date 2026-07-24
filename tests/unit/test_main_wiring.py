"""Wiring test for ``main``: the liveness reminder is checked once per invocation and
*before* the scraping orchestrator, so an aborted scrape can never suppress the heartbeat.

Everything main() touches is patched with ``autospec=True``, so this asserts the wiring
(that the reminder is constructed and run, in the right order) *and* that every
constructor/function call still matches the real signatures — a parameter added to
``ScrapingOrchestrator``, ``ReminderService``, ``AppriseNotifier``, ``load_targets`` or
``validate_notification_preflight`` fails here instead of passing silently. It does not test any scraping
behavior.
"""

import sys
import unittest
from unittest import mock

import core.main


class TestMainWiring(unittest.TestCase):
    def test_reminder_runs_once_before_the_orchestrator(self):
        order = []

        with (
            mock.patch.object(sys, "argv", ["main", "--quiet"]),
            mock.patch("core.main.setup_global_logging", autospec=True),
            mock.patch("core.main.PluginCatalog", autospec=True) as Catalog,
            mock.patch("core.main.ClientLoader", autospec=True),
            mock.patch("core.main.load_targets", autospec=True, return_value=[]),
            mock.patch("core.main.load_general_config", autospec=True) as load_general,
            mock.patch(
                "core.main.validate_notification_preflight", autospec=True, return_value=None
            ),
            mock.patch("core.main.AppriseNotifier", autospec=True) as notifier_type,
            mock.patch("core.main.ReminderStateRepository", autospec=True) as StateRepository,
            mock.patch("core.main.ReminderService", autospec=True) as ReminderService,
            mock.patch("core.main.ScrapingOrchestrator", autospec=True) as Orchestrator,
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
                core.main.main()

        self.assertEqual(caught.exception.code, 0)
        reminder.run_once.assert_called_once()
        self.assertEqual(order, ["reminder", "orchestrator"])
        load_general.assert_called_once_with(core.main.CONFIG_DIR)
        self.assertEqual(ReminderService.call_args.args[2], notifier_type.return_value)
        self.assertEqual(ReminderService.call_args.args[1], StateRepository.return_value)
        notifier_type.assert_called_once_with(("json://localhost",))

    def test_reminder_not_run_when_preflight_aborts(self):
        # A fatal preflight (e.g. missing notifications in service mode) exits before the
        # reminder/orchestrator phase, so no heartbeat is attempted on an unusable config.
        failed_load = mock.MagicMock()
        failed_load.target = "skroutz"
        failed_load.settings.__getitem__.return_value = 7
        with (
            mock.patch.object(sys, "argv", ["main", "--quiet"]),
            mock.patch("core.main.setup_global_logging", autospec=True),
            mock.patch("core.main.PluginCatalog", autospec=True) as Catalog,
            mock.patch("core.main.ClientLoader", autospec=True),
            mock.patch(
                "core.main.load_targets",
                autospec=True,
                return_value=[failed_load],
            ),
            mock.patch("core.main.load_general_config", autospec=True),
            mock.patch(
                "core.main.record_target_load_diagnostic",
                autospec=True,
            ) as record_diagnostic,
            mock.patch("core.main.validate_notification_preflight", autospec=True, return_value=3),
            mock.patch("core.main.AppriseNotifier", autospec=True),
            mock.patch("core.main.ReminderStateRepository", autospec=True),
            mock.patch("core.main.ReminderService", autospec=True) as ReminderService,
            mock.patch("core.main.ScrapingOrchestrator", autospec=True) as Orchestrator,
        ):
            Catalog.discover.return_value.targets = ("skroutz",)
            with self.assertRaises(SystemExit) as caught:
                core.main.main()

        self.assertEqual(caught.exception.code, 3)
        record_diagnostic.assert_called_once_with(failed_load)
        ReminderService.return_value.run_once.assert_not_called()
        Orchestrator.assert_not_called()

    def test_interactive_mode_installs_handler_and_uses_interactive_strategy(self):
        with (
            mock.patch.object(sys, "argv", ["main", "--skroutz"]),
            mock.patch("core.main.setup_global_logging"),
            mock.patch("core.main.PluginCatalog") as Catalog,
            mock.patch("core.main.ClientLoader") as ClientLoader,
            mock.patch("core.main.load_targets", return_value=[]) as load_targets,
            mock.patch("core.main.load_general_config") as load_general,
            mock.patch("core.main.install_interrupt_handler") as install_handler,
            mock.patch("core.main.check_for_updates", return_value=False),
            mock.patch("core.main.render_config_panel") as render_config,
            mock.patch("core.main.Console") as Console,
            mock.patch("core.main.signal.signal"),
            mock.patch("core.main.InteractiveRunReporter") as reporter_type,
            mock.patch("core.main.AppriseNotifier"),
            mock.patch("core.main.ReminderStateRepository"),
            mock.patch("core.main.ReminderService"),
            mock.patch("core.main.ScrapingOrchestrator") as Orchestrator,
        ):
            plugin = mock.MagicMock(display_name="Skroutz")
            catalog = Catalog.discover.return_value
            catalog.targets = ("skroutz", "insomnia")
            catalog.get.return_value = plugin
            load_general.return_value.notifications.valid_urls = ("json://localhost",)
            Orchestrator.return_value.run.return_value = 0
            with self.assertRaises(SystemExit) as caught:
                core.main.main()

        self.assertEqual(caught.exception.code, 0)
        load_targets.assert_called_once_with([plugin], core.main.CONFIG_DIR)
        render_config.assert_called_once_with(
            Console.return_value, load_general.return_value, False
        )
        install_handler.assert_called_once()
        Orchestrator.assert_called_once_with(
            [], ClientLoader.return_value, mock.ANY, False, reporter_type.return_value
        )


if __name__ == "__main__":
    unittest.main()
