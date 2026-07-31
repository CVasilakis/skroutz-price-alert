"""Unit tests for the target logger factory and the traceback writer.

``LOGS_DIR`` is redirected per-test by the autouse ``_isolate_runtime_paths``
fixture, so everything here writes to a temp dir. Logger objects are
process-global by name, so each test uses a unique target name (and closes the
handlers it created) to stay independent of test order.
"""

import itertools
import logging
import unittest
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from unittest import mock

import core.infrastructure.logging
from core.infrastructure.logging import (
    NonEmptyFilter,
    get_target_logger,
    save_diagnostic,
    save_traceback,
    try_save_diagnostic,
)

_ids = itertools.count()


class _LoggerCase(unittest.TestCase):
    def _target(self):
        """A unique target name per test (loggers are cached process-wide by name)."""
        name = f"fake_target_{next(_ids)}"
        self.addCleanup(self._close_handlers, name)
        return name

    @staticmethod
    def _close_handlers(target):
        logger = logging.getLogger(f"scraper.{target}")
        for handler in logger.handlers[:]:
            handler.close()
            logger.removeHandler(handler)


class TestGetTargetLogger(_LoggerCase):
    def test_quiet_mode_logs_to_a_rotating_file_with_the_given_retention(self):
        target = self._target()
        logger = get_target_logger(target, quiet=True, retention_days=12)

        self.assertFalse(logger.propagate)  # nothing may leak to the console
        (handler,) = logger.handlers
        self.assertIsInstance(handler, TimedRotatingFileHandler)
        # The configured log_retention_days becomes the handler's backupCount.
        self.assertEqual(handler.backupCount, 12)
        self.assertTrue(handler.utc)  # rollover boundary matches the UTC timestamps
        self.assertEqual(
            Path(handler.baseFilename),
            Path(core.infrastructure.logging.LOGS_DIR) / target / "output.log",
        )

    def test_quiet_log_lines_are_utc_labelled(self):
        target = self._target()
        logger = get_target_logger(target, quiet=True)
        logger.info("hello")
        content = (Path(core.infrastructure.logging.LOGS_DIR) / target / "output.log").read_text()
        self.assertRegex(content, r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} UTC\] hello$")

    def test_interactive_mode_adds_no_handler_and_propagates(self):
        target = self._target()
        logger = get_target_logger(target, quiet=False)
        self.assertTrue(logger.propagate)  # terminal output goes via the root logger
        self.assertEqual(logger.handlers, [])

    def test_second_call_reuses_the_logger_without_duplicating_handlers(self):
        target = self._target()
        first = get_target_logger(target, quiet=True)
        second = get_target_logger(target, quiet=True)
        self.assertIs(first, second)
        self.assertEqual(len(second.handlers), 1)  # no duplicate log lines

    def test_second_call_updates_retention_and_mode(self):
        target = self._target()
        logger = get_target_logger(target, quiet=True, retention_days=7)
        original = logger.handlers[0]
        updated = get_target_logger(target, quiet=True, retention_days=19)
        self.assertIs(updated.handlers[0], original)
        self.assertEqual(updated.handlers[0].backupCount, 19)

        interactive = get_target_logger(target, quiet=False)
        self.assertEqual(interactive.handlers, [])
        self.assertTrue(interactive.propagate)


class TestNonEmptyFilter(unittest.TestCase):
    def _record(self, msg):
        return logging.LogRecord("x", logging.INFO, "p", 1, msg, None, None)

    def test_blocks_empty_and_whitespace_messages(self):
        self.assertFalse(NonEmptyFilter().filter(self._record("")))
        self.assertFalse(NonEmptyFilter().filter(self._record("   ")))

    def test_passes_real_messages(self):
        self.assertTrue(NonEmptyFilter().filter(self._record("scraped")))


class TestSaveTraceback(_LoggerCase):
    def _trigger(self, **kwargs):
        logger = logging.getLogger("test.save_traceback")
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            save_traceback(logger, log_to_console=False, **kwargs)

    def test_writes_to_the_target_error_log_with_url_and_header_id(self):
        target = self._target()
        self._trigger(
            target_name=target,
            url="https://x/s/1/p.html",
            diagnostic_context={"platform": '"Windows"', "language": "en-US"},
        )
        content = (Path(core.infrastructure.logging.LOGS_DIR) / target / "errors.txt").read_text()
        self.assertIn("URL: https://x/s/1/p.html", content)
        self.assertIn('Diagnostic context: language: en-US, platform: "Windows"', content)
        self.assertIn("RuntimeError: boom", content)
        self.assertIn("UTC", content)

    def test_without_target_falls_back_to_the_root_error_log(self):
        self._trigger(target_name=None)
        content = (Path(core.infrastructure.logging.LOGS_DIR) / "errors.txt").read_text()
        self.assertIn("RuntimeError: boom", content)

    def test_appends_rather_than_overwrites(self):
        target = self._target()
        self._trigger(target_name=target)
        self._trigger(target_name=target)
        content = (Path(core.infrastructure.logging.LOGS_DIR) / target / "errors.txt").read_text()
        self.assertEqual(content.count("RuntimeError: boom"), 2)


def test_save_diagnostic_uses_target_or_root_log_without_console_output():
    save_diagnostic("Path: /absolute/config.json\nErrno: 13")
    save_diagnostic(
        "Path: /absolute/state.json\nException: PermissionError",
        target_name="insomnia",
    )

    root = Path(core.infrastructure.logging.LOGS_DIR)
    assert "Path: /absolute/config.json" in (root / "errors.txt").read_text()
    assert "Path: /absolute/state.json" in (root / "insomnia" / "errors.txt").read_text()


def test_try_save_diagnostic_reports_secondary_write_failure(monkeypatch):
    monkeypatch.setattr(
        core.infrastructure.logging,
        "save_diagnostic",
        mock.Mock(side_effect=PermissionError("denied")),
    )

    assert not try_save_diagnostic("Path: /absolute/config.json")


if __name__ == "__main__":
    unittest.main()
