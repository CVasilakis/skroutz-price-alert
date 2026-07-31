"""Unit tests for the execution lock helper, exercising a real ``FileLock``.

Every other suite patches ``acquire_lock`` to a no-op, so this is the one place the
actual mutual exclusion and lock-file placement are verified - the guarantee the reminder
and ``TargetRunner`` both rely on to stop concurrent runs from racing. ``LOCKS_DIR`` is
redirected to a temporary ``state/locks/`` by the autouse fixture.
"""

import os
import unittest
from pathlib import Path

import core.infrastructure.locking
from core.exceptions import LockAcquisitionError
from core.infrastructure.locking import acquire_lock


class TestAcquireLock(unittest.TestCase):
    def test_holds_exclusively_within_the_target(self):
        # A second acquire of the same target while the first is held fails immediately
        # (LOCK_TIMEOUT is 0), which the helper surfaces as LockAcquisitionError.
        with acquire_lock("skroutz"):
            with self.assertRaises(LockAcquisitionError):
                with acquire_lock("skroutz"):
                    self.fail("second acquire should not have succeeded")

    def test_releases_on_exit(self):
        with acquire_lock("skroutz"):
            pass
        # After the context exits the lock is free again.
        with acquire_lock("skroutz"):
            pass

    def test_default_lock_filename_and_dir(self):
        with acquire_lock("skroutz"):
            expected = os.path.join(core.infrastructure.locking.LOCKS_DIR, "skroutz.lock")
            self.assertTrue(os.path.exists(expected), expected)

    def test_framework_lock_uses_the_same_flat_namespace(self):
        with acquire_lock("reminder"):
            expected = os.path.join(core.infrastructure.locking.LOCKS_DIR, "reminder.lock")
            self.assertTrue(os.path.exists(expected), expected)

    def test_different_targets_do_not_contend(self):
        # Distinct names resolve to distinct lock files, so they never block each other.
        with acquire_lock("skroutz"):
            with acquire_lock("reminder"):
                pass

    def test_symlinked_locks_directory_is_rejected_without_following_it(self):
        locks_dir = Path(core.infrastructure.locking.LOCKS_DIR)
        outside = locks_dir.parent.parent / "outside"
        outside.mkdir()
        locks_dir.parent.mkdir()
        locks_dir.symlink_to(outside, target_is_directory=True)

        with self.assertRaises(OSError):
            with acquire_lock("skroutz"):
                self.fail("symlinked lock directory should not be used")

        self.assertFalse((outside / "skroutz.lock").exists())

    def test_symlinked_lock_file_is_rejected_without_following_it(self):
        locks_dir = Path(core.infrastructure.locking.LOCKS_DIR)
        locks_dir.mkdir(parents=True)
        outside = locks_dir.parent / "outside.lock"
        outside.touch()
        (locks_dir / "skroutz.lock").symlink_to(outside)

        with self.assertRaises(OSError):
            with acquire_lock("skroutz"):
                self.fail("symlinked lock file should not be used")

    def test_special_lock_file_destination_is_rejected(self):
        locks_dir = Path(core.infrastructure.locking.LOCKS_DIR)
        locks_dir.mkdir(parents=True)
        (locks_dir / "skroutz.lock").mkdir()

        with self.assertRaises(OSError):
            with acquire_lock("skroutz"):
                self.fail("special lock-file destination should not be used")


if __name__ == "__main__":
    unittest.main()
