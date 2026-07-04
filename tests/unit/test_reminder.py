"""Unit tests for the periodic liveness reminder.

Everything external is controlled: the clock and the update check are injected seams, the
notifier is a mock, and the config dir is a temp dir. Writes to ``logs/`` are contained
suite-wide by the autouse ``_isolate_logs_dir`` fixture (see ``tests/conftest.py``), which
redirects ``LOGS_DIR`` to a temp dir; the per-test ``_logger`` mock additionally keeps a
real file handler from being created. The core invariant under test is the anti-sliding
slot grid: persisted ``last_reminder`` values are always grid points (the configured
weekday at the configured local time; Saturday 13:00 by default), never actual send times.
"""

import contextlib
import datetime
import os
import tempfile
import unittest
from unittest import mock

import core.constants
import core.logger
from core.constants import TIMESTAMP_FORMAT
from core.exceptions import LockAcquisitionError, UpdateCheckError
from core.general import ReminderService
from core.general.reminder import (
    LAST_REMINDER_FIELD, most_recent_slot, next_due_slot,
)
from core.general.vocab import (
    normalize_reminder, normalize_reminder_day, normalize_reminder_time,
    time_parts, weekday_index,
)


# A Saturday: 2026-07-04. The default grid slot that day is 13:00:00 local time.
SAT = datetime.datetime(2026, 7, 4, 13, 0, 0)


from support import write_general as _write_general, read_general as _read_general


@contextlib.contextmanager
def _noop_lock(_name, _lock_filename=None):
    yield


def _make_service(cfg_dir, now, notify_return=True, update_return=False):
    """Builds a ReminderService with every collaborator controlled.

    Returns (service, notifier_mock, update_check_mock, clock). Advance time between
    calls via ``clock["now"] = ...``. The logger is a mock so no log dir is created.
    """
    clock = {"now": now}
    notifier = mock.Mock()
    notifier.notify_reminder.return_value = notify_return
    update_fn = mock.Mock(return_value=update_return)
    service = ReminderService(cfg_dir, notifier,
                              now_fn=lambda: clock["now"], update_check_fn=update_fn)
    service._logger = mock.Mock()
    return service, notifier, update_fn, clock


class _LockedDownCase(unittest.TestCase):
    """Base case: temp config dir + the file lock patched to a no-op."""

    def setUp(self):
        self.cfg_dir = tempfile.mkdtemp()
        patcher = mock.patch("core.general.reminder.acquire_lock", _noop_lock)
        patcher.start()
        self.addCleanup(patcher.stop)


class TestNormalizeReminder(unittest.TestCase):
    def test_supported_spellings(self):
        cases = [
            ("off", "off"), ("OFF", "off"), ("disabled", "off"), ("never", "off"),
            ("1 week", "1w"), ("1Week", "1w"), ("weekly", "1w"), ("1w", "1w"),
            ("1 month", "1m"), ("monthly", "1m"), ("1mo", "1m"),
            ("3 months", "3m"), ("quarterly", "3m"), ("3m", "3m"),
            ("6 months", "6m"), ("6m", "6m"),
            ("1 year", "1y"), ("12 months", "1y"), ("yearly", "1y"), ("annually", "1y"),
        ]
        for raw, expected in cases:
            self.assertEqual(normalize_reminder(raw), expected, f"raw={raw!r}")

    def test_unsupported_values_rejected(self):
        for raw in ["", "  ", "2 weeks", "2m", "5 months", "2 years", "1h", "7 days",
                    "soon", "4", None, True, 4, 1.5]:
            self.assertIsNone(normalize_reminder(raw), f"raw={raw!r}")


