from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from core.tui.footnotes import FootnoteRegistry

PANEL_WIDTH = 75
PRIMARY_COLUMN_MIN = 20
VALUE_COLUMN_MIN = 25

_COLUMN_COUNT = 3
_COLUMN_HORIZONTAL_PADDING = 2
_DEFAULT_ICON_WIDTH = 2
_MIN_RENDERABLE_COLUMN_WIDTH = 1


def panel_content_width(panel_width: int) -> int:
    """Return usable cells after the panel border and default horizontal padding."""
    return max(1, panel_width - 4)


def _text_cell_width(value: object) -> int | None:
    """Return the widest logical line for Rich text, or ``None`` for other renderables."""
    if isinstance(value, str):
        text = Text.from_markup(value)
    elif isinstance(value, Text):
        text = value
    else:
        return None
    return max((line.cell_len for line in text.split("\n")), default=0)


@dataclass(frozen=True)
class PanelTableLayout:
    """Content-aware widths and canonical construction for a panel's three-column table."""

    icon: int
    primary: int
    value: int

    @classmethod
    def from_rows(
        cls,
        panel_width: int,
        rows: Iterable[Sequence[object]],
    ) -> "PanelTableLayout":
        """Allocate shared widths, favoring values while preserving responsive minima."""
        icon_desired = _DEFAULT_ICON_WIDTH
        primary_desired = 0
        value_desired = 0
        for row in rows:
            if len(row) > 0 and (width := _text_cell_width(row[0])) is not None:
                icon_desired = max(icon_desired, width)
            if len(row) > 1 and (width := _text_cell_width(row[1])) is not None:
                primary_desired = max(primary_desired, width)
            if len(row) > 2 and (width := _text_cell_width(row[2])) is not None:
                value_desired = max(value_desired, width)

        table_padding = _COLUMN_COUNT * _COLUMN_HORIZONTAL_PADDING * 2
        column_budget = max(
            _MIN_RENDERABLE_COLUMN_WIDTH * 2,
            panel_content_width(panel_width) - table_padding - icon_desired,
        )

        # Below the preferred 63-cell panel geometry, retain the value minimum first and
        # relax the primary minimum only as much as the requested panel width requires.
        value_min = min(
            VALUE_COLUMN_MIN,
            max(_MIN_RENDERABLE_COLUMN_WIDTH, column_budget - _MIN_RENDERABLE_COLUMN_WIDTH),
        )
        primary_min = min(
            PRIMARY_COLUMN_MIN,
            max(_MIN_RENDERABLE_COLUMN_WIDTH, column_budget - value_min),
        )

        reserved_value = min(
            max(value_desired, value_min),
            column_budget - primary_min,
        )
        primary = min(
            max(primary_desired, primary_min),
            column_budget - reserved_value,
        )
        value = column_budget - primary
        return cls(icon_desired, primary, value)

    def new_table(self, primary_header: str = "Label") -> Table:
        """Build an empty table whose column metrics match this allocation."""
        table = Table(
            show_header=False,
            box=None,
            padding=(0, _COLUMN_HORIZONTAL_PADDING),
        )
        table.add_column("Icon", justify="center", width=self.icon)
        table.add_column(
            primary_header,
            style="bold",
            width=self.primary,
            no_wrap=True,
            overflow="ellipsis",
        )
        table.add_column("Value", width=self.value)
        return table


