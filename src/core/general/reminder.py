"""The periodic liveness reminder: slot arithmetic, persisted state, and dispatch.

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
    State is persisted *before* the notification is sent: a reminder is never delivered
    unless its slot has been recorded, so a persistent write failure cannot re-send the
    reminder on every subsequent run. If delivery then fails, the slot is rolled back so
    the next run retries rather than skipping a whole cadence.

State:
    ``last_reminder`` is a machine-written top-level field of ``config/general.json``,
    a sibling of the user-authored ``settings`` block (which is never written back) -
    the same split the scraper configs use for the products' ``last_checked``.
"""

import datetime
import json
import logging
import os
import shutil
from collections.abc import Callable
from typing import TYPE_CHECKING

from core.constants import TIMESTAMP_FORMAT
from core.exceptions import LockAcquisitionError
from core.general.settings import (
    KEY_REMINDER, KEY_REMINDER_DAY, KEY_REMINDER_TIME, GENERAL_SETTING_SPECS,
    general_config_path, resolve_general_settings,
)
from core.general.vocab import (
    DEFAULT_REMINDER_DAY, DEFAULT_REMINDER_TIME,
    display_reminder, time_parts, weekday_index, weeks_for,
)
from core.locks import acquire_lock
from core.logger import get_target_logger, save_traceback
from core.settings import STATUS_INVALID
from core.utils import check_for_updates, write_json_atomically

if TYPE_CHECKING:
    from core.notifier import Notifier


# Pseudo-target for the reminder's lock and logs (logs/reminder/), mirroring how each
# scraper target owns logs/<target>/.
REMINDER_TARGET = "reminder"

# The reminder's lock file within logs/reminder/. Named for what it guards - a liveness
# check, not a scrape - rather than the per-scraper "<target>_scraper_running.lock".
REMINDER_LOCK_FILENAME = "reminder_check.lock"

# The machine-written top-level field of general.json holding the grid slot of the last
# delivered reminder (or the anchor written on first run), in TIMESTAMP_FORMAT.
LAST_REMINDER_FIELD = "last_reminder"

# The default grid anchor, *derived* from the settings defaults so the two can never
# drift (the reminder grid and the settings panel agree on "Saturday 13:00" by
# construction). Used only as the default arguments of ``most_recent_slot`` for callers
# (e.g. unit tests) that do not pass an explicit schedule; the service always resolves
# and passes the configured weekday/time.
_DEFAULT_SLOT_WEEKDAY = weekday_index(DEFAULT_REMINDER_DAY)
_DEFAULT_SLOT_HOUR, _DEFAULT_SLOT_MINUTE = time_parts(DEFAULT_REMINDER_TIME)


