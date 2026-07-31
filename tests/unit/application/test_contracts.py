from core.application.contracts import RunOutcome
from core.exit_status import ExitStatus


def test_run_outcome_exit_priority_and_skipped():
    priority = (
        ExitStatus.APPLICATION_ERROR,
        ExitStatus.TARGET_CONFIG_ERROR,
        ExitStatus.NOTIFICATION_CONFIG_ERROR,
        ExitStatus.STORAGE_ERROR,
        ExitStatus.PLUGIN_DEPENDENCY_ERROR,
        ExitStatus.SCRAPE_ERROR,
        ExitStatus.RATE_LIMIT_ERROR,
        ExitStatus.NOTIFICATION_ERROR,
    )
    for index, expected in enumerate(priority):
        outcome = RunOutcome(statuses=set(priority[index:]))
        assert outcome.exit_status(interrupted=False, target_count=1) is expected

    assert (
        RunOutcome(skipped_count=1).exit_status(interrupted=False, target_count=1)
        is ExitStatus.ALREADY_RUNNING
    )
    assert (
        RunOutcome(skipped_count=1).exit_status(interrupted=False, target_count=2)
        is ExitStatus.SUCCESS
    )
    assert RunOutcome().exit_status(interrupted=False, target_count=1) is ExitStatus.SUCCESS
    assert RunOutcome().exit_status(interrupted=True, target_count=1) is ExitStatus.INTERRUPTED


def test_run_outcome_merge_combines_statuses_and_skip_counts():
    outcome = RunOutcome(statuses={ExitStatus.NOTIFICATION_ERROR}, skipped_count=1)
    outcome.merge(RunOutcome(statuses={ExitStatus.STORAGE_ERROR}, skipped_count=2))

    assert outcome.statuses == {ExitStatus.NOTIFICATION_ERROR, ExitStatus.STORAGE_ERROR}
    assert outcome.skipped_count == 3
