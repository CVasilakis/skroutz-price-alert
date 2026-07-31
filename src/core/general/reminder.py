"""Periodic liveness-reminder policy, recovery, and dispatch orchestration.

Pure slot arithmetic lives in :mod:`core.general.reminder_schedule`; schema-versioned
persistence lives in :mod:`core.general.reminder_state`. This module coordinates those
boundaries with locking, update inspection, logging, and notification delivery.

The reminder tells the user the scrapers are still running in the background. Because
the app is one-shot (systemd timers fire per-plugin runs), delivery happens on the
first run at/after a due moment - the check itself rides every ``main.py`` invocation
(see the hook in ``main``), once per process, not per scraper.

Schedule:
    Due moments live on a weekly grid anchored to a weekday and time the user picks
    (``reminder_day`` / ``reminder_time``, default Saturday 13:00). Those are read in the
    host's **local** time, so the grid is naive local wall clock - unlike the UTC used
    for the scrapers' ``last_checked``. Naive wall-clock arithmetic keeps a slot on the
    same weekday/time across DST changes (delivery is approximate by design, so being an
    hour off around a DST switch is immaterial).

Anti-sliding by construction:
    The persisted ``last_reminder`` is always the *scheduled slot*, never the actual send
    time, and never a moment in the future. A reminder delivered the morning after (the
    first run after the slot landed there) still records the slot itself, and the
    supported cadences are whole-week counts, so ``slot + interval`` is itself a grid
    point. When ``reminder_day`` / ``reminder_time`` change mid-cycle, the recorded slot
    is clamped so it can never move backwards (see ``_check_and_send``), so a reschedule
    cannot squeeze a second reminder into the same cadence window. Send times therefore
    never drift.

Delivery safety:
    For an established schedule, state advances only *after* notification delivery returns
    success. A false return or exception therefore leaves the old slot intact and the next
    invocation retries. If delivery succeeds but persistence fails, the old slot remains
    and a later invocation may deliver a duplicate; this deliberate at-least-once policy
    never marks an undelivered reminder as sent.

State:
    ``last_reminder`` is a machine-written field of ``state/general.json``. The
    user-authored ``config/general.json`` is never written at runtime.
"""

import datetime
import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from core.exceptions import LockAcquisitionError
from core.general.reminder_schedule import most_recent_slot, next_due_slot
from core.general.reminder_state import (
    ReminderStatePreservationError,
    ReminderStateProblem,
    ReminderStateRepository,
    ReminderStateSnapshot,
    ReminderStateWriteError,
)
from core.general.settings import (
    GENERAL_SETTING_SPECS,
    SPEC_REMINDER,
    SPEC_REMINDER_DAY,
    SPEC_REMINDER_TIME,
)
from core.general.vocab import display_reminder, time_parts, weekday_index, weeks_for
from core.infrastructure.logging import get_target_logger, save_traceback
from core.infrastructure.updates import check_for_updates
from core.notifications.contracts import NotificationService
from core.settings import ResolvedSettings, SettingStatus

# Pseudo-target for the reminder's state/locks/reminder.lock and logs/reminder/ output.
REMINDER_TARGET = "reminder"

# Human-readable local timestamp used in reminder logs and notification bodies.
REMINDER_DISPLAY_FORMAT = "%d-%m-%Y %H:%M:%S"