class TestNormalizeReminderDay(unittest.TestCase):
    def test_supported_spellings(self):
        cases = [
            ("monday", "Monday"), ("Monday", "Monday"), ("MON", "Monday"),
            ("tue", "Tuesday"), ("tues", "Tuesday"), ("wednesday", "Wednesday"),
            ("weds", "Wednesday"), ("thurs", "Thursday"), ("fri", "Friday"),
            (" Saturday ", "Saturday"), ("SAT", "Saturday"), ("sun", "Sunday"),
        ]
        for raw, expected in cases:
            self.assertEqual(normalize_reminder_day(raw), expected, f"raw={raw!r}")

    def test_unsupported_values_rejected(self):
        for raw in ["", "  ", "funday", "sundae", "6", None, True, 5]:
            self.assertIsNone(normalize_reminder_day(raw), f"raw={raw!r}")

    def test_weekday_index_round_trips(self):
        self.assertEqual(weekday_index("Monday"), 0)
        self.assertEqual(weekday_index("Saturday"), 5)
        self.assertEqual(weekday_index("Sunday"), 6)


class TestNormalizeReminderTime(unittest.TestCase):
    def test_supported_spellings(self):
        cases = [
            ("13:00", "13:00"), ("13", "13:00"), ("9", "09:00"), ("9:05", "09:05"),
            ("00:00", "00:00"), ("23:59", "23:59"), (" 1 pm ", "13:00"),
            ("1pm", "13:00"), ("1:30pm", "13:30"), ("12am", "00:00"), ("12pm", "12:00"),
            ("9am", "09:00"),
        ]
        for raw, expected in cases:
            self.assertEqual(normalize_reminder_time(raw), expected, f"raw={raw!r}")

    def test_unsupported_values_rejected(self):
        for raw in ["", "  ", "25:00", "13:99", "1300", "noon", "abc", "13pm",
                    "0pm", "-1", None, True, 13, 9.5]:
            self.assertIsNone(normalize_reminder_time(raw), f"raw={raw!r}")

    def test_time_parts_splits_canonical(self):
        self.assertEqual(time_parts("13:00"), (13, 0))
        self.assertEqual(time_parts("09:05"), (9, 5))


class TestSlotMath(unittest.TestCase):
    def test_saturday_before_13_belongs_to_previous_week(self):
        self.assertEqual(most_recent_slot(SAT.replace(hour=12, minute=59, second=59)),
                         SAT - datetime.timedelta(days=7))

    def test_saturday_exactly_13_is_its_own_slot(self):
        self.assertEqual(most_recent_slot(SAT), SAT)

    def test_sunday_and_midweek_map_back_to_saturday(self):
        for now in [SAT + datetime.timedelta(hours=14, minutes=17),   # Sunday 03:17
                    SAT + datetime.timedelta(days=4, hours=-4)]:      # Wednesday 09:00
            self.assertEqual(most_recent_slot(now), SAT, f"now={now}")

    def test_next_due_slot_stays_on_the_grid(self):
        for weeks in (1, 4, 13, 26, 52):
            due = next_due_slot(SAT, weeks)
            self.assertEqual(due.weekday(), 5)
            self.assertEqual((due.hour, due.minute, due.second), (13, 0, 0))


class TestFirstRun(_LockedDownCase):
    def test_missing_file_writes_anchor_and_sends_nothing(self):
        service, notifier, update_fn, _ = _make_service(
            self.cfg_dir, SAT + datetime.timedelta(hours=1))
        service.run_once()

        notifier.notify_reminder.assert_not_called()
        update_fn.assert_not_called()
        self.assertEqual(_read_general(self.cfg_dir),
                         {LAST_REMINDER_FIELD: SAT.strftime(TIMESTAMP_FORMAT)})

    def test_missing_field_patches_anchor_preserving_user_content(self):
        _write_general(self.cfg_dir, {"settings": {"reminder": "1 week"},
                                      "custom_key": [1, 2, 3]})
        service, notifier, _, _ = _make_service(self.cfg_dir, SAT)
        service.run_once()

        notifier.notify_reminder.assert_not_called()
        data = _read_general(self.cfg_dir)
        self.assertEqual(data["settings"], {"reminder": "1 week"})
        self.assertEqual(data["custom_key"], [1, 2, 3])
        self.assertEqual(data[LAST_REMINDER_FIELD], SAT.strftime(TIMESTAMP_FORMAT))