def most_recent_slot(now: datetime.datetime, weekday: int = _DEFAULT_SLOT_WEEKDAY,
                     hour: int = _DEFAULT_SLOT_HOUR, minute: int = _DEFAULT_SLOT_MINUTE) -> datetime.datetime:
    """Returns the latest grid slot (the given weekday at the given time) at or before ``now``.

    Args:
        now (datetime.datetime): The current time, naive local.
        weekday (int): The grid weekday as ``datetime.weekday()`` (Monday is 0).
        hour (int): The grid hour (0-23).
        minute (int): The grid minute (0-59).

    Returns:
        datetime.datetime: The most recent matching weekday-at-time, <= ``now``.
    """
    days_back = (now.weekday() - weekday) % 7
    candidate = (now - datetime.timedelta(days=days_back)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    if candidate > now:  # it is the grid weekday, but before the grid time
        candidate -= datetime.timedelta(days=7)
    return candidate


def next_due_slot(last_slot: datetime.datetime, weeks: int) -> datetime.datetime:
    """Returns the grid slot at which the next reminder becomes due.

    Because ``last_slot`` is a grid point and the cadence is a whole-week count, the
    result is itself a grid point (the same weekday/time) - the grid is preserved
    without any snapping.

    Args:
        last_slot (datetime.datetime): The persisted slot of the last reminder.
        weeks (int): The cadence in whole weeks (see ``SUPPORTED_REMINDERS``).

    Returns:
        datetime.datetime: The first slot at/after which a reminder should be sent.
    """
    return last_slot + datetime.timedelta(weeks=weeks)


class ReminderService:
    """Sends the periodic liveness reminder, at most once per due grid slot.

    One instance handles one ``main.py`` invocation via :meth:`run_once`, which never
    raises - a reminder bug must not kill a scraping run, and it executes *before* the
    orchestrator so an aborted scrape cannot suppress the heartbeat either.

    The authoritative read-check-persist-send sequence runs under the ``reminder`` file
    lock: concurrent plugin timers each invoke ``main.py``, and the lock makes "record
    the slot, then send" one atomic decision. A cheap unlocked pre-check skips the lock
    (and creating ``logs/reminder/``) on the common "not due yet" path. Contenders never
    block (the lock fails immediately) - they skip and let the holder finish.

    The collaborators a test needs to control are injected: the clock (``now_fn``), the
    update check (``update_check_fn``) and the notifier.
    """

    def __init__(self, config_dir: str, notifier: "Notifier",
                 now_fn: Callable[[], datetime.datetime] = datetime.datetime.now,
                 update_check_fn: Callable[[], bool] = check_for_updates) -> None:
        """Initializes the service.

        Args:
            config_dir (str): The directory holding the config files.
            notifier (Notifier): The service used to send the reminder.
            now_fn (Callable): Returns the current time as a naive *local* datetime (the
                grid is anchored to the host's local wall clock). Defaults to
                ``datetime.datetime.now``; a test seam.
            update_check_fn (Callable): Returns whether an update is available; may
                raise (test seam; defaults to ``check_for_updates``).
        """
        self.config_dir = config_dir
        self.config_path = general_config_path(config_dir)
        self.notifier = notifier
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
        """Resolves the settings, gates on "off"/no-services, and runs the check under the lock."""
        resolved = resolve_general_settings(self.config_dir)
        if resolved.block_warning:
            self._log.warning(f"🟡 config/general.json: {resolved.block_warning}.")
        for spec in GENERAL_SETTING_SPECS:
            if resolved.status(spec.key) == STATUS_INVALID:
                self._log.warning(f"🟡 config/general.json: {spec.warning}")

        canonical = resolved.value(KEY_REMINDER)
        weeks = weeks_for(canonical)
        if weeks is None:  # "off": no lock, no state, no network
            return

        # No configured notification target means the reminder can never be delivered;
        # skip entirely rather than "fail delivery" on every run (which would also run
        # the network update check each time and never advance the schedule).
        if not self.notifier.has_services:
            self._log.info("Reminder skipped: no notification services configured.")
            return

        weekday = weekday_index(resolved.value(KEY_REMINDER_DAY))
        hour, minute = time_parts(resolved.value(KEY_REMINDER_TIME))

        # Cheap unlocked pre-check: the overwhelmingly common outcome is "not due yet".
        # Only pay for the lock (and creating logs/reminder/) when a send is plausible;
        # the authoritative check re-reads and re-decides under the lock.
        if not self._maybe_due(weeks):
            return

        try:
            with acquire_lock(REMINDER_TARGET, REMINDER_LOCK_FILENAME):
                self._check_and_send(weeks, canonical, weekday, hour, minute)
        except LockAcquisitionError:
            self._log.info("Reminder check skipped: another instance is handling it.")

    def _maybe_due(self, weeks: int) -> bool:
        """Unlocked heuristic: ``False`` only when we can cheaply prove no reminder is due.

        Anything needing repair (first run, unreadable/corrupt state, a future timestamp)
        returns ``True`` so it is resolved under the lock; a readable, in-the-past slot is
        due only once ``now`` reaches its next grid slot.
        """
        _, last_slot, problem = self._read_state()
        if problem is not None or last_slot is None:
            return True
        now = self._now_fn()
        return last_slot > now or now >= next_due_slot(last_slot, weeks)

    def _check_and_send(self, weeks: int, canonical: str,
                        weekday: int, hour: int, minute: int) -> None:
        """Performs one due-check and, when due, one persist-then-send (under the lock)."""
        now = self._now_fn()
        data, last_slot, problem = self._read_state()

        if problem == "unreadable":
            # An OSError reading an existing file (permissions, transient I/O): we can
            # neither trust nor safely rewrite it (that would clobber the user's settings
            # with a state-only file), so skip this run and retry on the next.
            self._log.warning(
                "🟡 config/general.json is unreadable; skipping this reminder check (will retry next run)."
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
            if not self._persist_slot(data, anchor):
                return
            first_due = next_due_slot(anchor, weeks).strftime(TIMESTAMP_FORMAT)
            if problem == "corrupt":
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
                self._log.info(f"Reminder schedule initialized; first reminder due at {first_due} local time.")
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
        next_due = next_due_slot(new_slot, weeks).strftime(TIMESTAMP_FORMAT)

        # Persist *before* sending: a reminder must never go out unless its slot is on
        # disk, or a persistent write failure would re-send it on every subsequent run.
        if not self._persist_slot(data, new_slot):
            self._log.warning(
                "🟡 Could not record reminder state; skipping this send (will retry next run)."
            )
            return

        if not self.notifier.notify_reminder(update_available, display_reminder(canonical), next_due):
            # Delivery failed: roll the slot back so the next run retries instead of
            # skipping a whole cadence. Best-effort - a failed rollback only costs this
            # one cycle.
            self._persist_slot(data, last_slot)
            self._log.warning(
                "🟡 Reminder delivery failed; timestamp restored - will retry on the next run."
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

    def _read_state(self) -> tuple[dict | None, datetime.datetime | None, str | None]:
        """Reads ``general.json`` once, returning ``(data, last_slot, problem)``.

        This is the single reader for the reminder state, so the due-check and the
        write-back share one file read under the lock.

        Returns:
            tuple: ``data`` is the parsed dict (mutated and rewritten by
                :meth:`_persist_slot`), ``{}`` when the file is absent, or ``None`` when
                it is unreadable/corrupt (the writer rebuilds around it). ``last_slot`` is
                the parsed ``last_reminder`` grid slot, or ``None``. ``problem`` is
                ``None``; ``"unreadable"`` (an ``OSError`` - do not rewrite); or
                ``"corrupt"`` (an unparseable file or timestamp - re-anchor, backing up a
                corrupt file).
        """
        if not os.path.isfile(self.config_path):
            return {}, None, None
        try:
            with open(self.config_path, "r") as file:
                loaded = json.load(file)
        except OSError:
            return None, None, "unreadable"
        except json.JSONDecodeError:
            return None, None, "corrupt"

        if not isinstance(loaded, dict):
            return None, None, "corrupt"
        raw = loaded.get(LAST_REMINDER_FIELD)
        if raw is None:
            return loaded, None, None
        try:
            return loaded, datetime.datetime.strptime(str(raw), TIMESTAMP_FORMAT), None
        except (ValueError, TypeError):
            return loaded, None, "corrupt"

    def _persist_slot(self, data: dict | None, slot: datetime.datetime) -> bool:
        """Writes ``slot`` into the top-level state field and rewrites the file atomically.

        ``data`` is the already-parsed file contents from :meth:`_read_state` (mutated in
        place and rewritten, so the ``settings`` block and any unknown keys pass through
        untouched). ``None`` means the file was corrupt or not an object: it is backed up
        to ``general.json.corrupt`` and rebuilt around the state field (the storage
        backend's self-heal idiom). A write failure logs and reports False; it never
        raises into the run.

        Returns:
            bool: True when the state is on disk.
        """
        if data is None:
            data = {}
            if os.path.isfile(self.config_path):
                try:
                    shutil.copy2(self.config_path, self.config_path + ".corrupt")
                    self._log.warning(
                        "🟡 config/general.json is corrupted; backed it up to general.json.corrupt."
                    )
                except OSError:
                    pass  # best-effort backup; the rewrite below still repairs the file

        data[LAST_REMINDER_FIELD] = slot.strftime(TIMESTAMP_FORMAT)
        try:
            write_json_atomically(self.config_path, data)
        except OSError as e:
            self._log.warning(f"🟡 Could not update config/general.json: {e}")
            return False
        return True
