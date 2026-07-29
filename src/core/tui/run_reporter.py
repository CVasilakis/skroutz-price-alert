import logging
from collections.abc import Sequence

from rich.console import Console, Group
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.progress_bar import ProgressBar
from rich.rule import Rule
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text

from core import messages
from core.application.contracts import ConfigOutcome, Notes, PriceOutcome, RunReporter
from core.presentation import SettingView, resolved_setting_views
from core.settings import ResolvedSettings, SettingStatus
from core.tui.config_check import ConfigView, config_view
from core.tui.footnotes import FootnoteRegistry, inline_text
from core.tui.panel import PANEL_WIDTH, PanelTableLayout


class InteractiveRunReporter(RunReporter):
    """Rich live reporter for an interactive scraping run."""

    def __init__(self, width: int = PANEL_WIDTH):
        """Initializes the interactive reporter state."""
        self.width = width
        self.console = Console()
        self.live = None
        self.rows = []
        self.settings_rows = []
        self._footnotes = FootnoteRegistry()
        self.target_name = ""
        self.sleep_total = 0.0
        self.sleep_remaining = 0.0
        self.sleep_label = "Sleeping"
        self.is_sleeping = False
        self.scraping_name = ""
        self.scraping_attempt = 1
        self.scraping_max = 1
        self.is_complete = False

    @property
    def notes(self) -> tuple[str, ...]:
        """Return an immutable snapshot of the current target's footnotes."""
        return self._footnotes.notes

    def start_target(
        self,
        target_name: str,
        target_logger: logging.Logger,
        settings: ResolvedSettings,
        config: ConfigOutcome,
    ) -> None:
        """Starts a new live display session for the given target."""
        if self.live:
            self.live.stop()

        self.target_name = target_name
        self.rows = []
        self._footnotes.clear()
        self.is_sleeping = False
        self.scraping_name = ""
        self.scraping_attempt = 1
        self.scraping_max = 1
        self.sleep_label = "Sleeping"
        self.is_complete = False

        # Build the static settings section after resetting notes, so its invalid-value
        # footnotes take the first reference numbers, ahead of the scraping rows.
        view = config_view(
            config.loaded_count,
            list(config.faulty_indices),
            config.error,
            config.source_path,
            config.diagnostic_saved,
        )
        self.settings_rows = self._build_settings_rows(resolved_setting_views(settings), view)

        self.live = Live(self._generate_panel(), refresh_per_second=10)
        self.live.start()

    def _build_settings_rows(
        self, settings_view: Sequence[SettingView], config_view: ConfigView
    ) -> list[tuple]:
        """Renders the target-configuration health + resolved settings into ``(icon, label, value)`` rows.

        The 'Config' row (target-configuration health) leads the section when ``config_view`` is
        set. A valid setting shows as ``✅``; an unset value (or missing config) shows its
        active default as ``✅`` with a dim ``(default)`` marker; an invalid value shows
        the default it fell back to as ``🟡`` plus a footnote naming the problem. A
        """
        rows: list[tuple] = []
        refs = self._build_note_refs(config_view.footnote) if config_view.footnote else ""
        rows.append((config_view.icon, "Tracked Items", f"{config_view.value}{refs}"))
        for view in settings_view:
            note_ref = self._build_note_refs(view.footnote) if view.has_warning else ""
            value = escape(view.display_value)
            if view.has_warning:
                value = f"{value}{note_ref}"
            elif view.is_default:
                value = f"{value} [dim](default)[/dim]"
            icon = "🟡" if view.status in (SettingStatus.INVALID, SettingStatus.MISSING) else "✅"
            rows.append((icon, escape(view.label), value))
        return rows

    def start_scraping(self, name: str, attempt: int = 1, max_retries: int = 1) -> None:
        """Starts scraping the specified item and updates the live display.

        The spinner row stays visible across retries; from the second attempt on it
        shows an ``(attempt/max)`` counter so a single evolving row conveys progress.
        """
        self.scraping_name = name
        self.scraping_attempt = attempt
        self.scraping_max = max_retries
        if self.live:
            self.live.update(self._generate_panel())

    def complete_scraping(self) -> None:
        """Clears the scraping spinner state without refreshing the display.

        The display update is deferred to the next state-changing call
        (e.g., log_result, start_sleep) so the spinner row is replaced
        atomically, avoiding a brief visual contraction of the panel.
        """
        self.scraping_name = ""

    def _build_note_refs(self, notes: Notes) -> str:
        """Registers one or more footnotes and returns their combined reference markup.

        Each note is appended to the internal notes list and assigned a sequential
        number. The returned string contains all references joined together
        (e.g., ' [1] [2]').

        Args:
            notes (Notes): A single note string, a list, or None.

        Returns:
            str: The concatenated Rich markup references, or an empty string.
        """
        if notes is None:
            return ""
        return self._footnotes.add_many([notes] if isinstance(notes, str) else notes)

    @staticmethod
    def _note_list(notes: Notes) -> list[str]:
        """Coerce the presentation-neutral note union without changing its text."""
        if notes is None:
            return []
        return [notes] if isinstance(notes, str) else list(notes)

    def _generate_panel(self) -> Panel:
        """Generates the rich panel to be rendered on the live display."""
        # Assemble the live scraping rows (results, then the transient sleep/scraping row)
        # before building the table, so their labels feed the shared column sizing below.
        display_rows: list[tuple] = list(self.rows)
        if self.is_sleeping:
            grid = Table.grid(padding=(0, 1))
            grid.add_row(
                ProgressBar(
                    total=self.sleep_total,
                    completed=self.sleep_remaining,
                    style="grey37",
                    complete_style="cyan",
                    finished_style="cyan",
                ),
                f"[cyan]{self.sleep_remaining:.1f}s[/cyan]",
            )
            display_rows.append(("⏳", self.sleep_label, grid))
        elif self.scraping_name:
            if self.scraping_attempt > 1:
                scrape_text = (
                    f"[cyan]Scraping ({self.scraping_attempt}/{self.scraping_max})...[/cyan]"
                )
            else:
                scrape_text = "[cyan]Scraping...[/cyan]"
            display_rows.append(
                (Spinner("dots", style="cyan"), escape(self.scraping_name), scrape_text)
            )

        # Allocate all columns once across both sections. The transient sleep/scraping row
        # participates so the layout responds to everything currently visible.
        layout = PanelTableLayout.from_rows(
            self.width,
            self.settings_rows + display_rows,
        )

        display_table = layout.new_table("Name")
        for row in display_rows:
            display_table.add_row(*row)

        # Separate the static settings from live scraping content only when both
        # sections contain rows. Footnotes render independently below the body and
        # must not leave a dangling divider when a target is skipped before scraping.
        if self.settings_rows and display_rows:
            settings_table = layout.new_table("Name")
            for row in self.settings_rows:
                settings_table.add_row(*row)
            body = Group(settings_table, Rule(style="dim"), display_table)
        elif self.settings_rows:
            settings_table = layout.new_table("Name")
            for row in self.settings_rows:
                settings_table.add_row(*row)
            body = settings_table
        else:
            body = display_table

        if self._footnotes.notes:
            renderable = Group(body, self._footnotes.render())
        else:
            renderable = body

        has_green = False
        has_red = False
        has_yellow = False

        # Settings rows count toward the border color too, so an invalid setting tints
        # the panel yellow.
        for row in self.settings_rows + self.rows:
            icon = row[0]
            if icon == "🎉":
                has_green = True
            elif icon in ("❗", "🛑"):
                has_red = True
            elif icon == "🟡":
                has_yellow = True

        if has_green:
            panel_color = "green"
        elif has_red:
            panel_color = "red"
        elif has_yellow:
            panel_color = "yellow"
        elif self.is_complete:
            # On completion with no warnings or errors.
            panel_color = "green"
        else:
            panel_color = "blue"

        return Panel(
            renderable,
            title=f"[bold]{escape(self.target_name)} Scraping[/bold]",
            border_style=panel_color,
            width=self.width,
        )

    def log_result(
        self, icon: str, name: str, value: str, notes: Notes = None, attempt_notes: Notes = None
    ) -> None:
        """Logs a standard result directly into the rich table."""
        refs = self._build_note_refs(self._note_list(attempt_notes) + self._note_list(notes))
        self.rows.append((icon, escape(name), f"{value}{refs}"))
        if self.live:
            self.live.update(self._generate_panel())

    def log_price_result(
        self,
        name: str,
        price: float | None,
        currency: str,
        target: float,
        outcome: PriceOutcome,
        notes: Notes = None,
        attempt_notes: Notes = None,
        delivery_failed: bool = False,
    ) -> None:
        """Renders a price result row, coloring the price/target per the outcome."""
        safe_currency = escape(currency)
        target_str = f"(Target: {target} {safe_currency})"
        if outcome == PriceOutcome.NO_MATCH or price is None:
            value = f"[dim]{messages.ROW_NO_MATCH}[/dim] {target_str}"
        elif outcome == PriceOutcome.DROP:
            value = f"[bold green]{price} {safe_currency}[/bold green] {target_str}"
        elif outcome == PriceOutcome.NO_TARGET:
            value = f"{price} {safe_currency} [yellow]{target_str}[/yellow]"
        else:
            value = f"{price} {safe_currency} {target_str}"
        self.log_result(
            self._outcome_icon(outcome, delivery_failed),
            name,
            value,
            notes,
            attempt_notes,
        )

    @staticmethod
    def _outcome_icon(outcome: PriceOutcome, delivery_failed: bool = False) -> str:
        if delivery_failed:
            return "🟡"
        return {PriceOutcome.DROP: "🎉", PriceOutcome.NO_TARGET: "🟡"}.get(outcome, "✅")

    def log_warning(
        self, name: str, warning_str: str, notes: Notes = None, attempt_notes: Notes = None
    ) -> None:
        """Logs a warning entry to the live display."""
        refs = self._build_note_refs(self._note_list(attempt_notes) + self._note_list(notes))
        value = inline_text(warning_str, style="yellow")
        value.append_text(Text.from_markup(refs))
        self.rows.append(
            (
                "🟡",
                escape(name),
                value,
            )
        )
        if self.live:
            self.live.update(self._generate_panel())

    def log_error(
        self, name: str, error_str: str, notes: Notes = None, attempt_notes: Notes = None
    ) -> None:
        """Logs an error entry to the live display."""
        refs = self._build_note_refs(self._note_list(attempt_notes) + self._note_list(notes))
        value = inline_text(error_str)
        value.append_text(Text.from_markup(refs))
        self.rows.append(("❗", escape(name), value))
        if self.live:
            self.live.update(self._generate_panel())

    def log_attempt(self, name: str, attempt: int, max_retries: int, detail: str) -> None:
        """Ignored: failed attempts are collapsed into the item's single row."""
        pass

    def log_failure(
        self, name: str, error_type: str, attempt_notes: Notes = None, extra_notes: Notes = None
    ) -> None:
        """Logs the terminal failure as a single red row with one footnote per attempt."""
        notes = self._note_list(attempt_notes) + self._note_list(extra_notes)
        self.log_error(name, error_type, notes)

    def start_sleep(self, total_delay: float, retry_attempt: int = 0, max_retries: int = 0) -> None:
        """Starts the sleep state and renders a progress bar."""
        self.is_sleeping = True
        self.sleep_total = total_delay
        self.sleep_remaining = total_delay
        self.sleep_label = (
            f"Retrying ({retry_attempt}/{max_retries})" if retry_attempt else "Sleeping"
        )
        if self.live:
            self.live.update(self._generate_panel())

    def update_sleep(self, remaining: float) -> None:
        """Updates the progress bar with the remaining sleep duration."""
        self.sleep_remaining = remaining
        if self.live:
            self.live.update(self._generate_panel())

    def complete_sleep(self, actual_delay: float) -> None:
        """Clears the sleep progress bar state without refreshing the display.

        The display update is deferred to the next state-changing call
        (e.g., start_scraping, log_result) so the progress bar row is
        replaced atomically, avoiding a brief visual contraction of the panel.
        """
        self.is_sleeping = False

    def complete_target(self) -> None:
        """Stops the live display console for the target.

        Before stopping, the panel is re-rendered one final time in its completed
        state so that a clean run (no warning or error rows) settles on a green
        border instead of the in-progress blue.
        """
        if self.live:
            self.is_complete = True
            self.live.update(self._generate_panel())
            self.live.stop()
            self.live = None
            self.console.print()

    def log_interrupt(self, message: str) -> None:
        """Logs an interruption event."""
        if self.live:
            self.is_sleeping = False
            self.scraping_name = ""
            self.rows.append(("🛑", "Interrupted", escape(message)))
            self.live.update(self._generate_panel())
        else:
            logging.info(f"🛑 {message}", extra={"pad_top": 1, "pad_bottom": 1})
