from core.application.contracts import RunOutcome
from core.constants import (
    EXIT_CODE_INTERRUPT,
    EXIT_CODE_NOTIFICATION_ERROR,
    EXIT_CODE_PLUGIN_DEPENDENCY_ERROR,
    EXIT_CODE_RATE_LIMIT_ERROR,
    EXIT_CODE_SCRAPE_ERROR,
    EXIT_CODE_SKIPPED,
    EXIT_CODE_STORAGE_ERROR,
    EXIT_CODE_TARGET_CONFIG_ERROR,
)


def test_run_outcome_exit_priority_and_skipped():
    assert (
        RunOutcome(target_config_error=True, storage_error=True).exit_code(
            interrupted=False, target_count=1
        )
        == EXIT_CODE_TARGET_CONFIG_ERROR
    )

    def code(outcome, interrupted=False):
        return outcome.exit_code(interrupted=interrupted, target_count=1)

    assert code(RunOutcome(storage_error=True)) == EXIT_CODE_STORAGE_ERROR
    assert code(RunOutcome(dependency_error=True)) == EXIT_CODE_PLUGIN_DEPENDENCY_ERROR
    assert code(RunOutcome(scrape_error=True)) == EXIT_CODE_SCRAPE_ERROR
    assert code(RunOutcome(rate_limited=True)) == EXIT_CODE_RATE_LIMIT_ERROR
    assert code(RunOutcome(notification_error=True)) == EXIT_CODE_NOTIFICATION_ERROR
    assert code(RunOutcome(skipped_count=1)) == EXIT_CODE_SKIPPED
    assert code(RunOutcome(), True) == EXIT_CODE_INTERRUPT
