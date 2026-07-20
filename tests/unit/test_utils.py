"""Unit tests for the env/URL validation helpers in ``core.utils``.

Everywhere else these are *patched out* as seams (the preflight, the drivers,
the ``main()`` E2E); this is the one place their real logic runs — real
``.env`` files on disk and real Apprise instantiation (offline: ``json://`` and
``tgram://`` plugins instantiate without any network).
"""

import os
import shutil
import signal
import tempfile
import unittest
from unittest import mock

import core.utils
from core.exceptions import EnvFileError
from core.utils import (
    check_env_file,
    classify_notification_urls,
    describe_signal,
    is_valid_apprise_url,
)

VALID_URL = "json://localhost"
PLACEHOLDER_URL = "tgram://<token>/<chat_id>"


class _EnvFileCase(unittest.TestCase):
    """Base: a temp BASE_DIR for the .env file and a clean NOTIFICATION_URLS."""

    def setUp(self):
        self.base_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.base_dir, ignore_errors=True)
        # check_env_file resolves .env against the BASE_DIR bound in core.utils.
        patcher = mock.patch.object(core.utils, "BASE_DIR", self.base_dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        # load_dotenv does not override an existing env var, so clear it to let
        # each test's .env content take effect.
        env_patcher = mock.patch.dict(os.environ)
        env_patcher.start()
        self.addCleanup(env_patcher.stop)
        os.environ.pop("NOTIFICATION_URLS", None)

    def _write_env(self, content):
        path = os.path.join(self.base_dir, ".env")
        with open(path, "w") as f:
            f.write(content)
        return path


class TestCheckEnvFile(_EnvFileCase):
    def test_missing_file_raises(self):
        with self.assertRaisesRegex(EnvFileError, "No .env file"):
            check_env_file()

    @unittest.skipIf(os.geteuid() == 0, "root ignores file permissions")
    def test_unreadable_file_raises(self):
        path = self._write_env(f"NOTIFICATION_URLS={VALID_URL}\n")
        os.chmod(path, 0o000)
        self.addCleanup(os.chmod, path, 0o644)
        with self.assertRaisesRegex(EnvFileError, "No .env file found or unreadable"):
            check_env_file()

    def test_empty_notification_urls_raises(self):
        self._write_env("NOTIFICATION_URLS=\n")
        with self.assertRaisesRegex(EnvFileError, "No NOTIFICATION_URLS provided"):
            check_env_file()

    def test_all_invalid_urls_raise(self):
        self._write_env(f"NOTIFICATION_URLS={PLACEHOLDER_URL}, not-a-url\n")
        with self.assertRaisesRegex(EnvFileError, "no valid notification URL"):
            check_env_file()

    def test_one_valid_url_passes(self):
        # One valid endpoint among invalid ones is enough to run.
        self._write_env(f"NOTIFICATION_URLS=not-a-url, {VALID_URL}\n")
        check_env_file()  # must not raise

    def test_invalid_utf8_raises_env_file_error(self):
        path = os.path.join(self.base_dir, ".env")
        with open(path, "wb") as file:
            file.write(b"\xff")
        with self.assertRaisesRegex(EnvFileError, "not valid UTF-8"):
            check_env_file()


class TestIsValidAppriseUrl(unittest.TestCase):
    def test_valid_url(self):
        self.assertTrue(is_valid_apprise_url(VALID_URL))

    def test_surrounding_whitespace_is_ignored(self):
        self.assertTrue(is_valid_apprise_url(f"  {VALID_URL}  "))

    def test_empty_and_none_are_invalid(self):
        self.assertFalse(is_valid_apprise_url(""))
        self.assertFalse(is_valid_apprise_url(None))

    def test_unconfigured_placeholder_is_invalid(self):
        # The template's placeholder tokens must never count as configured.
        self.assertFalse(is_valid_apprise_url(PLACEHOLDER_URL))

    def test_unrecognized_scheme_is_invalid(self):
        self.assertFalse(is_valid_apprise_url("not-a-url"))


class TestClassifyNotificationUrls(unittest.TestCase):
    def test_partitions_valid_and_invalid_preserving_order(self):
        raw = f"{VALID_URL}, {PLACEHOLDER_URL}, not-a-url, mailto://user:pass@example.com"
        valid, invalid = classify_notification_urls(raw)
        self.assertEqual(valid, [VALID_URL, "mailto://user:pass@example.com"])
        self.assertEqual(invalid, [PLACEHOLDER_URL, "not-a-url"])

    def test_empty_entries_are_skipped(self):
        valid, invalid = classify_notification_urls(f" , {VALID_URL},,  ")
        self.assertEqual(valid, [VALID_URL])
        self.assertEqual(invalid, [])

    def test_empty_and_none_input(self):
        self.assertEqual(classify_notification_urls(""), ([], []))
        self.assertEqual(classify_notification_urls(None), ([], []))


class TestDescribeSignal(unittest.TestCase):
    def test_known_signals(self):
        self.assertEqual(describe_signal(signal.SIGINT), "SIGINT (Ctrl+C)")
        self.assertEqual(describe_signal(signal.SIGTERM), "SIGTERM (System Shutdown/Termination)")

    def test_unknown_signal_is_the_raw_number(self):
        self.assertEqual(describe_signal(99), "99")


if __name__ == "__main__":
    unittest.main()
