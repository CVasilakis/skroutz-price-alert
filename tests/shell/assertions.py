"""Semantic assertions for human-facing shell task output.

The shell UI may wrap a four-space task line onto eight-space continuation lines
according to ``COLUMNS``. These helpers fold only that presentation shape. They do
not normalize protocols, debug output, blank lines, padding, or snapshot text.
"""

import re

_TASK_LINE = re.compile(r"^    \[([!ivx])\] (.*)$")
_CONTINUATION_LINE = re.compile(r"^        (\S.*)$")


def logical_task_lines(output: str) -> tuple[str, ...]:
    """Return four-space task statuses with their wrapped prose rejoined."""
    logical: list[str] = []
    active_task: int | None = None
    for line in output.splitlines():
        task = _TASK_LINE.fullmatch(line)
        if task is not None:
            logical.append(f"[{task.group(1)}] {task.group(2)}")
            active_task = len(logical) - 1
            continue
        continuation = _CONTINUATION_LINE.fullmatch(line)
        if continuation is not None and active_task is not None:
            logical[active_task] += f" {continuation.group(1)}"
            continue
        active_task = None
    return tuple(logical)


def assert_task_status(output: str, marker: str, message: str) -> None:
    """Assert one marker and its complete semantic message as a single task."""
    expected = f"[{marker}] {message}"
    tasks = logical_task_lines(output)
    assert expected in tasks, f"{expected!r} not found in logical task lines: {tasks!r}"
