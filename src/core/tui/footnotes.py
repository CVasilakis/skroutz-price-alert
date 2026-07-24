"""Safe, shared Rich footnotes for terminal panels."""

from __future__ import annotations

import re
from collections.abc import Iterable

from rich.console import Group, RenderableType
from rich.table import Table
from rich.text import Text


_PROSE_WHITESPACE = re.compile(r"\s+")
_CONTROL_WHITESPACE = re.compile(r"[\t\r\n\f\v]+")


def _normalize(note: str) -> str:
    """Normalize producer text without changing spacing inside paired backticks."""
    stripped = note.strip()
    if not stripped:
        return ""

    # An unmatched backtick has no semantic meaning. Normalize the whole note as prose
    # and leave the character visible.
    if stripped.count("`") % 2:
        normalized = _PROSE_WHITESPACE.sub(" ", stripped)
    else:
        parts = stripped.split("`")
        normalized_parts: list[str] = []
        for index, part in enumerate(parts):
            if index % 2:
                # Code-like text keeps ordinary spaces, but producers cannot force line
                # breaks or tab stops into the panel.
                normalized_parts.append(_CONTROL_WHITESPACE.sub(" ", part))
            else:
                normalized_parts.append(_PROSE_WHITESPACE.sub(" ", part))
        normalized = "`".join(normalized_parts).strip()

    if normalized and not normalized.endswith((".", "!", "?")):
        normalized += "."
    return normalized


def inline_text(value: str, *, style: str = "") -> Text:
    """Safely style paired backtick spans without parsing general Rich markup."""
    body = Text(style=style)
    if value.count("`") % 2:
        body.append(value)
        return body

    for index, part in enumerate(value.split("`")):
        body.append(part, style="dim cyan" if index % 2 else style)
    return body


class FootnoteRegistry:
    """Register, reference, and safely render numbered panel footnotes."""

    def __init__(self) -> None:
        self._notes: list[str] = []

    @property
    def notes(self) -> tuple[str, ...]:
        """Return an immutable snapshot of the registered normalized notes."""
        return tuple(self._notes)

    def add(self, note: str) -> str:
        """Register one nonblank note and return its leading-space reference markup."""
        normalized = _normalize(note)
        if not normalized:
            return ""
        if normalized in self._notes:
            return f" [dim default][{self._notes.index(normalized) + 1}][/dim default]"
        self._notes.append(normalized)
        return f" [dim default][{len(self._notes)}][/dim default]"

    def add_many(self, notes: Iterable[str]) -> str:
        """Register notes in order and return their combined reference markup."""
        references = [reference.lstrip() for note in notes if (reference := self.add(note))]
        return f" {' '.join(references)}" if references else ""

    def clear(self) -> None:
        """Remove every registered note."""
        self._notes.clear()

    def render(self) -> RenderableType:
        """Return the complete hanging-indent footnote block."""
        grid = Table.grid(expand=True, padding=(0, 1))
        grid.add_column("Reference", justify="right", no_wrap=True)
        grid.add_column("Footnote", ratio=1, overflow="fold")
        for number, note in enumerate(self._notes, 1):
            # Panel padding contributes another cell, retaining the established
            # three-space visual inset (two cells owned by the footnote block).
            grid.add_row(Text(f"  [{number}]", style="dim"), inline_text(note, style="dim"))
        return Group(Text(""), grid)


__all__ = ["FootnoteRegistry", "inline_text"]
