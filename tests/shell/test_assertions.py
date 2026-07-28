import pytest

from shell.assertions import assert_task_status, logical_task_lines


@pytest.mark.parametrize(
    "output",
    (
        "    [v] Repository-local pre-push checks are enabled.\n",
        "    [v] Repository-local pre-push checks\n        are enabled.\n",
    ),
)
def test_task_assertion_accepts_equivalent_wrapped_and_unwrapped_output(output):
    assert_task_status(output, "v", "Repository-local pre-push checks are enabled.")


def test_only_eight_space_continuations_of_four_space_tasks_are_folded():
    output = (
        "protocol\trow\n"
        "    [x] A task failed for a\n"
        "        semantic reason.\n"
        "  [i] A differently indented status\n"
        "        is not folded.\n"
        "    debug padding remains separate\n"
    )

    assert logical_task_lines(output) == ("[x] A task failed for a semantic reason.",)
