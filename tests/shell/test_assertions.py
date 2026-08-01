import pytest

from shell.assertions import (
    assert_task_status,
    logical_task_lines,
    shell_outer_padding_errors,
    shell_tui_layout_errors,
)


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


def test_valid_sectioned_shell_tui_layout_has_no_errors():
    output = (
        "\n"
        "[+] First section\n"
        "    [v] A task.\n"
        "        wrapped continuation.\n"
        "\n"
        "[!] Second section\n"
        "    Guidance text.\n"
        "\n"
    )

    assert shell_tui_layout_errors(output) == ()


@pytest.mark.parametrize(
    ("output", "expected"),
    (
        (
            "[+] Section\n    [v] Task.\n\n",
            "shell output must start with exactly one blank line",
        ),
        (
            "\n[+] One\n    [v] Task.\n[+] Two\n    [v] Task.\n\n",
            "sections must be separated by exactly one blank line",
        ),
        (
            "\n[+] Section\n    [v] First.\n\n    [v] Second.\n\n",
            "blank line inside section",
        ),
        (
            "\n[+] Section\n  [v] Bad indentation.\n\n",
            "section body must use four- or eight-space indentation",
        ),
        (
            "\n[+] Empty\n\n",
            "section has no body",
        ),
        (
            "\n[+] Section\n    [v] Task.\n",
            "shell output must end with exactly one blank line",
        ),
    ),
)
def test_shell_tui_layout_reports_malformed_sections(output, expected):
    errors = shell_tui_layout_errors(output)

    assert any(expected in error for error in errors), errors


def test_help_output_obeys_outer_padding_without_section_layout():
    assert shell_tui_layout_errors("\nUsage: tool.sh [-h]\n\n") == ()


@pytest.mark.parametrize(
    ("output", "expected"),
    (
        ("Error: failed.\n\n", "shell output must start with exactly one blank line"),
        ("\nError: failed.\n", "shell output must end with exactly one blank line"),
    ),
)
def test_non_sectioned_shell_output_still_requires_outer_padding(output, expected):
    assert expected in shell_outer_padding_errors(output)
