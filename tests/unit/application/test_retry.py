from core.application.retry import SKIP_ERRORS, policy_for
from core.exceptions import (
    InvalidScrapeResultError,
    InvalidURLError,
    PriceUnavailableError,
    RateLimitError,
    ResourceNotFoundError,
    ScraperError,
    ScraperParseError,
    ServerError,
)
from core.exit_status import ExitStatus


def test_error_policies_preserve_retry_semantics():
    assert policy_for(RateLimitError()).abort
    assert not policy_for(ServerError()).prepare_before_retry
    assert policy_for(ScraperParseError()).exit_status is ExitStatus.SCRAPE_ERROR
    assert policy_for(RuntimeError()).save_traceback


def test_every_plugin_facing_exception_has_an_explicit_framework_policy():
    assert SKIP_ERRORS == (ResourceNotFoundError, PriceUnavailableError, InvalidURLError)
    assert policy_for(RateLimitError()).exit_status is ExitStatus.RATE_LIMIT_ERROR
    assert policy_for(ServerError()).exit_status is None
    assert policy_for(ScraperParseError()).exit_status is ExitStatus.SCRAPE_ERROR
    assert policy_for(InvalidScrapeResultError()).exit_status is ExitStatus.SCRAPE_ERROR
    assert policy_for(ScraperError()).exit_status is None
