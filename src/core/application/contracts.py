"""Presentation-neutral contracts and outcomes for one scraping run.

The vocabulary the application layer speaks to whatever is rendering it. Nothing
here imports Rich, touches the filesystem, or knows whether a run is interactive:
the orchestrator emits these values, and a frontend (the Rich reporter, the silent
background reporter, the UI snapshot harness) decides how they look.

Two policies live here rather than in a frontend, because both must hold no matter
who is watching: how per-target conditions merge into one process exit status
(:meth:`RunOutcome.exit_status`), and what an item's execution produced
(:class:`ItemRunOutcome`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from core.exit_status import ExitStatus
from core.scrapers.api import TrackedItem
from core.settings import ResolvedSettings

Notes = str | list[str] | None
"""Zero, one, or several footnotes on a row. ``None`` and ``[]`` both mean none,
so callers may pass an optional value straight through without normalizing it."""


class PriceOutcome(Enum):
    """How one successful check compared against the item's target price.

    Success only: a failed check never produces one of these. The distinction
    exists so a frontend can pick an icon and color without re-deriving the
    comparison, and so "no threshold set" and "no advert matched" never look like
    an ordinary in-budget result.
    """

    DROP = "drop"
    """Below target. The alert-worthy outcome; a notification was attempted."""

    NO_TARGET = "no_target"
    """Priced, but the row set ``target_price`` to 0 to monitor without alerting."""

    OK = "ok"
    """Priced at or above the target. The ordinary steady state."""

    NO_MATCH = "no_match"
    """A listing check that completed and matched nothing. A success, not a failure."""


@dataclass(frozen=True)
class ConfigOutcome:
    """One target's configuration health, as the 'Config' row shows it.

    Carries the counts and the already-formatted failure text, never the config
    document, so a frontend renders this row without reading or re-validating a
    file.
    """

    loaded_count: int
    """How many item rows decoded successfully and will be checked."""

    faulty_indices: tuple[int, ...] = ()
    """1-based positions of rows that failed to decode; those rows are skipped."""

    error: str | None = None
    """Presentation-safe reason the whole config failed to load, if it did.

    Set only for a target-level failure (missing file, bad JSON, wrong schema),
    which is mutually exclusive with having decoded any rows.
    """

    source_path: str | None = None
    """The display path (``config/<target>.json``) named in the failure wording."""

    diagnostic_saved: bool | None = None
    """Whether the technical detail reached the error log: ``True`` saved,
    ``False`` the write itself failed (the user is told), ``None`` nothing to save."""


@dataclass(frozen=True)
class ItemRunOutcome:
    """Everything one item's execution contributes back to the run.

    Deliberately four separate signals rather than one verdict: an item can fail
    without being reported to the user, be reported without changing the exit
    status, or stop the target while still being reported. Collapsing them would
    force a single severity scale onto conditions that are not comparable.
    """

    item: TrackedItem
    """The item that was executed, echoed back so the caller need not track order."""

    reported_error: Exception | None = None
    """The failure to list in the scraping-errors notification, when the user
    should hear about it. ``None`` for a clean run and for failures deliberately
    kept quiet, such as a remote 5xx or a resource that no longer exists."""

    statuses: frozenset[ExitStatus] = frozenset()
    """Exit conditions this item raised. A set because one item can raise more
    than one, and because the run merges every item's conditions before choosing
    a single status."""

    abort_target: bool = False
    """Stop the target without executing its remaining items. Set only when
    continuing would keep hitting a host that is refusing traffic."""


@dataclass
class RunOutcome:
    """Mergeable run conditions and the policy for selecting one process status.

    A run accumulates conditions from every target and then reduces them to the
    single integer a process may exit with. Keeping the reduction here is what
    lets a new exit status be one entry in the priority list below, instead of
    another branch in the orchestrator and another in the status UI.
    """

    statuses: set[ExitStatus] = field(default_factory=set)
    """Every condition raised anywhere in the run, unordered and de-duplicated."""

    skipped_count: int = 0
    """How many targets never started because another instance held their lock."""

    def merge(self, other: RunOutcome) -> None:
        """Fold one target's outcome into the run's, keeping every condition."""
        self.statuses.update(other.statuses)
        self.skipped_count += other.skipped_count

    def exit_status(self, *, interrupted: bool, target_count: int) -> ExitStatus:
        """Reduce every accumulated condition to the one status the process exits with.

        The priority list is ordered by **what the user has to fix first**, not by
        how much of the run each condition spoiled. A broken installation or an
        unreadable configuration blocks everything and comes first; storage and
        dependency faults block one target; only then the scrape outcomes. Within
        those, a parse fault outranks a rate limit because it needs a code change
        while a rate limit is transient and usually clears by the next timer
        firing. A failed notification is last: the scraping itself succeeded.

        Interruption wins outright. A user who pressed Ctrl+C, or a system that
        sent SIGTERM, is told the run was interrupted rather than being handed the
        diagnosis of whatever the run had found up to that point.

        Args:
            interrupted: Whether the run was cut short by a signal.
            target_count: How many targets this invocation selected, which decides
                whether lock skips describe the whole run (see below).

        Returns:
            The single status to exit with; ``SUCCESS`` when nothing was raised.

        Note:
            ``ALREADY_RUNNING`` is returned only when *every* selected target was
            lock-skipped. This reads as all-or-nothing but matches how the app
            actually runs unattended: systemd invokes one scraper per timer, so a
            background run selects exactly one target and a lock skip is by
            definition the whole run, correctly surfacing 42 to systemd. The
            all-or-nothing condition only ever softens a *manual* multi-target
            run, where reporting a failure because one of several targets was
            momentarily locked would be a false alarm about an otherwise
            successful run.
        """
        if interrupted:
            return ExitStatus.INTERRUPTED
        for status in (
            ExitStatus.APPLICATION_ERROR,
            ExitStatus.TARGET_CONFIG_ERROR,
            ExitStatus.NOTIFICATION_CONFIG_ERROR,
            ExitStatus.STORAGE_ERROR,
            ExitStatus.PLUGIN_DEPENDENCY_ERROR,
            ExitStatus.SCRAPE_ERROR,
            ExitStatus.RATE_LIMIT_ERROR,
            ExitStatus.NOTIFICATION_ERROR,
        ):
            if status in self.statuses:
                return status
        if self.skipped_count > 0 and self.skipped_count == target_count:
            return ExitStatus.ALREADY_RUNNING
        return ExitStatus.SUCCESS


class RunReporter(Protocol):
    """Core reporting protocol implemented by interactive and silent frontends.

    Every observable thing a run does passes through here. The application never
    prints, so a frontend that implements this protocol sees the complete run:
    ``InteractiveRunReporter`` draws a live Rich panel, ``SilentRunReporter``
    writes ``logs/<target>/output.log`` for systemd, and the UI snapshot harness
    drives both to pin their output.

    Call order for one target::

        start_target                     once, before any item
          start_scraping / complete_scraping   around each attempt's request
          log_attempt                    a failed attempt that will be retried
          start_sleep/update_sleep/complete_sleep   pacing between requests
          log_result / log_price_result / log_warning / log_error / log_failure
                                         exactly one per item, its final say
        complete_target                  once, settling the panel's border color

    Methods return nothing and must not raise: a reporter is a passive observer,
    and a frontend fault must never change what a run does. Implementations may
    ignore any call they have no use for — the silent reporter discards the
    spinner and sleep events, which have no meaning in a log file.
    """

    def start_target(
        self,
        target_name: str,
        target_logger: logging.Logger,
        settings: ResolvedSettings,
        config: ConfigOutcome,
    ) -> None:
        """Begin one target: its name, log sink, resolved settings, and config health."""

    def start_scraping(self, name: str, attempt: int = 1, max_retries: int = 1) -> None:
        """Show that one item's request is in flight (attempt N of ``max_retries``)."""

    def complete_scraping(self) -> None:
        """End the in-flight state. Always paired with :meth:`start_scraping`, including
        when the request raised."""

    def log_result(
        self, icon: str, name: str, value: str, notes: Notes = None, attempt_notes: Notes = None
    ) -> None:
        """Record one item's final non-price outcome with a caller-chosen icon and value."""

    def log_price_result(
        self,
        name: str,
        price: float | None,
        currency: str,
        target: float,
        outcome: PriceOutcome,
        notes: Notes = None,
        attempt_notes: Notes = None,
        delivery_failed: bool = False,
    ) -> None:
        """Record one item's final priced outcome.

        ``price`` is ``None`` only for a listing check that matched nothing.
        ``delivery_failed`` reports that the alert itself could not be sent, which
        is presented distinctly from the price comparison.
        """

    def log_warning(
        self, name: str, warning_str: str, notes: Notes = None, attempt_notes: Notes = None
    ) -> None:
        """Record an item ending in a recoverable condition the user should see."""

    def log_error(
        self, name: str, error_str: str, notes: Notes = None, attempt_notes: Notes = None
    ) -> None:
        """Record an item ending in a failure attributable to that item."""

    def log_system_error(self, error_str: str) -> None:
        """Record a target-level fault that is not attributable to any one item."""

    def log_storage_error(self, summary: str, details: Notes = None) -> None:
        """Record a state or lock failure, whose detail is presentation-safe already."""

    def log_attempt(self, name: str, attempt: int, max_retries: int, detail: str) -> None:
        """Note one failed attempt that will be retried. Not an item's final say."""

    def log_failure(
        self, name: str, error_type: str, attempt_notes: Notes = None, extra_notes: Notes = None
    ) -> None:
        """Record an item that exhausted every attempt, with its per-attempt history."""

    def start_sleep(self, total_delay: float, retry_attempt: int = 0, max_retries: int = 0) -> None:
        """Begin an interruptible wait. Nonzero ``retry_attempt`` marks retry backoff
        rather than ordinary pacing between items."""

    def update_sleep(self, remaining: float) -> None:
        """Report the remaining wait, called repeatedly while sleeping."""

    def complete_sleep(self, actual_delay: float) -> None:
        """End a wait, reporting how long it actually took."""

    def complete_target(self) -> None:
        """Finish one target, settling any state that depends on the whole run."""

    def log_interrupt(self, message: str) -> None:
        """Record that a signal ended the run early."""


__all__ = [
    "ConfigOutcome",
    "ItemRunOutcome",
    "Notes",
    "PriceOutcome",
    "RunOutcome",
    "RunReporter",
]