class TestDueAndDelivery(_LockedDownCase):
    def test_sunday_delivery_records_saturdays_slot(self):
        _write_general(self.cfg_dir, {
            "settings": {"reminder": "1 week"},
            LAST_REMINDER_FIELD: "27-06-2026 13:00:00",
        })
        sunday = datetime.datetime(2026, 7, 5, 3, 17, 42)
        service, notifier, _, _ = _make_service(self.cfg_dir, sunday)
        service.run_once()

        notifier.notify_reminder.assert_called_once()
        self.assertEqual(_read_general(self.cfg_dir)[LAST_REMINDER_FIELD],
                         "04-07-2026 13:00:00")  # the slot, not the send time

    def test_no_sliding_over_many_late_deliveries(self):
        # Every delivery lands hours late (Sunday 03:17); the persisted values must
        # remain exact 13:00:00 Saturdays, 7 days apart, forever.
        _write_general(self.cfg_dir, {
            "settings": {"reminder": "1 week"},
            LAST_REMINDER_FIELD: "27-06-2026 13:00:00",
        })
        service, notifier, _, clock = _make_service(
            self.cfg_dir, datetime.datetime(2026, 7, 5, 3, 17))

        slots = []
        for week in range(8):
            clock["now"] = datetime.datetime(2026, 7, 5, 3, 17) + datetime.timedelta(weeks=week)
            service.run_once()
            slots.append(datetime.datetime.strptime(
                _read_general(self.cfg_dir)[LAST_REMINDER_FIELD], TIMESTAMP_FORMAT))

        self.assertEqual(notifier.notify_reminder.call_count, 8)
        for slot in slots:
            self.assertEqual(slot.weekday(), 5)
            self.assertEqual((slot.hour, slot.minute, slot.second), (13, 0, 0))
        for earlier, later in zip(slots, slots[1:]):
            self.assertEqual(later - earlier, datetime.timedelta(days=7))

    def test_missed_slots_send_once_and_jump_to_latest(self):
        # Host was off for months: exactly one reminder, anchored to the latest slot
        # (not last_slot + interval), so there is no catch-up storm.
        _write_general(self.cfg_dir, {
            "settings": {"reminder": "1 week"},
            LAST_REMINDER_FIELD: "07-02-2026 13:00:00",
        })
        service, notifier, _, _ = _make_service(self.cfg_dir, SAT + datetime.timedelta(hours=2))
        service.run_once()

        self.assertEqual(notifier.notify_reminder.call_count, 1)
        self.assertEqual(_read_general(self.cfg_dir)[LAST_REMINDER_FIELD],
                         SAT.strftime(TIMESTAMP_FORMAT))

    def test_not_due_makes_no_write_and_no_network(self):
        original = {"settings": {"reminder": "1 month"},
                    LAST_REMINDER_FIELD: SAT.strftime(TIMESTAMP_FORMAT)}
        _write_general(self.cfg_dir, original)
        service, notifier, update_fn, _ = _make_service(
            self.cfg_dir, SAT + datetime.timedelta(weeks=2))  # 1m = 4 weeks: not due
        service.run_once()

        notifier.notify_reminder.assert_not_called()
        update_fn.assert_not_called()  # the update check is lazy: due reminders only
        self.assertEqual(_read_general(self.cfg_dir), original)

    def test_delivery_failure_keeps_timestamp_then_retry_succeeds(self):
        _write_general(self.cfg_dir, {
            "settings": {"reminder": "1 week"},
            LAST_REMINDER_FIELD: "27-06-2026 13:00:00",
        })
        service, notifier, _, clock = _make_service(
            self.cfg_dir, SAT + datetime.timedelta(hours=1), notify_return=False)
        service.run_once()
        self.assertEqual(_read_general(self.cfg_dir)[LAST_REMINDER_FIELD],
                         "27-06-2026 13:00:00")  # not advanced

        notifier.notify_reminder.return_value = True
        clock["now"] = SAT + datetime.timedelta(hours=6)  # e.g. the next scraper run
        service.run_once()
        self.assertEqual(notifier.notify_reminder.call_count, 2)
        self.assertEqual(_read_general(self.cfg_dir)[LAST_REMINDER_FIELD],
                         SAT.strftime(TIMESTAMP_FORMAT))

    def test_notification_reports_cadence_and_next_due(self):
        _write_general(self.cfg_dir, {
            "settings": {"reminder": "1 month"},
            LAST_REMINDER_FIELD: "06-06-2026 13:00:00",
        })
        service, notifier, _, _ = _make_service(
            self.cfg_dir, SAT + datetime.timedelta(hours=1), update_return=True)
        service.run_once()

        update_available, interval_display, next_due = notifier.notify_reminder.call_args.args
        self.assertIs(update_available, True)
        self.assertEqual(interval_display, "1 month")
        self.assertEqual(next_due, "01-08-2026 13:00:00")  # slot + 4 weeks


