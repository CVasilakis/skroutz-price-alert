"""Unit tests for signal helpers."""

import signal
import unittest

from core.infrastructure.signals import describe_signal


class TestDescribeSignal(unittest.TestCase):
    def test_known_signals(self):
        self.assertEqual(describe_signal(signal.SIGINT), "SIGINT (Ctrl+C)")
        self.assertEqual(describe_signal(signal.SIGTERM), "SIGTERM (System Shutdown/Termination)")

    def test_unknown_signal_is_the_raw_number(self):
        self.assertEqual(describe_signal(99), "99")


if __name__ == "__main__":
    unittest.main()
