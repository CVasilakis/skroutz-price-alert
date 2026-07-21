"""Unit tests for the execution lock helper, exercising a real ``FileLock``.

Every other suite patches ``acquire_lock`` to a no-op, so this is the one place the
actual mutual exclusion and lock-file placement are verified - the guarantee the reminder
and the orchestrator both rely on to stop concurrent runs from racing. ``LOGS_DIR`` is
redirected to a temp dir by the autouse ``_isolate_logs_dir`` fixture, so the lock files
land there and never in the repository's ``logs/``.
"""

import os
import unittest

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
            expected = os.path.join(
                core.infrastructure.locking.LOGS_DIR, "skroutz", "skroutz_scraper_running.lock"
            )
            self.assertTrue(os.path.exists(expected), expected)

    def test_custom_lock_filename(self):
        # The reminder pseudo-target overrides the filename (it guards a liveness check).
        with acquire_lock("reminder", "reminder_check.lock"):
            expected = os.path.join(
                core.infrastructure.locking.LOGS_DIR, "reminder", "reminder_check.lock"
            )
            self.assertTrue(os.path.exists(expected), expected)

    def test_different_targets_do_not_contend(self):
        # Each target owns its own directory/lock, so distinct targets never block.
        with acquire_lock("skroutz"):
            with acquire_lock("reminder", "reminder_check.lock"):
                pass


if __name__ == "__main__":
    unittest.main()
