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
    time. A reminder delivered the morning after (because the first run after the slot
    landed there) still records the slot itself, and the supported cadences are whole-week
    counts, so ``slot + interval`` is itself a grid point. Send times can therefore never
    drift week over week.

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
    KEY_REMINDER, KEY_REMINDER_DAY, KEY_REMINDER_TIME,
    SPEC_REMINDER, SPEC_REMINDER_DAY, SPEC_REMINDER_TIME,
    display_reminder, general_config_path, time_parts, weekday_index, weeks_for,
    resolve_general_settings,
)
from core.locks import acquire_lock
from core.logger import get_target_logger, save_traceback
from core.scrapers.base.settings import STATUS_INVALID
from core.utils import check_for_updates

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

# The default grid anchor (Saturday 13:00), used when ``reminder_day`` / ``reminder_time``
# are unset. Kept in sync with DEFAULT_REMINDER_DAY / DEFAULT_REMINDER_TIME in settings.py.
_SLOT_WEEKDAY = 5  # Saturday (datetime.weekday(): Monday is 0)
_SLOT_HOUR = 13    # 13:00 local
_SLOT_MINUTE = 0


def _now_local() -> datetime.datetime:
    """Returns the current time as a naive *local* datetime.

    The reminder grid is anchored to the host's local wall clock (the user picks
    ``reminder_day`` / ``reminder_time`` in local time), so slots are computed and
    persisted in naive local time. The value is naive (no tzinfo) so it formats with
    TIMESTAMP_FORMAT and parses back without a timezone suffix. Naive wall-clock
    arithmetic keeps ``slot + N weeks`` on the same weekday/time across DST changes.
    """
    return datetime.datetime.now()