class TestCustomSchedule(_LockedDownCase):
    def test_custom_day_and_time_anchor_the_grid(self):
        # reminder_day/reminder_time move the grid off the Saturday-13:00 default: the
        # persisted slot and the reported next-due land on the configured Monday 09:00.
        _write_general(self.cfg_dir, {
            "settings": {"reminder": "1 week", "reminder_day": "Monday",
                         "reminder_time": "9:00"},
            LAST_REMINDER_FIELD: "29-06-2026 09:00:00",  # the previous Monday 09:00
        })
        monday = datetime.datetime(2026, 7, 6, 10, 30, 0)  # Monday, after the 09:00 slot
        service, notifier, _, _ = _make_service(self.cfg_dir, monday)
        service.run_once()

        notifier.notify_reminder.assert_called_once()
        self.assertEqual(_read_general(self.cfg_dir)[LAST_REMINDER_FIELD],
                         "06-07-2026 09:00:00")  # the Monday 09:00 slot, not the send time
        self.assertEqual(notifier.notify_reminder.call_args.args[2],
                         "13-07-2026 09:00:00")  # next due: slot + 1 week

    def test_invalid_day_and_time_fall_back_to_saturday_13(self):
        # Unsupported reminder_day/time warn and default, so the grid stays Saturday 13:00.
        _write_general(self.cfg_dir, {
            "settings": {"reminder": "1 week", "reminder_day": "Funday",
                         "reminder_time": "25:00"},
            LAST_REMINDER_FIELD: "27-06-2026 13:00:00",
        })
        service, notifier, _, _ = _make_service(self.cfg_dir, SAT + datetime.timedelta(hours=1))
        service.run_once()

        notifier.notify_reminder.assert_called_once()
        self.assertEqual(_read_general(self.cfg_dir)[LAST_REMINDER_FIELD],
                         SAT.strftime(TIMESTAMP_FORMAT))
        warnings = [c.args[0] for c in service._logger.warning.call_args_list]
        self.assertTrue(any("reminder_day" in w for w in warnings))
        self.assertTrue(any("reminder_time" in w for w in warnings))


class TestUnusableState(_LockedDownCase):
    def test_corrupt_timestamp_reanchors_with_warning_and_no_send(self):
        _write_general(self.cfg_dir, {
            "settings": {"reminder": "1 week"},
            LAST_REMINDER_FIELD: "not-a-timestamp",
        })
        service, notifier, _, _ = _make_service(self.cfg_dir, SAT + datetime.timedelta(days=1))
        service.run_once()

        notifier.notify_reminder.assert_not_called()
        self.assertEqual(_read_general(self.cfg_dir)[LAST_REMINDER_FIELD],
                         SAT.strftime(TIMESTAMP_FORMAT))
        service._logger.warning.assert_called_once()
        self.assertIn("Corrupted last_reminder", service._logger.warning.call_args.args[0])

    def test_corrupt_file_is_backed_up_and_rebuilt(self):
        path = os.path.join(self.cfg_dir, "general.json")
        with open(path, "w") as f:
            f.write("{ not json")
        service, notifier, _, _ = _make_service(self.cfg_dir, SAT)
        service.run_once()

        # Unreadable settings degrade to the default cadence; unusable state anchors.
        notifier.notify_reminder.assert_not_called()
        self.assertTrue(os.path.isfile(path + ".corrupt"))
        self.assertEqual(_read_general(self.cfg_dir),
                         {LAST_REMINDER_FIELD: SAT.strftime(TIMESTAMP_FORMAT)})


