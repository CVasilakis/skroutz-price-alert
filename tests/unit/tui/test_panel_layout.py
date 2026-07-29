import io

import pytest
from rich.console import Console
from rich.table import Table
from rich.text import Text

from core.presentation import resolved_setting_views
from core.scrapers.api import ScraperPlugin, SettingSpec
from core.scrapers.framework.compiler import compile_plugin
from core.settings import resolve_settings
from core.tui.config_check import add_setting_row, config_view
from core.tui.panel import (
    PANEL_WIDTH,
    PRIMARY_COLUMN_MIN,
    VALUE_COLUMN_MIN,
    PanelTableLayout,
    StatusPanelBuilder,
)
from core.tui.run_reporter import InteractiveRunReporter


def _render(renderable, console_width: int = 140) -> str:
    stream = io.StringIO()
    console = Console(file=stream, width=console_width, color_system=None)
    if isinstance(renderable, StatusPanelBuilder):
        renderable.render(console)
    else:
        console.print(renderable)
    return stream.getvalue()


@pytest.mark.parametrize("width", [55, PANEL_WIDTH, 95])
def test_status_panel_width_labels_and_footnotes_follow_custom_geometry(width):
    panel = StatusPanelBuilder("Responsive", width=width)
    panel.add_row("✅", "商品" * 40, "OK" + panel.add_note_ref("word " * 60))

    output = _render(panel)
    lines = output.splitlines()
    assert all(Text.from_ansi(line).cell_len == width for line in lines)
    assert "…" in lines[1]
    assert all(Text.from_ansi(line).cell_len <= width for line in lines)


@pytest.mark.parametrize(
    ("label", "value", "expected_primary", "expected_value"),
    [
        ("Short", "small", 20, 37),
        ("L" * 30, "small", 30, 27),
        ("L" * 40, "small", 32, 25),
        ("L" * 30, "V" * 30, 27, 30),
        ("L" * 30, "V" * 37, 20, 37),
        ("L" * 30, "V" * 80, 20, 37),
    ],
)
def test_default_layout_prioritizes_value_content(label, value, expected_primary, expected_value):
    layout = PanelTableLayout.from_rows(PANEL_WIDTH, [("✅", label, value)])

    assert layout.icon == 2
    assert layout.primary == expected_primary
    assert layout.value == expected_value
    assert layout.primary + layout.value == 57


def test_wider_panel_derives_additional_capacity_from_panel_width():
    layout = PanelTableLayout.from_rows(95, [("✅", "L" * 80, "small")])

    assert layout.primary == 52
    assert layout.value == VALUE_COLUMN_MIN


def test_compact_panel_relaxes_primary_minimum_before_value_minimum():
    layout = PanelTableLayout.from_rows(55, [("✅", "L" * 80, "small")])

    assert layout.primary == 12
    assert layout.value == VALUE_COLUMN_MIN


@pytest.mark.parametrize(
    ("panel_width", "expected_primary", "expected_value"),
    [(45, 2, 25), (40, 1, 21)],
)
def test_very_narrow_panel_reduces_value_only_after_primary(
    panel_width, expected_primary, expected_value
):
    layout = PanelTableLayout.from_rows(panel_width, [("✅", "L" * 80, "V" * 80)])

    assert layout.primary == expected_primary
    assert layout.value == expected_value


def test_text_measurement_uses_markup_unicode_and_widest_logical_line():
    value = Text("short\n")
    value.append("商品" * 8)
    layout = PanelTableLayout.from_rows(
        PANEL_WIDTH,
        [("✅", "[bold]L[/bold]" * 30, value)],
    )

    assert layout.primary == 25
    assert layout.value == 32


def test_non_text_value_uses_reserved_capacity_without_claiming_intrinsic_width():
    progress = Table.grid()
    progress.add_row("progress")

    layout = PanelTableLayout.from_rows(PANEL_WIDTH, [("✅", "L" * 30, progress)])

    assert layout.primary == 30
    assert layout.value == 27


def test_value_minimum_constants_match_supported_default_geometry():
    assert PRIMARY_COLUMN_MIN == 20
    assert VALUE_COLUMN_MIN == 25


def test_status_value_with_two_footnotes_stays_on_one_line():
    value = "1630.30 € (Target: 1500.50 €) [1] [2]"
    panel = StatusPanelBuilder("Dynamic")
    panel.add_row("🎉", "Suppress Repeated Price Alerts", value)

    lines = _render(panel).splitlines()

    assert len(lines) == 3
    assert value in lines[1]
    assert "Suppress Repeated P…" in lines[1]


def test_default_panel_width_is_shared():
    assert StatusPanelBuilder("Status").width == PANEL_WIDTH
    assert InteractiveRunReporter().width == PANEL_WIDTH


