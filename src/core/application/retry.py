"""Retry and terminal-error policies for item execution.

The one place that decides what each modeled failure costs. ``ItemExecutor``
applies these policies; it does not contain a single ``isinstance`` check of its
own, so adding or reclassifying a failure means editing the table below rather
than the execution loop.

Each policy answers four independent questions, and they genuinely are
independent — a failure can be retried without being reported, reported without
changing the exit status, or stop the target while still being reported:

1. should the client reset itself between attempts?
2. does an exhausted item stop the rest of the target?
3. does the user hear about it in the scraping-errors notification?
4. which process exit status, if any, does it raise?
"""

from __future__ import annotations

from dataclasses import dataclass

from core import messages
from core.exceptions import (
    InvalidURLError,
    PriceUnavailableError,
    RateLimitError,
    ResourceNotFoundError,
    ScraperError,
    ScraperParseError,
    ServerError,
)
from core.exit_status import ExitStatus

SKIP_ERRORS = (ResourceNotFoundError, PriceUnavailableError, InvalidURLError)
"""Failures that end an item immediately, without consuming its retry budget.

These describe a stable fact about the resource rather than a transient fault:
the page is gone, it carries no price, or the input cannot address it. Retrying
would re-fetch the same answer and spend the pacing delay for nothing. They are
matched before the retry table, so they never reach :func:`policy_for`.
"""

ERRORS_LOG_TOKEN = "<errors_log>"
"""Placeholder in :attr:`ErrorPolicy.extra_notes` for the error-log pointer.

The note text needs the target name, which this module has no access to.
``ItemExecutor`` substitutes the real ``logs/<target>/errors.txt`` wording when it
renders the final failure, keeping the policy table free of run-time context.
"""


@dataclass(frozen=True)
class ErrorPolicy:
    """What one class of failure costs an item, its target, and the process.

    The defaults describe an ordinary retryable scraping fault: reset the client
    between attempts, keep going after it, tell the user, and leave the exit
    status alone.
    """

    prepare_before_retry: bool = True
    """Call the client's ``prepare_retry()`` between attempts.

    Turned off only when resetting cannot help, which would waste the reset and
    discard a healthy session for nothing.
    """

    abort: bool = False
    """Skip the target's remaining items once this failure exhausts its attempts.

    Reserved for evidence that the host is refusing traffic: continuing would keep
    requesting from a server that has already said no.
    """

    counts_as_failure: bool = True
    """Include the item in the scraping-errors notification.

    Turned off for conditions the user cannot act on and did not cause, so a
    remote outage does not produce an alert per item on every timer firing. Such
    a run stays visible: the failure is still shown and logged, and an outage that
    persists surfaces through the stale-tracking notification.
    """

    exit_status: ExitStatus | None = None
    """The process status this failure raises, or ``None`` to leave it unchanged.

    ``None`` means "visible but not a process-level failure" — the run may still
    exit ``0`` if nothing else went wrong.
    """

    save_traceback: bool = False
    """Write a full traceback to ``logs/<target>/errors.txt``.

    Reserved for failures whose message is not self-explanatory. A parse error
    already names what could not be read, so a traceback would add noise to the
    log a user is meant to read.
    """

    extra_notes: tuple[str, ...] = ()
    """Extra footnotes on the exhausted item's row, ``ERRORS_LOG_TOKEN`` included."""


DEFAULT_POLICY = ErrorPolicy(
    exit_status=ExitStatus.SCRAPE_ERROR,
    save_traceback=True,
    extra_notes=(ERRORS_LOG_TOKEN,),
)
"""The policy for anything a plugin did not model: a bug, not a scraping outcome.

Applied to every exception outside the ``ScraperError`` hierarchy — a
``KeyError`` in a parser, a transport library raising its own type. It is the
loudest policy on purpose: unexpected faults raise an exit status and always keep
a traceback, because nothing else explains them.
"""

RETRY_POLICIES: tuple[tuple[type[Exception], ErrorPolicy], ...] = (
    # Ordered most specific first: policy_for takes the first isinstance match, so
    # the ScraperError base case must stay last or it would shadow its subclasses.
    (
        RateLimitError,
        ErrorPolicy(
            abort=True,
            exit_status=ExitStatus.RATE_LIMIT_ERROR,
            save_traceback=True,
            extra_notes=(messages.NOTE_RATE_LIMIT_ABORTED, ERRORS_LOG_TOKEN),
        ),
    ),
    # A remote 5xx is the far side's problem: rotating this end's identity cannot
    # fix it, and alerting per item would turn one outage into recurring noise.
    (ServerError, ErrorPolicy(prepare_before_retry=False, counts_as_failure=False)),
    # The store changed its markup or payload. The message names what could not be
    # read, so it raises a status without also writing a traceback.
    (ScraperParseError, ErrorPolicy(exit_status=ExitStatus.SCRAPE_ERROR)),
    # Any other modeled failure: reported and kept, but not a process-level failure,
    # since a plugin raised it deliberately for a condition it understood.
    (ScraperError, ErrorPolicy(save_traceback=True, extra_notes=(ERRORS_LOG_TOKEN,))),
)


def policy_for(exc: Exception) -> ErrorPolicy:
    """Return the policy for one raised failure, most specific match first.

    Args:
        exc: The exception an attempt raised. Skip errors never reach here; the
            executor matches :data:`SKIP_ERRORS` before consulting the table.

    Returns:
        The first matching policy, or :data:`DEFAULT_POLICY` for anything outside
        the modeled ``ScraperError`` hierarchy.
    """
    return next(
        (policy for exc_type, policy in RETRY_POLICIES if isinstance(exc, exc_type)),
        DEFAULT_POLICY,
    )


__all__ = ["ERRORS_LOG_TOKEN", "ErrorPolicy", "SKIP_ERRORS", "policy_for"]