class TestGates(unittest.TestCase):
    def setUp(self):
        self.cfg_dir = tempfile.mkdtemp()

    def test_off_takes_no_lock_and_touches_nothing(self):
        _write_general(self.cfg_dir, {"settings": {"reminder": "off"},
                                      LAST_REMINDER_FIELD: "07-02-2026 13:00:00"})
        service, notifier, update_fn, _ = _make_service(self.cfg_dir, SAT)
        with mock.patch("core.general.reminder.acquire_lock") as lock:
            service.run_once()

        lock.assert_not_called()
        notifier.notify_reminder.assert_not_called()
        update_fn.assert_not_called()
        self.assertEqual(_read_general(self.cfg_dir)[LAST_REMINDER_FIELD],
                         "07-02-2026 13:00:00")

    def test_invalid_value_warns_and_uses_default_cadence(self):
        # "fortnightly" is unsupported: warn once, run with the 1-month default.
        _write_general(self.cfg_dir, {"settings": {"reminder": "fortnightly"},
                                      LAST_REMINDER_FIELD: "06-06-2026 13:00:00"})
        service, notifier, _, _ = _make_service(self.cfg_dir, SAT + datetime.timedelta(hours=1))
        with mock.patch("core.general.reminder.acquire_lock", _noop_lock):
            service.run_once()

        warnings = [c.args[0] for c in service._logger.warning.call_args_list]
        self.assertTrue(any("Unsupported reminder value" in w for w in warnings))
        # 06-06 + 4 weeks = 04-07 13:00: due under the default cadence.
        self.assertEqual(notifier.notify_reminder.call_args.args[1], "1 month")

    def test_busy_lock_skips_silently(self):
        _write_general(self.cfg_dir, {"settings": {"reminder": "1 week"},
                                      LAST_REMINDER_FIELD: "27-06-2026 13:00:00"})
        service, notifier, _, _ = _make_service(self.cfg_dir, SAT + datetime.timedelta(hours=1))

        @contextlib.contextmanager
        def busy_lock(_name, _lock_filename=None):
            raise LockAcquisitionError
            yield  # pragma: no cover

        with mock.patch("core.general.reminder.acquire_lock", busy_lock):
            service.run_once()  # must not raise

        notifier.notify_reminder.assert_not_called()
        self.assertEqual(_read_general(self.cfg_dir)[LAST_REMINDER_FIELD],
                         "27-06-2026 13:00:00")


class TestUpdateCheckTolerance(_LockedDownCase):
    def _run_with_failing_check(self, exc):
        _write_general(self.cfg_dir, {"settings": {"reminder": "1 week"},
                                      LAST_REMINDER_FIELD: "27-06-2026 13:00:00"})
        service, notifier, update_fn, _ = _make_service(self.cfg_dir, SAT + datetime.timedelta(hours=1))
        update_fn.side_effect = exc
        service.run_once()
        return notifier

    def test_update_check_error_still_sends_as_inconclusive(self):
        notifier = self._run_with_failing_check(UpdateCheckError("no network"))
        notifier.notify_reminder.assert_called_once()
        self.assertIsNone(notifier.notify_reminder.call_args.args[0])

    def test_unexpected_update_check_failure_still_sends(self):
        notifier = self._run_with_failing_check(RuntimeError("boom"))
        notifier.notify_reminder.assert_called_once()
        self.assertIsNone(notifier.notify_reminder.call_args.args[0])