def test_interactive_notes_use_shared_punctuation_normalization():
    reporter = InteractiveRunReporter()
    reporter.log_result("✅", "Item", "OK", ["Done!", "Why?", "Plain"])
    assert reporter.notes == ("Done!", "Why?", "Plain.")


@pytest.mark.parametrize("width", [55, PANEL_WIDTH, 95])
def test_interactive_layout_preserves_full_unicode_name_and_scales_progress(width):
    name = "商品" * 40
    reporter = InteractiveRunReporter(width=width)
    reporter.target_name = "Store"
    reporter.start_scraping(name)
    assert reporter.scraping_name == name
    reporter.complete_scraping()
    reporter.log_result("✅", name, "OK", "word " * 40)
    assert Text.from_markup(reporter.rows[0][1]).plain == name
    reporter.start_sleep(20)
    reporter.update_sleep(10)

    output = _render(reporter._generate_panel())
    lines = output.splitlines()
    assert all(Text.from_ansi(line).cell_len == width for line in lines)
    assert "…" in lines[1]
    progress_line = next(line for line in lines if "10.0s" in line)
    assert progress_line.count("━") >= 4


def test_interactive_layout_shrinks_primary_when_long_value_appears():
    reporter = InteractiveRunReporter()
    reporter.target_name = "Store"
    reporter.settings_rows = [
        ("✅", "Suppress Repeated Price Alerts", "false"),
    ]

    before = _render(reporter._generate_panel())
    assert "Suppress Repeated Price Alerts" in before

    reporter.log_result(
        "🎉",
        "Expensive product with a long name",
        "1630.30 € (Target: 1500.50 €)",
        ["First note", "Second note"],
    )
    after = _render(reporter._generate_panel())

    assert "Suppress Repeated P…" in after
    assert "Expensive product w…" in after
    assert "1630.30 € (Target: 1500.50 €) [1] [2]" in after


def test_status_and_interactive_consumers_share_footnote_layout_and_code_style():
    note = "Inspect `[red]/tmp/plugin path[/red]` after [blue]failure[/blue]"
    status = StatusPanelBuilder("Status")
    status.add_row("🟡", "Item", "Warning" + status.add_note_ref(note))

    reporter = InteractiveRunReporter()
    reporter.target_name = "Status"
    reporter.log_warning("Item", "Warning", note)

    status_output = _render(status)
    run_output = _render(reporter._generate_panel())
    status_note = [line for line in status_output.splitlines() if "[1]" in line][-1:]
    run_note = [line for line in run_output.splitlines() if "[1]" in line][-1:]
    assert status_note == run_note
    assert "[blue]failure[/blue]" in status_output
    assert "[blue]failure[/blue]" in run_output

    for registry in (status._footnotes, reporter._footnotes):
        console = Console(width=PANEL_WIDTH, color_system="truecolor")
        segments = list(console.render(registry.render(), console.options.update(width=71)))
        code = next(segment for segment in segments if "/tmp/plugin path" in segment.text)
        assert str(code.style) == "dim cyan"


def test_compiled_long_plugin_warning_is_safe_in_both_panel_paths():
    warning = (
        "Plugin [red]markup[/red] remains literal while the operator checks "
        "`config/plugin path.json`; this deliberately long warning retains every "
        "piece of useful remediation text and wraps inside the panel"
    )
    spec = SettingSpec("plugin_mode", int, default=1, warning=warning)
    plugin = compile_plugin(
        ScraperPlugin(display_name="Plugin", settings=(spec,)),
        target="plugin",
        package="tests.plugins.plugin",
    )
    settings = resolve_settings(plugin.setting_specs, {"plugin_mode": "invalid"})
    view = next(view for view in resolved_setting_views(settings) if view.label == "Plugin Mode")

    status = StatusPanelBuilder("Plugin")
    add_setting_row(status, view)
    status_output = _render(status)

    reporter = InteractiveRunReporter()
    reporter.target_name = "Plugin"
    reporter.settings_rows = reporter._build_settings_rows([view], config_view(1))
    run_output = _render(reporter._generate_panel())

    for output in (status_output, run_output):
        assert "[red]markup[/red]" in output
        assert "config/plugin path.json" in output
        assert "useful remediation text" in output
        assert all(Text.from_ansi(line).cell_len <= PANEL_WIDTH for line in output.splitlines())
    for registry in (status._footnotes, reporter._footnotes):
        console = Console(width=PANEL_WIDTH, color_system="truecolor")
        segments = list(console.render(registry.render(), console.options.update(width=71)))
        path = next(segment for segment in segments if "config/plugin path.json" in segment.text)
        assert str(path.style) == "dim cyan"
