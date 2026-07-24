import io

import pytest
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from core import messages
from core.general.configuration import GENERAL_PERMISSION_WARNING
from core.scrapers.framework.setting_messages import (
    interval_warning_message,
    notify_errors_warning_message,
    retention_warning_message,
    suppress_repeated_price_alerts_warning_message,
)
from core.tui.footnotes import FootnoteRegistry
from core.tui.panel import PANEL_WIDTH
from core.tui.service_verdicts import classify_service_state


def _render(registry: FootnoteRegistry, width: int = PANEL_WIDTH) -> str:
    stream = io.StringIO()
    Console(file=stream, width=width + 25, color_system=None).print(
        Panel(registry.render(), width=width)
    )
    return stream.getvalue()


def test_empty_notes_are_ignored_and_references_are_sequential():
    notes = FootnoteRegistry()
    assert notes.add(" \t\n ") == ""
    assert notes.add("first") == " [dim default][1][/dim default]"
    assert notes.add_many(["second", "", "third"]) == (
        " [dim default][2][/dim default] [dim default][3][/dim default]"
    )
    assert notes.notes == ("first.", "second.", "third.")

    notes.clear()
    assert notes.notes == ()


def test_duplicate_notes_reuse_the_original_reference():
    notes = FootnoteRegistry()

    assert notes.add("same note") == " [dim default][1][/dim default]"
    assert notes.add(" same note. ") == " [dim default][1][/dim default]"
    assert notes.notes == ("same note.",)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Already.", "Already."),
        ("Really!", "Really!"),
        ("Why?", "Why?"),
        ("Needs one", "Needs one."),
    ],
)
def test_terminal_punctuation_normalization(source, expected):
    notes = FootnoteRegistry()
    notes.add(source)
    assert notes.notes == (expected,)


def test_ten_notes_share_the_widest_reference_column():
    notes = FootnoteRegistry()
    notes.add_many(f"note {number}" for number in range(1, 11))
    lines = [line for line in _render(notes).splitlines() if "[" in line]

    assert len(lines) == 10
    assert lines[0].index("Note".lower()) == lines[-1].index("Note".lower())
    assert "[1]" in lines[0]
    assert "[10]" in lines[-1]


def test_sixty_four_and_sixty_five_cell_notes_are_both_preserved():
    notes = FootnoteRegistry()
    note_64 = "x" * 63 + "."
    note_65 = "y" * 64 + "."
    notes.add_many([note_64, note_65])

    assert [Text(note).cell_len for note in notes.notes] == [64, 65]
    rendered = _render(notes)
    assert "x" * 63 in rendered
    assert "y" * 64 in rendered.replace("\n", "")


def test_long_prose_and_unbroken_tokens_fold_without_exceeding_panel_width():
    notes = FootnoteRegistry()
    notes.add("word " * 50)
    notes.add("z" * 180)

    rendered = _render(notes, width=55)
    lines = rendered.splitlines()
    assert len(lines) > 8
    assert all(Text.from_ansi(line).cell_len <= 55 for line in lines)
    assert rendered.replace("\n", "").count("z") == 180


def test_unicode_combining_text_is_preserved_and_measured_in_terminal_cells():
    notes = FootnoteRegistry()
    value = "商品 e\u0301"
    notes.add(value)

    assert notes.notes == (f"{value}.",)
    assert Text(notes.notes[0]).cell_len == 7


def test_whitespace_and_unmatched_backticks_are_normalized_safely():
    notes = FootnoteRegistry()
    notes.add("  before \n\t after   `literal   text  ")
    notes.add(" prose \n `code   keeps\tspaces` \t end ")

    assert notes.notes == (
        "before after `literal text.",
        "prose `code   keeps spaces` end.",
    )


