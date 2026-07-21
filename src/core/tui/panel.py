import re
from collections.abc import Iterable, Sequence

from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text


def uniform_column_widths(
    rows: Iterable[Sequence], columns: tuple[int, ...] = (0, 1)
) -> dict[int, int]:
    """Max rendered cell width per column index across all rows (non-str cells ignored).

    Used to give every section table in a panel identical icon/label column widths so the
    value column lines up across the sections divided by a rule. The rendered width is measured
    via ``Text.from_markup(...).cell_len`` so Rich markup and ``escape()``d brackets are resolved
    to their true display width; non-string cells (e.g. a ``Spinner`` or ``ProgressBar``) are
    skipped, since the emoji icons dominate the icon column's width.

    Args:
        rows (Iterable[Sequence]): The row tuples to measure (``(icon, label, value)``).
        columns (tuple[int, ...]): The column indices to size. Defaults to icon and label.

    Returns:
        dict[int, int]: Column index -> fixed width, for columns that had a string cell.
    """
    widths: dict[int, int] = {}
    for row in rows:
        for c in columns:
            if c < len(row) and isinstance(row[c], str):
                widths[c] = max(widths.get(c, 0), Text.from_markup(row[c]).cell_len)
    return widths


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

    def __init__(self, title: str, width: int = 75):
        """Initializes the panel builder.

        Args:
            title (str): The panel title displayed in the border.
            width (int): The panel width in characters. Defaults to 75.
        """
        self.title = title
        self.width = width
        self._rows: list[tuple] = []
        self.notes: list[str] = []
        self.icons: list[str] = []

    @staticmethod
    def _new_table(col_widths: dict[int, int] | None = None) -> Table:
        """Builds an empty 3-column (icon, label, value) section table.

        Args:
            col_widths (dict[int, int] | None): Fixed widths per column index, shared across
                a panel's sections so every section's value column starts at the same position.
                A ``None`` width leaves the column auto-sized.
        """
        col_widths = col_widths or {}
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Icon", justify="center", width=col_widths.get(0))
        table.add_column("Label", style="bold", width=col_widths.get(1))
        table.add_column("Value")
        return table

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
        note_stripped = note.strip()
        if note_stripped and not note_stripped.endswith("."):
            note_stripped += "."
        self.notes.append(note_stripped)
        return f" [dim default][{len(self.notes)}][/dim default]"

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
        # Size the icon/label columns once across every section's rows so the value column
        # starts at the same position above and below each divider.
        widths = uniform_column_widths((e[1], e[2], e[3]) for e in self._rows if e[0] == "row")

        sections: list = []
        current = self._new_table(widths)
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
                current = self._new_table(widths)
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
            notes_lines = [""]
            for i, note in enumerate(self.notes, 1):
                escaped_note = escape(note)
                escaped_note = re.sub(r"`([^`]+)`", r"[cyan]\1[/cyan]", escaped_note)
                notes_lines.append(f"  [{i}] {escaped_note}")
            blocks.append(Text.from_markup("\n".join(notes_lines), style="dim"))

        renderable = blocks[0] if len(blocks) == 1 else Group(*blocks)

        console.print(
            Panel(
                renderable,
                title=f"[bold]{escape(self.title)}[/bold]",
                border_style=color,
                width=self.width,
            )
        )
