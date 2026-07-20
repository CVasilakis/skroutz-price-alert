import io

from rich.console import Console

from core.run import PriceOutcome
from core.settings import SettingStatus, SettingView
from core.ui.config_check import add_setting_row, config_view
from core.ui.panel import StatusPanelBuilder
from core.ui.tui import InteractiveRunReporter


def _render(renderable) -> str:
    stream = io.StringIO()
    Console(file=stream, color_system=None, width=100).print(renderable)
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


def test_status_setting_callbacks_and_title_are_plain_text():
    panel = StatusPanelBuilder("[red]Store[/red]")
    add_setting_row(
        panel,
        SettingView("[red]Mode[/red]", "[blue]fast[/blue]", SettingStatus.OK),
    )
    output_stream = io.StringIO()
    panel.render(Console(file=output_stream, color_system=None, width=100))
    output = output_stream.getvalue()

    assert "[red]Store[/red]" in output
    assert "[red]Mode[/red]" in output
    assert "[blue]fast[/blue]" in output
    assert panel.get_panel_color() == "green"
