"""Artifact-switching behavior of the self-contained HTML gallery."""

from rich.text import Text

from ui.catalog._base import BuildResult, OutputLog, Scenario, Surface
from ui.harness.html_report import render_report


def _scenario(*, in_gallery: bool = True) -> Scenario:
    return Scenario(
        name="artifact_case",
        surface=Surface.RUN if in_gallery else Surface.STARTUP,
        description="Interactive and quiet output",
        build=lambda: BuildResult(
            Text("interactive-only-marker"),
            "green",
            output_logs=(
                OutputLog("logs/example/output.log", "[fixed UTC] <unsafe> & complete\n"),
            ),
        ),
        in_gallery=in_gallery,
    )


def test_report_adds_switchable_interactive_and_background_artifacts():
    report = render_report([_scenario()])

    assert ">Interactive</button>" in report
    assert ">logs/example/output.log</button>" in report
    assert "interactive-only-marker" in report
    assert "&lt;unsafe&gt; &amp; complete" in report
    assert "[fixed UTC] <unsafe>" not in report


def test_hidden_interactive_artifact_stays_hidden_until_explicitly_requested():
    scenario = _scenario(in_gallery=False)

    default_report = render_report([scenario])
    explicit_report = render_report([scenario], show_hidden_interactive=True)

    assert "interactive-only-marker" not in default_report
    assert "logs/example/output.log" in default_report
    assert "interactive-only-marker" in explicit_report
    assert ">Interactive</button>" in explicit_report
