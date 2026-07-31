"""Real filesystem and process-level coverage for state-root-bound locks."""

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from filelock import Timeout

from core.exceptions import LockAcquisitionError
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

        with self.assertRaises(OSError):
            with self.manager.acquire("skroutz"):
                self.fail("symlinked lock directory should not be used")

        self.assertFalse((outside / "skroutz.lock").exists())

    def test_symlinked_lock_file_is_rejected_without_following_it(self):
        self.manager.locks_dir.mkdir(parents=True)
        outside = self.root / "outside.lock"
        outside.touch()
        self.manager.lock_path("skroutz").symlink_to(outside)

        with self.assertRaises(OSError):
            with self.manager.acquire("skroutz"):
                self.fail("symlinked lock file should not be used")

    def test_special_lock_file_destination_is_rejected(self):
        self.manager.locks_dir.mkdir(parents=True)
        self.manager.lock_path("skroutz").mkdir()

        with self.assertRaises(OSError):
            with self.manager.acquire("skroutz"):
                self.fail("special lock-file destination should not be used")

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
