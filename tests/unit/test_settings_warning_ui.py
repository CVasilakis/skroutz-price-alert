"""Unknown-setting warning presentation and quiet-log behavior."""

import logging
import unittest
from unittest import mock

from core.ui.tui import InteractiveExecutionStrategy, SilentExecutionStrategy


WARNING = "Unknown setting key(s) ignored: future_option, typo_key"


class TestUnknownSettingsWarning(unittest.TestCase):
    def test_interactive_adds_yellow_settings_row_and_footnote(self):
        strategy = InteractiveExecutionStrategy()
        strategy.notes = []
        rows = strategy._build_settings_rows((), settings_warning=WARNING)
        self.assertEqual(rows[0][:2], ("🟡", "Settings / Unknown keys ignored"))
        self.assertIn("[1]", rows[0][2])
        self.assertEqual(strategy.notes, [WARNING + "."])

    def test_silent_logs_same_warning_once_at_target_start(self):
        strategy = SilentExecutionStrategy()
        logger = mock.create_autospec(logging.Logger, instance=True)
        strategy.start_target("store", logger, settings_warning=WARNING)
        logger.warning.assert_called_once_with(
            f"❗ Settings / Unknown keys ignored: {WARNING}"
        )


if __name__ == "__main__":
    unittest.main()