class TestNeverRaises(_LockedDownCase):
    def test_unexpected_failure_is_swallowed_and_tracebacked(self):
        _write_general(self.cfg_dir, {"settings": {"reminder": "1 week"}})
        service, _, _, _ = _make_service(self.cfg_dir, SAT)
        with mock.patch.object(service, "_read_state", side_effect=RuntimeError("boom")), \
             mock.patch("core.general.reminder.save_traceback") as saver:
            service.run_once()  # must not raise
        saver.assert_called_once()

    def test_real_traceback_write_is_isolated_from_the_repo_logs_dir(self):
        # Regression for the leak that produced a real logs/reminder/errors.txt: the
        # except path calls the *real* save_traceback (unpatched here), which appends to
        # LOGS_DIR/reminder/errors.txt. The autouse conftest redirect must send that to a
        # temp dir, never the repository's own logs/.
        self.assertNotEqual(core.logger.LOGS_DIR, core.constants.LOGS_DIR,
                            "conftest should have redirected LOGS_DIR away from the repo")
        repo_errors = os.path.join(core.constants.LOGS_DIR, "reminder", "errors.txt")
        before = os.path.getsize(repo_errors) if os.path.isfile(repo_errors) else None

        _write_general(self.cfg_dir, {"settings": {"reminder": "1 week"}})
        service, _, _, _ = _make_service(self.cfg_dir, SAT)
        with mock.patch.object(service, "_read_state", side_effect=RuntimeError("boom")):
            service.run_once()  # must not raise; writes a real traceback file

        # The traceback landed in the redirected temp dir...
        self.assertTrue(os.path.isfile(
            os.path.join(core.logger.LOGS_DIR, "reminder", "errors.txt")))
        # ...and the repository's own logs/ was left completely untouched.
        after = os.path.getsize(repo_errors) if os.path.isfile(repo_errors) else None
        self.assertEqual(before, after,
                         "traceback leaked into the real repository logs/ directory")

    def test_persist_write_failure_skips_send_to_avoid_a_storm(self):
        # State is persisted *before* sending: if the write fails, the reminder is NOT
        # sent (else a persistent write failure would re-deliver on every run). The
        # timestamp is left unchanged so the next run retries once the disk recovers.
        _write_general(self.cfg_dir, {"settings": {"reminder": "1 week"},
                                      LAST_REMINDER_FIELD: "27-06-2026 13:00:00"})
        service, notifier, _, _ = _make_service(self.cfg_dir, SAT + datetime.timedelta(hours=1))
        with mock.patch("core.general.reminder.write_json_atomically",
                        side_effect=OSError("disk full")):
            service.run_once()  # must not raise

        notifier.notify_reminder.assert_not_called()
        self.assertEqual(_read_general(self.cfg_dir)[LAST_REMINDER_FIELD],
                         "27-06-2026 13:00:00")  # unchanged; next run retries

    def test_run_once_never_raises_even_if_logging_fails(self):
        # The except path builds the reminder logger to write a traceback; if the log
        # directory itself is unwritable, that build fails too. run_once must still not
        # propagate (it is called outside main()'s crash-handling try block).
        _write_general(self.cfg_dir, {"settings": {"reminder": "1 week"}})
        clock = {"now": SAT}
        service = ReminderService(self.cfg_dir, mock.Mock(),
                                  now_fn=lambda: clock["now"],
                                  update_check_fn=mock.Mock(return_value=False))
        with mock.patch("core.general.reminder.get_target_logger", side_effect=OSError("logs/ read-only")), \
             mock.patch.object(service, "_read_state", side_effect=RuntimeError("boom")):
            service.run_once()  # must not raise despite both the run AND the logger failing


