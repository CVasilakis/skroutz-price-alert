import io

from rich.console import Console

from core.application.contracts import PriceOutcome
from core.presentation import SettingView
from core.settings import SettingStatus
from core.tui.config_check import add_setting_row, config_view
from core.tui.panel import PANEL_WIDTH, StatusPanelBuilder
from core.tui.run_reporter import InteractiveRunReporter


def _render(renderable) -> str:
    stream = io.StringIO()
    Console(file=stream, color_system=None, width=PANEL_WIDTH + 25).print(renderable)
    return stream.getvalue()


def test_interactive_external_text_is_literal_and_cannot_change_success_border():
    reporter = InteractiveRunReporter()
    reporter.target_name = "[red]Store[/red]"
    reporter.settings_rows = reporter._build_settings_rows(
        [SettingView("[red]Region[/red]", "[blue]EU[/blue]", SettingStatus.OK)],
        config_view(1),
    )
    reporter.log_price_result(
        "[red]Phone[/red]",
        10,
        "[blue]EUR[/blue]",
        20,
        PriceOutcome.OK,
    )
    reporter.is_complete = True

    panel = reporter._generate_panel()
    output = _render(panel)

    for literal in (
        "[red]Store[/red]",
        "[red]Region[/red]",
        "[blue]EU[/blue]",
        "[red]Phone[/red]",
        "[blue]EUR[/blue]",
    ):
        assert literal in output
    assert str(panel.border_style) == "green"


def test_exception_config_and_url_details_render_literally_inside_error_panel():
    reporter = InteractiveRunReporter()
    reporter.target_name = "Store"
    reporter.settings_rows = reporter._build_settings_rows(
        [], config_view(0, error="[red]bad config[/red]")
    )
    reporter.log_error(
        "[yellow]Item[/yellow]",
        "[green]Parser exploded[/green]",
        "URL: [bold]https://store.example/[x][/bold]",
    )
    panel = reporter._generate_panel()
    output = _render(panel)

    assert "[red]bad config[/red]" in output
    assert "[yellow]Item[/yellow]" in output
    assert "[green]Parser exploded[/green]" in output
    assert "[bold]https://store.example/[x][/bold]" in output
    assert str(panel.border_style) == "red"


def test_interactive_error_row_backticks_are_dim_cyan_without_parsing_markup():
    reporter = InteractiveRunReporter()
    reporter.target_name = "Store"
    reporter.log_error("Storage", "Could not save `[red]state/store.json[/red]`")
    panel = reporter._generate_panel()
    console = Console(width=PANEL_WIDTH + 25, color_system="truecolor")
    segments = list(console.render(panel, console.options))

    path = next(segment for segment in segments if "state/store.json" in segment.text)
    assert str(path.style) == "dim cyan"
    assert "[red]state/store.json[/red]" in "".join(segment.text for segment in segments)
    assert "`" not in "".join(segment.text for segment in segments)


def test_interactive_system_error_text_is_literal_and_inline_code_is_styled():
    reporter = InteractiveRunReporter()
    reporter.target_name = "Store"
    reporter.log_system_error(
        "Missing [red]dependency[/red]; run `./install.sh --[blue]store[/blue]`."
    )
    panel = reporter._generate_panel()
    console = Console(width=PANEL_WIDTH + 25, color_system="truecolor")
    segments = list(console.render(panel, console.options))
    rendered = "".join(segment.text for segment in segments)

    assert "[red]dependency[/red]" in rendered
    assert "./install.sh" in rendered
    assert "--[blue]store[/blue]" in rendered
    command = next(segment for segment in segments if "./install.sh" in segment.text)
    assert str(command.style) == "dim cyan"
    assert "System" not in rendered


def test_status_setting_callbacks_and_title_are_plain_text():
    panel = StatusPanelBuilder("[red]Store[/red]")
    add_setting_row(
        panel,
        SettingView("[red]Mode[/red]", "[blue]fast[/blue]", SettingStatus.OK),
    )
    output_stream = io.StringIO()
    panel.render(Console(file=output_stream, color_system=None, width=PANEL_WIDTH + 25))
    output = output_stream.getvalue()

    assert "[red]Store[/red]" in output
    assert "[red]Mode[/red]" in output
    assert "[blue]fast[/blue]" in output
    assert panel.get_panel_color() == "green"
