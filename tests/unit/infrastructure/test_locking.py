"""Real filesystem and process-level coverage for state-root-bound locks."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from filelock import Timeout

from core.exceptions import LockAcquisitionError, LockStorageError
from core.infrastructure.locking import StateLockManager


class TestStateLockManager(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="scrooge-lock-test-"))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.manager = StateLockManager(self.root / "state")

    def test_holds_exclusively_within_the_state_root(self):
        with self.manager.acquire("skroutz"):
            with self.assertRaises(LockAcquisitionError):
                with StateLockManager(self.root / "state").acquire("skroutz"):
                    self.fail("second acquire should not have succeeded")

    def test_releases_on_normal_and_exceptional_exit(self):
        with self.manager.acquire("skroutz"):
            pass
        with self.assertRaisesRegex(RuntimeError, "body failed"):
            with self.manager.acquire("skroutz"):
                raise RuntimeError("body failed")
        with self.manager.acquire("skroutz"):
            pass

    def test_timeout_from_protected_body_is_not_an_acquisition_error(self):
        body_timeout = Timeout("unrelated-plugin-lock")

        with self.assertRaises(Timeout) as raised:
            with self.manager.acquire("skroutz"):
                raise body_timeout

        self.assertIs(raised.exception, body_timeout)
        with self.manager.acquire("skroutz"):
            pass

    def test_target_and_framework_lock_paths_share_the_state_root(self):
        with self.manager.acquire("skroutz"):
            self.assertTrue((self.root / "state/locks/skroutz.lock").exists())
        with self.manager.acquire("reminder"):
            self.assertTrue((self.root / "state/locks/reminder.lock").exists())

    def test_different_names_and_different_state_roots_do_not_contend(self):
        other = StateLockManager(self.root / "other-state")
        with self.manager.acquire("skroutz"):
            with self.manager.acquire("reminder"):
                with other.acquire("skroutz"):
                    pass

    def test_symlinked_locks_directory_is_rejected_without_following_it(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.manager.state_dir.mkdir()
        self.manager.locks_dir.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(LockStorageError):
            with self.manager.acquire("skroutz"):
                self.fail("symlinked lock directory should not be used")

        self.assertFalse((outside / "skroutz.lock").exists())

    def test_symlinked_lock_file_is_rejected_without_following_it(self):
        self.manager.locks_dir.mkdir(parents=True)
        outside = self.root / "outside.lock"
        outside.touch()
        self.manager.lock_path("skroutz").symlink_to(outside)

        with self.assertRaises(LockStorageError):
            with self.manager.acquire("skroutz"):
                self.fail("symlinked lock file should not be used")

    def test_special_lock_file_destination_is_rejected(self):
        self.manager.locks_dir.mkdir(parents=True)
        self.manager.lock_path("skroutz").mkdir()

        with self.assertRaises(LockStorageError):
            with self.manager.acquire("skroutz"):
                self.fail("special lock-file destination should not be used")

    def test_os_error_from_protected_body_is_not_a_lock_storage_error(self):
        body_error = OSError("client body failed")

        with self.assertRaises(OSError) as raised:
            with self.manager.acquire("skroutz"):
                raise body_error

        self.assertIs(raised.exception, body_error)

    def test_acquire_and_release_os_errors_are_typed_storage_failures(self):
        lock = mock.Mock()
        lock.acquire.side_effect = PermissionError("denied")
        with mock.patch("core.infrastructure.locking.FileLock", return_value=lock):
            with self.assertRaises(LockStorageError) as acquire_error:
                with self.manager.acquire("skroutz"):
                    self.fail("lock acquisition should fail")
        self.assertIn("acquire machine-state lock", acquire_error.exception.diagnostic_detail)

        lock.reset_mock()
        lock.acquire.side_effect = None
        lock.release.side_effect = OSError("release failed")
        with mock.patch("core.infrastructure.locking.FileLock", return_value=lock):
            with self.assertRaises(LockStorageError) as release_error:
                with self.manager.acquire("skroutz"):
                    pass
        self.assertIn("release machine-state lock", release_error.exception.diagnostic_detail)

    def _holder(self) -> subprocess.Popen[str]:
        script = """
import sys
from pathlib import Path

sys.path.insert(0, str(Path.cwd() / "src"))
from core.infrastructure.locking import StateLockManager

with StateLockManager(sys.argv[1]).acquire("skroutz"):
    print("locked", flush=True)
    sys.stdin.read()
"""
        process = subprocess.Popen(
            [sys.executable, "-c", script, str(self.manager.state_dir)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        self.addCleanup(self._stop_process, process)
        assert process.stdout is not None
        self.assertEqual("locked\n", process.stdout.readline())
        return process

    @staticmethod
    def _stop_process(process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            process.kill()
            process.wait()
        for stream in (process.stdin, process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()

    def test_cross_process_contention_and_graceful_release(self):
        process = self._holder()
        with self.assertRaises(LockAcquisitionError):
            with self.manager.acquire("skroutz"):
                self.fail("another process owns the lock")

        assert process.stdin is not None
        process.stdin.close()
        self.assertEqual(0, process.wait())
        with self.manager.acquire("skroutz"):
            pass

    def test_process_death_releases_the_kernel_lock(self):
        process = self._holder()
        process.kill()
        process.wait()

        self.assertTrue(self.manager.lock_path("skroutz").exists())
        with self.manager.acquire("skroutz"):
            pass


if __name__ == "__main__":
    unittest.main()