def test_markup_is_literal_and_only_paired_backticks_gain_dim_cyan_style():
    notes = FootnoteRegistry()
    notes.add("Literal [red]tag[/red], then `[bold]/tmp/a b[/bold]`")
    console = Console(width=PANEL_WIDTH, color_system="truecolor")
    segments = list(console.render(notes.render(), console.options.update(width=71)))

    literal = next(segment for segment in segments if "[red]tag[/red]" in segment.text)
    code = next(segment for segment in segments if "/tmp/a b" in segment.text)
    assert str(literal.style) == "dim"
    assert str(code.style) == "dim cyan"
    assert "`" not in "".join(segment.text for segment in segments)


def test_apostrophe_delimited_paths_remain_prose_while_backticks_style_code():
    notes = FootnoteRegistry()
    notes.add("Compare 'config/plain.json' with `config/styled.json`")
    console = Console(width=PANEL_WIDTH, color_system="truecolor")
    segments = list(console.render(notes.render(), console.options.update(width=71)))

    prose = next(segment for segment in segments if "config/plain.json" in segment.text)
    code = next(segment for segment in segments if "config/styled.json" in segment.text)
    assert str(prose.style) == "dim"
    assert str(code.style) == "dim cyan"


def test_wrapped_body_lines_have_a_hanging_indent():
    notes = FootnoteRegistry()
    notes.add("one two three four five six seven eight nine ten eleven twelve")
    rendered = _render(notes, width=38)
    body_lines = [
        line
        for line in rendered.splitlines()
        if "one two" in line or "seven" in line or "twelve" in line
    ]
    first_body_cell = body_lines[0].index("one")
    assert len(body_lines) >= 2
    assert [
        line.index(next(word for word in ("one", "seven", "twelve") if word in line))
        for line in body_lines
    ] == [first_body_cell] * len(body_lines)


def test_application_owned_notes_fit_one_row_at_default_panel_width():
    scrape_verdict = classify_service_state(
        "exit-code",
        "18",
        "insomnia",
        "insomnia.json",
    )
    assert scrape_verdict.note is not None
    built_in_notes = [
        GENERAL_PERMISSION_WARNING,
        messages.NOTE_NOTIFIED_FAIL,
        messages.NOTE_NOTIFIED_NONE,
        messages.NOTE_SKIP_FIELD,
        messages.NOTE_RATE_LIMIT_ABORTED,
        messages.errors_log_pointer("insomnia"),
        interval_warning_message(),
        retention_warning_message(),
        notify_errors_warning_message(),
        suppress_repeated_price_alerts_warning_message(),
        scrape_verdict.note,
    ]
    notes = FootnoteRegistry()
    notes.add_many(built_in_notes)

    rendered = _render(notes)
    content_rows = [
        line[1:-1].strip()
        for line in rendered.splitlines()[1:-1]
        if line[1:-1].strip()
    ]
    assert len(notes.notes) >= 10
    assert len(content_rows) == len(notes.notes)
    assert all(Text.from_ansi(line).cell_len <= PANEL_WIDTH for line in rendered.splitlines())


@pytest.mark.parametrize(
    "note",
    [
        "Create missing `config/insomnia.json` from the plugin example.",
        "Cannot read `config/insomnia.json`; check its permissions.",
        "Fix JSON in `config/insomnia.json` at line 12, column 4.",
        "`config/insomnia.json` must contain a JSON object.",
        "Fix invalid state in `state/insomnia.json`; details are logged.",
        "Cannot save `state/insomnia.json`; check its permissions.",
    ],
)
def test_dynamic_file_error_notes_use_at_most_two_rows(note):
    notes = FootnoteRegistry()
    notes.add(note)

    rendered = _render(notes)
    content_rows = [
        line[1:-1].strip()
        for line in rendered.splitlines()[1:-1]
        if line[1:-1].strip()
    ]
    assert 1 <= len(content_rows) <= 2
    assert note.replace("`", "") in rendered.replace("\n", "")
    assert all(Text.from_ansi(line).cell_len <= PANEL_WIDTH for line in rendered.splitlines())