class TestFutureAndUnreadableState(_LockedDownCase):
    def test_future_last_reminder_reanchors_with_warning_and_no_send(self):
        # A parseable but future last_reminder (host clock moved back, restored backup,
        # manual edit) must not silently mute reminders until that time passes: it is
        # treated as unusable and re-anchored to the current grid, with a warning.
        _write_general(self.cfg_dir, {
            "settings": {"reminder": "1 week"},
            LAST_REMINDER_FIELD: "01-08-2026 13:00:00",  # a month ahead of `now`
        })
        service, notifier, _, _ = _make_service(self.cfg_dir, SAT)
        service.run_once()

        notifier.notify_reminder.assert_not_called()
        self.assertEqual(_read_general(self.cfg_dir)[LAST_REMINDER_FIELD],
                         SAT.strftime(TIMESTAMP_FORMAT))  # re-anchored to the current slot
        warnings = [c.args[0] for c in service._logger.warning.call_args_list]
        self.assertTrue(any("future" in w for w in warnings))

    def test_unreadable_state_skips_without_clobbering_settings(self):
        # An OSError reading an existing file must not lead to a state-only rewrite that
        # wipes the user's settings block; the run is skipped and retried next time.
        original = {"settings": {"reminder": "1 week", "reminder_day": "Monday"},
                    LAST_REMINDER_FIELD: "27-06-2026 13:00:00"}
        _write_general(self.cfg_dir, original)
        service, notifier, _, _ = _make_service(self.cfg_dir, SAT + datetime.timedelta(hours=1))
        with mock.patch.object(service, "_read_state", return_value=(None, None, "unreadable")):
            service.run_once()

        notifier.notify_reminder.assert_not_called()
        self.assertEqual(_read_general(self.cfg_dir), original)  # settings untouched


class TestNoServices(_LockedDownCase):
    def test_no_notification_services_skips_entirely(self):
        # With no deliverable notification target, the reminder can never be sent; it must
        # skip without probing the network or advancing (else it would retry forever).
        _write_general(self.cfg_dir, {"settings": {"reminder": "1 week"},
                                      LAST_REMINDER_FIELD: "27-06-2026 13:00:00"})
        service, notifier, update_fn, _ = _make_service(self.cfg_dir, SAT + datetime.timedelta(hours=1))
        notifier.has_services = False
        service.run_once()

        notifier.notify_reminder.assert_not_called()
        update_fn.assert_not_called()
        self.assertEqual(_read_general(self.cfg_dir)[LAST_REMINDER_FIELD],
                         "27-06-2026 13:00:00")  # untouched


class TestRescheduleDoesNotDoubleSend(_LockedDownCase):
    def test_reminder_time_change_does_not_double_send(self):
        # A weekly reminder anchored to Saturday 13:00; the user moves the time to 23:00.
        # The reminder due at the old 13:00 grid is delivered once, but the recorded slot
        # must not regress and fire a *second* reminder at 23:00 the same day.
        _write_general(self.cfg_dir, {
            "settings": {"reminder": "1 week", "reminder_time": "23:00"},
            LAST_REMINDER_FIELD: "27-06-2026 13:00:00",
        })
        service, notifier, _, clock = _make_service(self.cfg_dir, SAT + datetime.timedelta(hours=1))
        service.run_once()
        self.assertEqual(notifier.notify_reminder.call_count, 1)

        # Later the same day, after the newly-configured 23:00 grid time: must NOT re-fire.
        clock["now"] = SAT.replace(hour=23, minute=30)
        service.run_once()
        self.assertEqual(notifier.notify_reminder.call_count, 1)  # still one - no double send

    def test_not_due_run_takes_no_lock(self):
        # The unlocked pre-check short-circuits the common "not due" path before the lock.
        _write_general(self.cfg_dir, {"settings": {"reminder": "1 month"},
                                      LAST_REMINDER_FIELD: SAT.strftime(TIMESTAMP_FORMAT)})
        service, notifier, _, _ = _make_service(self.cfg_dir, SAT + datetime.timedelta(weeks=1))
        with mock.patch("core.general.reminder.acquire_lock") as lock:
            service.run_once()
        lock.assert_not_called()
        notifier.notify_reminder.assert_not_called()


if __name__ == "__main__":
    unittest.main()
