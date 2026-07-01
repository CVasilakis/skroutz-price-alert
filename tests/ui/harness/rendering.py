"""Deterministic rendering of a :class:`BuildResult` to captured text.

A recording Rich console renders the scenario's panel; the captured plain text (layout,
wrapping, icons — no ANSI) plus a ``# border: <color>`` header is the golden artifact. The
gallery uses the same :func:`paint` to render with full color to a live console.

Determinism notes:
* The console width is fixed and >= the 75-char panel width, so footnote wrapping inside
  the panel is reproduced exactly while the surrounding width never shifts.
* ``get_time`` is pinned so the scraping-row ``Spinner`` renders a stable first frame.
* Trailing whitespace is stripped per line so console-width padding never leaks into the
  golden files.
"""

import io

from rich.console import Console

from panel import StatusPanelBuilder

from ui.catalog._base import BuildResult

# >= the 75-char panel width so panels render at full width with stable surrounding space.
CONSOLE_WIDTH = 100


def make_recording_console() -> Console:
    """A recording console with fixed width, forced color, and a pinned clock."""
    console = Console(
        record=True,
        file=io.StringIO(),  # capture-only: record without echoing to stdout
        force_terminal=True,
        color_system="truecolor",
        width=CONSOLE_WIDTH,
    )
    # Pin the clock so the scraping Spinner ("dots") renders a deterministic frame.
    console.get_time = lambda: 0.0
    return console


def paint(console: Console, result: BuildResult) -> None:
    """Renders ``result`` to ``console`` (handles both panel kinds the drivers produce)."""
    renderable = result.renderable
    if isinstance(renderable, StatusPanelBuilder):
        renderable.render(console, panel_color=result.border_color)
    else:
        console.print(renderable)


def capture_text(result: BuildResult) -> str:
    """Renders ``result`` to a recording console and returns clean plain text."""
    console = make_recording_console()
    paint(console, result)
    text = console.export_text(styles=False)
    # Strip per-line trailing padding and surrounding blank lines for stable diffs.
    return "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")


def snapshot_body(result: BuildResult) -> str:
    """The full golden-file content: a border-color header plus the captured panel."""
    return f"# border: {result.border_color}\n\n{capture_text(result)}\n"