class ReminderService:
    """Sends the periodic liveness reminder, at most once per due grid slot.

    One instance handles one ``main.py`` invocation via :meth:`run_once`, which never
    raises - a reminder bug must not kill a scraping run, and it executes *before* the
    orchestrator so an aborted scrape cannot suppress the heartbeat either.

    The authoritative read-check-send-persist sequence runs under the ``reminder`` file
    lock: concurrent plugin timers each invoke ``main.py``, and the lock makes "send,
    then record the slot" one serialized decision. A cheap unlocked pre-check skips the lock
    on the common "not due yet" path. Contenders never block (the lock fails immediately) -
    they skip and let the holder finish.

    The collaborators a test needs to control are injected: the clock (``now_fn``), the
    update check (``update_check_fn``) and the notifier.
    """

    def __init__(
        self,
        settings: ResolvedSettings | None,
        state: ReminderStateRepository,
        notifier: NotificationService,
        *,
        acquire_lock_fn: Callable[[str], AbstractContextManager[Any]],
        settings_error: str | None = None,
        now_fn: Callable[[], datetime.datetime] = datetime.datetime.now,
        update_check_fn: Callable[[], bool] = check_for_updates,
    ) -> None:
        """Initializes the service.

        Args:
            settings (ResolvedSettings | None): The already-resolved general settings.
            state (ReminderStateRepository): Machine-owned reminder persistence.
            notifier (NotificationService): The service used to send the reminder.
            acquire_lock_fn (Callable): Acquires a named lock from this state root.
            settings_error (str | None): A redacted settings-load failure that disables
                only the reminder.
            now_fn (Callable): Returns the current time as a naive *local* datetime (the
                grid is anchored to the host's local wall clock). Defaults to
                ``datetime.datetime.now``; a test seam.
            update_check_fn (Callable): Returns whether an update is available; may
                raise (test seam; defaults to ``check_for_updates``).
        """
        self.settings = settings
        self.settings_error = settings_error
        self.state = state
        self.notifier = notifier
        self.acquire_lock_fn = acquire_lock_fn
        self._now_fn = now_fn
        self._update_check_fn = update_check_fn
        self._logger: logging.Logger | None = None

    @property
    def _log(self) -> logging.Logger:
        """The reminder's logger, created lazily and always file-backed.

        The reminder rides every ``main.py`` invocation, including interactive runs whose
        output is a sequence of Rich panels. Its diagnostics therefore always go to its
        own file log (``logs/reminder/output.log``) and *never* to the shared console -
        a raw log line printed to the terminal mid-run would break the panel layout, and
        an invalid reminder value is already surfaced on the Configuration Check panel.
        So the logger is requested in file mode (``quiet=True``) regardless of run mode
        (``propagate=False``, no console handler).

        Instantiating the file handler creates ``logs/reminder/`` on disk; a run with the
        reminder off (which logs nothing) should leave no trace, so creation is deferred
        until a message is actually emitted.
        """
        if self._logger is None:
            self._logger = get_target_logger(REMINDER_TARGET, quiet=True)
        return self._logger

    def run_once(self) -> None:
        """Runs one reminder check for this invocation; never raises.

        Any unexpected failure is logged with a full traceback to
        ``logs/reminder/errors.txt`` and swallowed, so the scraping run proceeds. Even
        the traceback write is guarded: if the log directory itself is unwritable, the
        failure is dropped rather than allowed to escape into ``main``.
        """
        try:
            self._run_once()
        except Exception:
            try:
                save_traceback(self._log, target_name=REMINDER_TARGET)
            except Exception:
                pass  # last resort: never let reminder diagnostics abort the run

    def _run_once(self) -> None:
        """Gate on the injected settings and run the reminder check under the lock."""
        if self.settings is None:
            detail = self.settings_error or "general settings could not be resolved"
            self._log.warning(f"🟡 config/general.json: {detail}; reminder skipped.")
            return

        resolved = self.settings
        for spec in GENERAL_SETTING_SPECS:
            if resolved.status(spec) is SettingStatus.INVALID:
                self._log.warning(f"🟡 config/general.json: {spec.invalid_warning}")

        canonical = resolved[SPEC_REMINDER]
        weeks = weeks_for(canonical)
        if weeks is None:  # "off": no lock, no state, no network
            return

        # No configured notification target means the reminder can never be delivered;
        # skip entirely rather than "fail delivery" on every run (which would also run
        # the network update check each time and never advance the schedule).
        if not self.notifier.has_services:
            self._log.info("Reminder skipped: no notification services configured.")
            return

        weekday = weekday_index(resolved[SPEC_REMINDER_DAY])
        hour, minute = time_parts(resolved[SPEC_REMINDER_TIME])

        # Cheap unlocked pre-check: the overwhelmingly common outcome is "not due yet".
        # Only pay for the lock when a send is plausible;
        # the authoritative check re-reads and re-decides under the lock.
        if not self._maybe_due(weeks):
            return

        try:
            with self.acquire_lock_fn(REMINDER_TARGET):
                self._check_and_send(weeks, canonical, weekday, hour, minute)
        except LockAcquisitionError:
            self._log.info("Reminder check skipped: another instance is handling it.")

    def _maybe_due(self, weeks: int) -> bool:
        """Unlocked heuristic: ``False`` only when we can cheaply prove no reminder is due.

        Anything needing repair (first run, unreadable/corrupt state, a future timestamp)
        returns ``True`` so it is resolved under the lock; a readable, in-the-past slot is
        due only once ``now`` reaches its next grid slot.
        """
        snapshot = self.state.load()
        if snapshot.problem is not None or snapshot.last_slot is None:
            return True
        now = self._now_fn()
        return snapshot.last_slot > now or now >= next_due_slot(snapshot.last_slot, weeks)

    def _check_and_send(
        self, weeks: int, canonical: str, weekday: int, hour: int, minute: int
    ) -> None:
        """Performs one due-check and, when due, one send-then-persist (under the lock)."""
        now = self._now_fn()
        snapshot = self.state.load()
        last_slot = snapshot.last_slot

        if snapshot.problem is ReminderStateProblem.UNREADABLE:
            # An OSError reading an existing file (permissions, transient I/O): we can
            # neither trust nor safely rewrite it, so skip and retry on the next run.
            self._log.warning(
                "🟡 state/general.json is unreadable; skipping this reminder check (will retry next run)."
            )
            return

        # A last_reminder in the future (host clock moved back, a restored backup, a manual
        # edit) would otherwise mute reminders until that moment passes - up to a full
        # cadence. Treat it as unusable and re-anchor to the current grid.
        future = last_slot is not None and last_slot > now
        if future:
            last_slot = None

        if last_slot is None:
            # First run (or unusable state): anchor to the current grid slot and send
            # nothing - the first reminder arrives one full interval later.
            anchor = most_recent_slot(now, weekday, hour, minute)
            if not self._save_slot(snapshot, anchor):
                return
            first_due = next_due_slot(anchor, weeks).strftime(REMINDER_DISPLAY_FORMAT)
            if snapshot.problem is ReminderStateProblem.INVALID_TIMESTAMP:
                self._log.warning(
                    f"🟡 Corrupted last_reminder timestamp! Re-anchored; "
                    f"next reminder due at {first_due} local time."
                )
            elif future:
                self._log.warning(
                    f"🟡 last_reminder is in the future (clock change or manual edit); re-anchored. "
                    f"Next reminder due at {first_due} local time."
                )
            else:
                self._log.info(
                    f"Reminder schedule initialized; first reminder due at {first_due} local time."
                )
            return

        due_slot = next_due_slot(last_slot, weeks)
        if now < due_slot:
            return  # not due: no write, no network

        # Due. The update check is done here - lazily, only when a reminder actually
        # goes out - because it shells out to `git ls-remote` (a network call).
        update_available = self._check_updates()

        # Record the grid slot for this delivery. Take the later of "the most recent slot
        # on the current grid" and "the slot that made us due", so a mid-cycle
        # reminder_day/time change can never move the recorded slot backwards and trigger
        # a second reminder within the same cadence window. Both are <= now, so this never
        # persists a future slot.
        new_slot = max(most_recent_slot(now, weekday, hour, minute), due_slot)
        next_due = next_due_slot(new_slot, weeks).strftime(REMINDER_DISPLAY_FORMAT)

        try:
            delivered = bool(
                self.notifier.notify_reminder(
                    update_available,
                    display_reminder(canonical),
                    next_due,
                )
            )
        except Exception:
            # A notifier bug/transport exception must obey the same state invariant as
            # a normal False result: leave last_reminder untouched so the next run retries.
            try:
                save_traceback(self._log, target_name=REMINDER_TARGET)
            except Exception:
                pass
            self._log.warning(
                "🟡 Reminder delivery raised an exception; timestamp unchanged - "
                "will retry on the next run."
            )
            return

        if not delivered:
            self._log.warning(
                "🟡 Reminder delivery failed; timestamp unchanged - will retry on the next run."
            )
            return

        # Only a confirmed delivery may advance an established last_reminder. If this
        # write fails, keeping the old slot can cause a duplicate next run, but never a
        # false record claiming that an undelivered reminder was sent.
        if not self._save_slot(snapshot, new_slot):
            self._log.warning(
                "🟡 Reminder was delivered but its timestamp could not be recorded; "
                "the next run may deliver it again."
            )
            return

        self._log.info(f"✅ Reminder sent. Next reminder due at {next_due} local time.")

    def _check_updates(self) -> bool | None:
        """Returns whether a project update is available, or ``None`` when unknown.

        Any failure (network, git, an unexpected bug in the check) degrades to
        ``None`` - the reminder still goes out, reporting the check as inconclusive.
        """
        try:
            return bool(self._update_check_fn())
        except Exception:
            self._log.warning("🟡 Update check failed; reminder will report it as inconclusive.")
            return None

    def _save_slot(self, snapshot: ReminderStateSnapshot, slot: datetime.datetime) -> bool:
        """Persist a slot while translating repository failures into run diagnostics."""
        try:
            self.state.save(snapshot, slot)
        except ReminderStatePreservationError:
            self._log.warning("🟡 state/general.json is malformed; refusing to overwrite it.")
            return False
        except ReminderStateWriteError as exc:
            self._log.warning(f"🟡 Could not update state/general.json: {exc}")
            return False
        return True