class StatusPanelBuilder:
    """Reusable builder for Rich status panels with icon-based rows, footnotes, and automatic border coloring.

    Encapsulates the repeated pattern of: 3-column table (icon, label, value) +
    footnotes + icon-driven border color. Used by CLI tools (main, status, ping)
    to render consistent, self-contained status panels.

    Rows may be split into sections with :meth:`add_separator` (e.g. a settings
    section above the systemd status rows); each section renders as its own table and
    sections are divided by a thin horizontal rule.

    Usage:
        panel = StatusPanelBuilder("My Panel Title")
        ref = panel.add_note_ref("Some footnote text")
        panel.add_row("✅", "Label", f"Value{ref}")
        panel.add_separator()
        panel.add_row("✅", "Another", "Value")
        panel.render(console)
    """

    # Internal row entries are tagged tuples: ("row", icon, label, value) or ("sep",).
    _SEP: tuple[str] = ("sep",)

    def __init__(self, title: str, width: int = PANEL_WIDTH):
        """Initializes the panel builder.

        Args:
            title (str): The panel title displayed in the border.
            width (int): The panel width in terminal cells. Defaults to ``PANEL_WIDTH``.
        """
        self.title = title
        self.width = width
        self._rows: list[tuple] = []
        self._footnotes = FootnoteRegistry()
        self.icons: list[str] = []

    @property
    def notes(self) -> tuple[str, ...]:
        """Return the panel's normalized footnotes."""
        return self._footnotes.notes

    def add_row(self, icon: str, label: str, value: str) -> None:
        """Adds a row to the panel and tracks the icon for border color calculation.

        Args:
            icon (str): The status icon (e.g., '✅', '🟡', '❗', '🛑').
            label (str): The row label (rendered bold).
            value (str): The row value (supports Rich markup).
        """
        self.icons.append(icon)
        self._rows.append(("row", icon, label, value))

    def add_separator(self) -> None:
        """Marks a section break; the next rows render below a thin rule.

        A separator with no rows on either side is dropped at render time, so leading,
        trailing or doubled separators are harmless.
        """
        self._rows.append(self._SEP)

    def add_note_ref(self, note: str) -> str:
        """Adds a footnote and returns its formatted reference markup.

        The reference is a dim, bracketed number (e.g., '[1]') that can be
        appended to a row's value to link it to the footnote.

        Args:
            note (str): The footnote text.

        Returns:
            str: The Rich markup string for the footnote reference.
        """
        return self._footnotes.add(note)

    def get_panel_color(self) -> str:
        """Determines the panel border color based on the tracked icons.

        Priority: red (if any '❗') > yellow (if any '🟡') > green (default).

        Returns:
            str: The Rich color string for the panel border.
        """
        if "❗" in self.icons:
            return "red"
        elif "🟡" in self.icons:
            return "yellow"
        return "green"

    def _build_sections(self) -> list:
        """Splits the rows into section tables joined by dim rules.

        Returns:
            list: An ordered list of renderables (section ``Table``s separated by
                ``Rule``s) suitable for a ``Group``. Always contains at least one table.
        """
        # Allocate all columns once across every section so values receive priority and
        # retain the same starting position above and below each divider.
        layout = PanelTableLayout.from_rows(
            self.width,
            ((e[1], e[2], e[3]) for e in self._rows if e[0] == "row"),
        )

        sections: list = []
        current = layout.new_table()
        current_has_rows = False
        pending_sep = False

        for entry in self._rows:
            if entry[0] == "sep":
                # Defer the divider until a row actually follows, so a trailing or
                # empty-side separator never leaves a dangling rule.
                if current_has_rows:
                    pending_sep = True
                continue

            if pending_sep:
                sections.append(current)
                sections.append(Rule(style="dim"))
                current = layout.new_table()
                current_has_rows = False
                pending_sep = False

            _, icon, label, value = entry
            current.add_row(icon, label, value)
            current_has_rows = True

        sections.append(current)
        return sections

    def render(self, console: Console, panel_color: str | None = None) -> None:
        """Renders the panel to the given console.

        Args:
            console (Console): The Rich console to print to.
            panel_color (str | None): Override for the border color.
                If None, the color is determined automatically by get_panel_color().
        """
        color = panel_color or self.get_panel_color()

        blocks: list = self._build_sections()

        if self.notes:
            blocks.append(self._footnotes.render())

        renderable = blocks[0] if len(blocks) == 1 else Group(*blocks)

        console.print(
            Panel(
                renderable,
                title=f"[bold]{escape(self.title)}[/bold]",
                border_style=color,
                width=self.width,
            )
        )


__all__ = [
    "PANEL_WIDTH",
    "PRIMARY_COLUMN_MIN",
    "PanelTableLayout",
    "StatusPanelBuilder",
    "VALUE_COLUMN_MIN",
    "panel_content_width",
]
