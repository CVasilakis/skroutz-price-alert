"""Deterministic rendering of a :class:`BuildResult` to captured text.

A recording Rich console renders the scenario's panel; the captured plain text (layout,
wrapping, icons — no ANSI) plus a ``# border: <color>`` header is the golden artifact. The
gallery uses the same :func:`paint` to render with full color to a live console.

Determinism notes:
* The console width is fixed relative to the configured panel width, so footnote wrapping
  inside the panel is reproduced exactly while the surrounding width never shifts.
* Color is explicitly enabled even when the developer exports ``NO_COLOR``; Rich's
  progress bar otherwise changes its *text glyphs*, not merely its ANSI styling.
* ``get_time`` is pinned so the scraping-row ``Spinner`` renders a stable first frame.
* Trailing whitespace is stripped per line so console-width padding never leaks into the
  golden files.
"""

import io

from rich.console import Console

from core.tui.panel import PANEL_WIDTH, StatusPanelBuilder
from ui.catalog._base import BuildResult

# Keep a stable margin around the configured panel width.
CONSOLE_WIDTH = PANEL_WIDTH + 25


def make_recording_console() -> Console:
    """A recording console with fixed width, forced color, and a pinned clock."""
    console = Console(
        record=True,
        file=io.StringIO(),  # capture-only: record without echoing to stdout
        force_terminal=True,
        color_system="truecolor",
        no_color=False,
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


# Box-drawing corners that open / close a Rich panel border.
_PANEL_OPEN = "╭"
_PANEL_CLOSE = "╰"


def lines_outside_panels(transcript: str) -> list[str]:
    """Returns the non-blank transcript lines that fall *outside* any panel box.

    A panel is delimited by a top border line (containing ``╭``) and a bottom border line
    (containing ``╰``); every line between them is inside. Blank lines - including the gaps
    between stacked panels - are ignored. This is how the interactive-startup surface
    detects a line printed straight to the console mid-run (e.g. a stray log line), which
    breaks the panel layout by appearing *between* panels rather than inside one.

    Args:
        transcript (str): Captured plain-text console output (styles already stripped).

    Returns:
        list[str]: Every offending line, in order (empty when the layout is clean).
    """
    depth = 0
    stray: list[str] = []
    for line in transcript.splitlines():
        opens = line.count(_PANEL_OPEN)
        closes = line.count(_PANEL_CLOSE)
        if depth == 0 and opens == 0 and line.strip():
            stray.append(line)
        depth = max(0, depth + opens - closes)
    return stray


def snapshot_body(result: BuildResult) -> str:
    """The full golden-file content: a border-color header plus the captured panel.

    Shell scenarios also record the script's exit status as a second header line, so
    an exit-code regression is a one-line diff. Panel scenarios (exit_code=None) keep
    the original single-line header byte-for-byte.
    """
    header = f"# border: {result.border_color}\n"
    if result.exit_code is not None:
        header += f"# exit: {result.exit_code}\n"
    return f"{header}\n{capture_text(result)}\n"
