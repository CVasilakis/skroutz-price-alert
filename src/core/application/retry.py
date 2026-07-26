"""Retry and terminal-error policies for item execution."""

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

SKIP_ERRORS = (ResourceNotFoundError, PriceUnavailableError, InvalidURLError)
ERRORS_LOG_TOKEN = "<errors_log>"


@dataclass(frozen=True)
class ErrorPolicy:
    prepare_before_retry: bool = True
    abort: bool = False
    counts_as_failure: bool = True
    affects_exit_status: bool = False
    save_traceback: bool = False
    extra_notes: tuple[str, ...] = ()


DEFAULT_POLICY = ErrorPolicy(
    affects_exit_status=True,
    save_traceback=True,
    extra_notes=(ERRORS_LOG_TOKEN,),
)

RETRY_POLICIES: tuple[tuple[type[Exception], ErrorPolicy], ...] = (
    (
        RateLimitError,
        ErrorPolicy(
            abort=True,
            save_traceback=True,
            extra_notes=(messages.NOTE_RATE_LIMIT_ABORTED, ERRORS_LOG_TOKEN),
        ),
    ),
    (ServerError, ErrorPolicy(prepare_before_retry=False, counts_as_failure=False)),
    (ScraperParseError, ErrorPolicy(affects_exit_status=True)),
    (ScraperError, ErrorPolicy(save_traceback=True, extra_notes=(ERRORS_LOG_TOKEN,))),
)


def policy_for(exc: Exception) -> ErrorPolicy:
    return next(
        (policy for exc_type, policy in RETRY_POLICIES if isinstance(exc, exc_type)),
        DEFAULT_POLICY,
    )


__all__ = ["ERRORS_LOG_TOKEN", "ErrorPolicy", "SKIP_ERRORS", "policy_for"]
