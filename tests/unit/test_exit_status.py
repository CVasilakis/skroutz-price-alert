"""Unit tests for the exit-code -> presentation verdict table.

The UI snapshot suite pins how each verdict *renders*; this pins the verdict
*decision* itself (``classify_service_state``), so a regression in the table
lookup, the success rule, or the ``{detail}`` substitution cannot hide behind a
blindly regenerated snapshot.
"""

import unittest

from core.constants import (
    EXIT_CODE_SUCCESS, EXIT_CODE_SKIPPED, EXIT_CODE_PRODUCTS_ERROR,
    EXIT_CODE_ENV_ERROR, EXIT_CODE_RATE_LIMIT_ERROR, EXIT_CODE_INTERRUPT,
    EXIT_CODE_SCRAPE_ERROR, EXIT_CODE_STORAGE_ERROR,
    EXIT_CODE_NOTIFICATION_ERROR, EXIT_CODE_PLUGIN_DEPENDENCY_ERROR,
)
from core.exit_status import classify_service_state


class TestClassifyServiceState(unittest.TestCase):
    def _classify(self, result="exit-code", exec_status="", target="skroutz",
                  config="skroutz.json"):
        return classify_service_state(result, exec_status, target, config)

    def test_success_requires_result_and_zero_exit(self):
        verdict = self._classify(result="success", exec_status=str(EXIT_CODE_SUCCESS))
        self.assertEqual((verdict.icon, verdict.label, verdict.color, verdict.note),
                         ("✅", "OK", "green", None))

    def test_zero_exit_without_success_result_is_not_ok(self):
        # systemd may report Result != success with ExecMainStatus 0 (e.g. a
        # signal kill); that must fall through to the unknown-failure verdict.
        verdict = self._classify(result="signal", exec_status="0")
        self.assertEqual(verdict.label, "Failed")
        self.assertEqual(verdict.note, "Reason: signal, Exit Code: 0")

    def test_products_error_fills_in_the_config_filename(self):
        verdict = self._classify(exec_status=str(EXIT_CODE_PRODUCTS_ERROR),
                                 config="custom-name.json")
        self.assertEqual((verdict.icon, verdict.label, verdict.color),
                         ("❗", "Failed", "red"))
        self.assertEqual(verdict.note, "Issue with the `config/custom-name.json` file.")

    def test_known_codes_map_to_their_verdicts(self):
        for code, icon, label in [
            (EXIT_CODE_ENV_ERROR, "❗", "Failed"),
            (EXIT_CODE_RATE_LIMIT_ERROR, "❗", "Failed"),
            (EXIT_CODE_SCRAPE_ERROR, "❗", "Scraping Failed"),
            (EXIT_CODE_STORAGE_ERROR, "❗", "Storage Failed"),
            (EXIT_CODE_NOTIFICATION_ERROR, "🟡", "Notification Warning"),
            (EXIT_CODE_PLUGIN_DEPENDENCY_ERROR, "❗", "Dependencies Missing"),
            (EXIT_CODE_SKIPPED, "🟡", "Skipped"),
            (EXIT_CODE_INTERRUPT, "🟡", "Interrupted"),
        ]:
            with self.subTest(code=code):
                verdict = self._classify(exec_status=str(code))
                self.assertEqual((verdict.icon, verdict.label), (icon, label))
                self.assertIsNotNone(verdict.note)

    def test_new_codes_interpolate_target_and_config(self):
        scrape = self._classify(exec_status=str(EXIT_CODE_SCRAPE_ERROR), target="insomnia")
        self.assertIn("logs/insomnia/output.log", scrape.note)

        storage = self._classify(exec_status=str(EXIT_CODE_STORAGE_ERROR),
                                 config="custom.json")
        self.assertIn("config/custom.json", storage.note)

        dependency = self._classify(
            exec_status=str(EXIT_CODE_PLUGIN_DEPENDENCY_ERROR), target="insomnia",
        )
        self.assertIn("./install.sh --insomnia", dependency.note)

    def test_unknown_code_carries_the_raw_reason_and_code(self):
        verdict = self._classify(result="core-dump", exec_status="99")
        self.assertEqual(verdict.label, "Failed")
        self.assertEqual(verdict.note, "Reason: core-dump, Exit Code: 99")

    def test_non_integer_exit_status_falls_back_gracefully(self):
        verdict = self._classify(result="exit-code", exec_status="not-a-number")
        self.assertEqual(verdict.label, "Failed")
        self.assertEqual(verdict.note, "Reason: exit-code, Exit Code: not-a-number")

    def test_empty_fields_read_as_unknown(self):
        verdict = self._classify(result="", exec_status="")
        self.assertEqual(verdict.note, "Reason: Unknown, Exit Code: Unknown")


if __name__ == "__main__":
    unittest.main()
