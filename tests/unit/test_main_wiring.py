"""Wiring test for ``main``: the liveness reminder is checked once per invocation and
*before* the scraping orchestrator, so an aborted scrape can never suppress the heartbeat.

Everything main() touches is patched to a mock, so this asserts only the wiring (that the
reminder is constructed and run, in the right order) - not any scraping behavior.
"""

import sys
import unittest
from unittest import mock

import core.main


class TestMainWiring(unittest.TestCase):
    def test_reminder_runs_once_before_the_orchestrator(self):
        order = []
        reminder = mock.Mock()
        reminder.run_once.side_effect = lambda: order.append("reminder")
        orchestrator = mock.Mock()
        orchestrator.run.side_effect = lambda: (order.append("orchestrator"), 0)[1]

        with mock.patch.object(sys, "argv", ["main", "--quiet"]), \
             mock.patch("core.main.setup_global_logging"), \
             mock.patch("core.main.ScraperRegistry") as Registry, \
             mock.patch("core.main.load_targets", return_value=[]), \
             mock.patch("core.main.preflight", return_value=None), \
             mock.patch("core.main.Notifier") as Notifier, \
             mock.patch("core.main.ReminderService", return_value=reminder) as ReminderService, \
             mock.patch("core.main.ScrapingOrchestrator", return_value=orchestrator):
            Registry.registered_targets.return_value = ["skroutz"]
            with self.assertRaises(SystemExit) as caught:
                core.main.main()

        self.assertEqual(caught.exception.code, 0)
        reminder.run_once.assert_called_once()
        self.assertEqual(order, ["reminder", "orchestrator"])
        # Constructed with the config dir and the shared notifier instance.
        self.assertEqual(ReminderService.call_args.args[1], Notifier.return_value)

    def test_reminder_not_run_when_preflight_aborts(self):
        # A fatal preflight (e.g. a missing .env in service mode) exits before the
        # reminder/orchestrator phase, so no heartbeat is attempted on an unusable config.
        reminder = mock.Mock()
        with mock.patch.object(sys, "argv", ["main", "--quiet"]), \
             mock.patch("core.main.setup_global_logging"), \
             mock.patch("core.main.ScraperRegistry") as Registry, \
             mock.patch("core.main.load_targets", return_value=[]), \
             mock.patch("core.main.preflight", return_value=3), \
             mock.patch("core.main.Notifier"), \
             mock.patch("core.main.ReminderService", return_value=reminder), \
             mock.patch("core.main.ScrapingOrchestrator") as Orchestrator:
            Registry.registered_targets.return_value = ["skroutz"]
            with self.assertRaises(SystemExit) as caught:
                core.main.main()

        self.assertEqual(caught.exception.code, 3)
        reminder.run_once.assert_not_called()
        Orchestrator.assert_not_called()


if __name__ == "__main__":
    unittest.main()