def most_recent_slot(now: datetime.datetime, weekday: int = _SLOT_WEEKDAY,
                     hour: int = _SLOT_HOUR, minute: int = _SLOT_MINUTE) -> datetime.datetime:
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
    result is itself a Saturday 13:00 - the grid is preserved without any snapping.

    Args:
        last_slot (datetime.datetime): The persisted slot of the last reminder.
        weeks (int): The cadence in whole weeks (see ``SUPPORTED_REMINDERS``).

    Returns:
        datetime.datetime: The first slot at/after which a reminder should be sent.
    """
    return last_slot + datetime.timedelta(weeks=weeks)


def _write_json_atomically(path: str, data: dict) -> None:
    """Writes ``data`` to ``path`` via a temp-file swap (mirrors the storage writer).

    Deliberately not the storage backend's ``_save_json_atomically``: that one is bound
    to a manager's filepath and raises ``StorageFileError`` (fatal for a scrape), while
    a reminder state write must degrade to log-and-skip. Raises ``OSError`` and lets
    the caller decide.
    """
    temp_path = path + ".tmp"
    with open(temp_path, mode="w") as file:
        json.dump(data, file, indent=2)
    os.replace(temp_path, path)


class ReminderService:
    """Sends the periodic liveness reminder, at most once per due grid slot.

    One instance handles one ``main.py`` invocation via :meth:`run_once`, which never
    raises - a reminder bug must not kill a scraping run, and it executes *before* the
    orchestrator so an aborted scrape cannot suppress the heartbeat either.

    The whole read-check-send-write sequence runs under the ``reminder`` file lock:
    concurrent plugin timers each invoke ``main.py``, and "delivery failed => do not
    advance the timestamp" makes the send part of the atomic decision. Contenders never
    block (the lock fails immediately) - they skip and let the holder finish.

    The collaborators a test needs to control are injected: the clock (``now_fn``), the
    update check (``update_check_fn``) and the notifier.
    """

    def __init__(self, config_dir: str, notifier: "Notifier",
                 now_fn: Callable[[], datetime.datetime] = _now_local,
                 update_check_fn: Callable[[], bool] = check_for_updates) -> None:
        """Initializes the service.

        Args:
            config_dir (str): The directory holding the config files.
            notifier (Notifier): The service used to send the reminder.
            now_fn (Callable): Returns the current naive-local time (test seam).
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
        ``logs/reminder/errors.txt`` and swallowed, so the scraping run proceeds.
        """
        try:
            self._run_once()
        except Exception:
            save_traceback(self._log, target_name=REMINDER_TARGET)

    def _run_once(self) -> None:
        """Resolves the settings, gates on "off", and runs the check under the lock."""
        resolved = resolve_general_settings(self.config_dir)
        if resolved.block_warning:
            self._log.warning(f"🟡 config/general.json: {resolved.block_warning}.")
        for spec in (SPEC_REMINDER, SPEC_REMINDER_DAY, SPEC_REMINDER_TIME):
            if resolved.status(spec.key) == STATUS_INVALID:
                self._log.warning(f"🟡 config/general.json: {spec.warning}")

        canonical = resolved.value(KEY_REMINDER)
        weeks = weeks_for(canonical)
        if weeks is None:  # "off": no lock, no state, no network
            return

        weekday = weekday_index(resolved.value(KEY_REMINDER_DAY))
        hour, minute = time_parts(resolved.value(KEY_REMINDER_TIME))

        try:
            with acquire_lock(REMINDER_TARGET, REMINDER_LOCK_FILENAME):
                self._check_and_send(weeks, canonical, weekday, hour, minute)
        except LockAcquisitionError:
            self._log.info("Reminder check skipped: another instance is handling it.")

    def _check_and_send(self, weeks: int, canonical: str,
                        weekday: int, hour: int, minute: int) -> None:
        """Performs one due-check and, when due, one send-then-persist (under the lock)."""
        now = self._now_fn()
        last_slot, was_corrupt = self._read_state_slot()

        if last_slot is None:
            # First run (or unusable state): anchor to the current grid slot and send
            # nothing - the first reminder arrives one full interval later.
            anchor = most_recent_slot(now, weekday, hour, minute)
            if not self._persist_slot(anchor):
                return
            first_due = next_due_slot(anchor, weeks).strftime(TIMESTAMP_FORMAT)
            if was_corrupt:
                self._log.warning(
                    f"🟡 Corrupted last_reminder timestamp! Re-anchored; "
                    f"next reminder due at {first_due} local time."
                )
            else:
                self._log.info(f"Reminder schedule initialized; first reminder due at {first_due} local time.")
            return

        if now < next_due_slot(last_slot, weeks):
            return  # not due: no write, no network

        # Due. The update check is done here - lazily, only when a reminder actually
        # goes out - because it shells out to `git ls-remote` (a network call).
        update_available = self._check_updates()

        # Persist the *slot*, never the send time: a late delivery records the scheduled
        # grid slot, keeping every future due moment on the grid. Re-deriving it from the
        # current config also re-aligns the grid after a reminder_day/time change.
        new_slot = most_recent_slot(now, weekday, hour, minute)
        next_due = next_due_slot(new_slot, weeks).strftime(TIMESTAMP_FORMAT)

        if not self.notifier.notify_reminder(update_available, display_reminder(canonical), next_due):
            self._log.warning(
                "🟡 Reminder delivery failed; timestamp not advanced - will retry on the next run."
            )
            return

        if self._persist_slot(new_slot):
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

    def _read_state_slot(self) -> tuple[datetime.datetime | None, bool]:
        """Reads the persisted last-reminder slot from ``general.json``.

        A missing/unreadable/corrupt file or an absent field is simply "no usable
        state" (the caller anchors and persists, which also repairs the file); a
        present but unparseable value is additionally flagged so the caller warns.

        Returns:
            tuple[datetime.datetime | None, bool]: ``(slot, was_corrupt)`` - the parsed
                slot or ``None``, and whether an unparseable value was found.
        """
        raw = None
        if os.path.isfile(self.config_path):
            try:
                with open(self.config_path, "r") as file:
                    data = json.load(file)
                if isinstance(data, dict):
                    raw = data.get(LAST_REMINDER_FIELD)
            except (OSError, json.JSONDecodeError):
                pass

        if raw is None:
            return None, False
        try:
            return datetime.datetime.strptime(str(raw), TIMESTAMP_FORMAT), False
        except ValueError:
            return None, True

    def _persist_slot(self, slot: datetime.datetime) -> bool:
        """Writes ``slot`` into the top-level state field, preserving user content.

        Reads the file fresh (absorbing any external edits), sets only
        ``last_reminder``, and rewrites atomically - the ``settings`` block and any
        unknown keys pass through untouched. A corrupt file is backed up to
        ``general.json.corrupt`` first and rebuilt around the state field (the
        storage backend's self-heal idiom). A write failure logs and reports False;
        it never raises into the run.

        Returns:
            bool: True when the state is on disk.
        """
        data: dict = {}
        if os.path.isfile(self.config_path):
            try:
                with open(self.config_path, "r") as file:
                    loaded = json.load(file)
            except (OSError, json.JSONDecodeError):
                loaded = None
            if isinstance(loaded, dict):
                data = loaded
            else:
                try:
                    shutil.copy2(self.config_path, self.config_path + ".corrupt")
                    self._log.warning(
                        "🟡 config/general.json is corrupted; backed it up to general.json.corrupt."
                    )
                except OSError:
                    pass  # best-effort backup; the rewrite below still repairs the file

        data[LAST_REMINDER_FIELD] = slot.strftime(TIMESTAMP_FORMAT)
        try:
            _write_json_atomically(self.config_path, data)
        except OSError as e:
            self._log.warning(f"🟡 Could not update config/general.json: {e}")
            return False
        return True
