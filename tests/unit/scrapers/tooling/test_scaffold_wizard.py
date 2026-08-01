import io
from unittest import mock

from rich.console import Console

from core.scrapers.tooling.scaffold import ScaffoldRequest, ScaffoldResult
from core.scrapers.tooling.scaffold_wizard import collect_request, render_completion


def test_wizard_guides_reviews_and_confirms_a_common_http_plugin():
    stream = io.StringIO()
    console = Console(file=stream, width=76, color_system=None)
    prompts = [
        "acme_store",
        "Acme Store",
        "store.example",
        "/products/",
        "price",
        "1h",
        "http",
    ]
    confirmations = [False, False, False, False, True, True]

    with (
        mock.patch("core.scrapers.tooling.scaffold_wizard.Prompt.ask", side_effect=prompts),
        mock.patch(
            "core.scrapers.tooling.scaffold_wizard.Confirm.ask",
            side_effect=confirmations,
        ),
    ):
        request = collect_request(console)

    assert request == ScaffoldRequest(
        "acme_store",
        "Acme Store",
        ("store.example",),
        "/products/",
        transport="http",
    )
    output = stream.getvalue()
    assert "New scraper plugin" in output
    assert "Configuration fields" in output
    assert "execution interval" in output
    assert "shared HTTP transport" in output
    assert "Review scaffold" in output


def test_wizard_cancellation_returns_no_request():
    console = Console(file=io.StringIO(), width=76, color_system=None)
    prompts = ["acme", "Acme", "store.example", "/items/", "price", "1h", "bare"]
    confirmations = [False, False, False, False, False, False]

    with (
        mock.patch("core.scrapers.tooling.scaffold_wizard.Prompt.ask", side_effect=prompts),
        mock.patch(
            "core.scrapers.tooling.scaffold_wizard.Confirm.ask",
            side_effect=confirmations,
        ),
    ):
        assert collect_request(console) is None


def test_completion_panel_warns_when_tests_were_skipped(tmp_path):
    stream = io.StringIO()
    console = Console(file=stream, width=76, color_system=None)
    request = ScaffoldRequest("acme", "Acme", ("store.example",), "/items/", include_tests=False)

    render_completion(
        request,
        ScaffoldResult(tmp_path / "src/acme", None),
        console,
    )

    output = stream.getvalue()
    assert "Scaffold created" in output
    assert "Tests skipped" in output
    assert "plugin-check.sh --acme" in output
