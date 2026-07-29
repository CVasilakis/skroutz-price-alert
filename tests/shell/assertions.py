"""Semantic assertions for human-facing shell task output.

The shell UI may wrap a four-space task line onto eight-space continuation lines
according to ``COLUMNS``. These helpers fold only that presentation shape. They do
not normalize protocols, debug output, blank lines, padding, or snapshot text.
"""

import re

_TASK_LINE = re.compile(r"^    \[([!ivx])\] (.*)$")
_CONTINUATION_LINE = re.compile(r"^        (\S.*)$")
_SECTION_HEADING = re.compile(r"^\[[+!]\] \S.*$")


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


def shell_tui_layout_errors(output: str) -> tuple[str, ...]:
    """Return structural errors in a sectioned, non-debug shell transcript.

    Help output has no section headings and is outside this grammar. Operational
    output must have one outer blank line, exactly one blank line between sections,
    no blank lines within a section, and body text indented by four or eight spaces.
    This semantic check complements exact UI snapshots: regenerating snapshots
    cannot accidentally approve a malformed section layout.
    """
    lines = output.splitlines()
    headings = [index for index, line in enumerate(lines) if _SECTION_HEADING.fullmatch(line)]
    if not headings:
        return ()

    errors: list[str] = []
    if headings[0] != 1 or not lines or lines[0] != "":
        errors.append("sectioned output must start with exactly one blank line")
    if not lines or lines[-1] != "" or (len(lines) > 1 and lines[-2] == ""):
        errors.append("sectioned output must end with exactly one blank line")

    for position, heading_index in enumerate(headings):
        line_number = heading_index + 1
        if position:
            previous_heading = headings[position - 1]
            if (
                heading_index < 2
                or lines[heading_index - 1] != ""
                or lines[heading_index - 2] == ""
            ):
                errors.append(
                    f"line {line_number}: sections must be separated by exactly one blank line"
                )
            if heading_index == previous_heading + 1:
                errors.append(f"line {line_number}: previous section has no body")

        section_end = headings[position + 1] if position + 1 < len(headings) else len(lines)
        body = lines[heading_index + 1 : section_end]
        if body and body[-1] == "":
            body = body[:-1]
        if not body:
            errors.append(f"line {line_number}: section has no body")
            continue

        for offset, line in enumerate(body, start=heading_index + 2):
            if not line:
                errors.append(f"line {offset}: blank line inside section")
                continue
            indentation = len(line) - len(line.lstrip(" "))
            if indentation not in (4, 8):
                errors.append(
                    f"line {offset}: section body must use four- or eight-space indentation"
                )

    return tuple(errors)
