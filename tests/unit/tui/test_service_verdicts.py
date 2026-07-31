"""Unit tests for the exit-code -> presentation verdict table.

The UI snapshot suite pins how each verdict *renders*; this pins the verdict
*decision* itself (``classify_service_state``), so a regression in the table
lookup, the success rule, or the ``{detail}`` substitution cannot hide behind a
blindly regenerated snapshot.
"""

import unittest

from core.exit_status import ExitStatus
from core.tui.service_verdicts import classify_service_state


def _systemd_status(status: ExitStatus) -> str:
    """Serialize an exit status as systemd exposes ``ExecMainStatus``."""
    return str(int(status))


class TestClassifyServiceState(unittest.TestCase):
    def _classify(
        self, result="exit-code", exec_status="", target="skroutz", config="skroutz.json"
    ):
        return classify_service_state(result, exec_status, target, config)

    def test_success_requires_result_and_zero_exit(self):
        verdict = self._classify(result="success", exec_status=_systemd_status(ExitStatus.SUCCESS))
        self.assertEqual(
            (verdict.icon, verdict.label, verdict.color, verdict.note), ("✅", "OK", "green", None)
        )

    def test_zero_exit_without_success_result_is_not_ok(self):
        # systemd may report Result != success with ExecMainStatus 0 (e.g. a
        # signal kill); that must fall through to the unknown-failure verdict.
        verdict = self._classify(result="signal", exec_status="0")
        self.assertEqual(verdict.label, "Failed")
        self.assertEqual(verdict.note, "Reason: signal, Exit Code: 0")

    def test_target_config_error_fills_in_the_config_filename(self):
        verdict = self._classify(
            exec_status=_systemd_status(ExitStatus.TARGET_CONFIG_ERROR),
            config="custom-name.json",
        )
        self.assertEqual((verdict.icon, verdict.label, verdict.color), ("❗", "Failed", "red"))
        self.assertEqual(verdict.note, "Issue with the `config/custom-name.json` file.")

    def test_known_codes_map_to_their_verdicts(self):
        for code, icon, label in [
            (ExitStatus.APPLICATION_ERROR, "❗", "Application Failed"),
            (ExitStatus.NOTIFICATION_CONFIG_ERROR, "❗", "Failed"),
            (ExitStatus.RATE_LIMIT_ERROR, "❗", "Failed"),
            (ExitStatus.SCRAPE_ERROR, "❗", "Scraping Failed"),
            (ExitStatus.STORAGE_ERROR, "❗", "Storage Failed"),
            (ExitStatus.NOTIFICATION_ERROR, "🟡", "Notification Warning"),
            (ExitStatus.PLUGIN_DEPENDENCY_ERROR, "❗", "Dependencies Missing"),
            (ExitStatus.ALREADY_RUNNING, "🟡", "Skipped"),
            (ExitStatus.INTERRUPTED, "🟡", "Interrupted"),
        ]:
            with self.subTest(code=code):
                verdict = self._classify(exec_status=_systemd_status(code))
                self.assertEqual((verdict.icon, verdict.label), (icon, label))
                self.assertIsNotNone(verdict.note)

    def test_every_declared_exit_status_has_a_specific_verdict(self):
        for status in ExitStatus:
            with self.subTest(status=status):
                result = "success" if status is ExitStatus.SUCCESS else "exit-code"
                verdict = self._classify(result=result, exec_status=_systemd_status(status))
                self.assertNotIn("Reason:", verdict.note or "")

    def test_new_codes_interpolate_target_and_config(self):
        scrape = self._classify(
            exec_status=_systemd_status(ExitStatus.SCRAPE_ERROR), target="insomnia"
        )
        assert scrape.note is not None
        self.assertIn("logs/insomnia/output.log", scrape.note)

        storage = self._classify(
            exec_status=_systemd_status(ExitStatus.STORAGE_ERROR), target="insomnia"
        )
        assert storage.note is not None
        self.assertIn("state/insomnia.json", storage.note)
        self.assertIn("state/locks/insomnia.lock", storage.note)

        dependency = self._classify(
            exec_status=_systemd_status(ExitStatus.PLUGIN_DEPENDENCY_ERROR),
            target="insomnia",
        )
        assert dependency.note is not None
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
