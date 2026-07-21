from core.application.retry import policy_for
from core.exceptions import RateLimitError, ScraperParseError, ServerError


def test_error_policies_preserve_retry_semantics():
    assert policy_for(RateLimitError()).abort
    assert not policy_for(ServerError()).prepare_before_retry
    assert policy_for(ScraperParseError()).affects_exit_status
    assert policy_for(RuntimeError()).save_traceback
