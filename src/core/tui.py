import logging
from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Optional, Sequence, Union
from rich.console import Console, Group
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from rich.markup import escape
from rich.spinner import Spinner
from rich.progress_bar import ProgressBar

from scrapers.base.settings import SettingView
from config_check import ConfigView
from panel import uniform_column_widths

# Accepts a single note string, a list of note strings, or None.
Notes = Union[str, List[str], None]


class PriceOutcome(Enum):
    """The outcome of a successful price scrape, used to pick row styling.

    The orchestrator decides which bucket a result falls into (business logic);
    each strategy maps the bucket to its own icon/formatting (presentation).
    """
    DROP = "drop"            # below target -> celebrate + notify
    NO_TARGET = "no_target"  # no real target set (target price is 0.0)
    OK = "ok"                # at or above a real target


class ExecutionStrategy(ABC):
    """Abstract base class for execution UI and logging strategies."""

    @staticmethod
    def _outcome_icon(outcome: PriceOutcome) -> str:
        """Maps a price outcome to its status icon (shared by all strategies)."""
        return {PriceOutcome.DROP: "🎉", PriceOutcome.NO_TARGET: "🟡"}.get(outcome, "✅")

    @staticmethod
    def _normalize_notes(notes: Notes) -> List[str]:
        """Normalizes the notes parameter into a flat list of strings.

        Accepts None, a single string, or a list and always returns a
        (possibly empty) list suitable for iteration.

        Args:
            notes (Notes): The raw notes value.

        Returns:
            List[str]: A list of note strings (empty when notes is None).
        """
        if notes is None:
            return []

        def _ensure_period(s: str) -> str:
            s_stripped = s.strip()
            if s_stripped and not s_stripped.endswith('.'):
                return s_stripped + '.'
            return s_stripped

        if isinstance(notes, str):
            return [_ensure_period(notes)] if notes else []
        return [_ensure_period(n) for n in notes if n]

    @abstractmethod
    def start_target(self, target_name: str, target_logger: logging.Logger,
                     settings_view: Sequence[SettingView] = (),
                     block_warning: Optional[str] = None,
                     config_view: Optional[ConfigView] = None) -> None:
        """Called when a new scraping target begins.

        Args:
            target_name (str): The target being scraped.
            target_logger (logging.Logger): The target's logger (used by the silent strategy).
            settings_view (Sequence[SettingView]): The target's resolved settings,
                rendered as a section atop the interactive panel (and logged once by the
                silent strategy). The orchestrator resolves these so the strategies stay
                presentation-only.
            block_warning (Optional[str]): A one-line message when the config's
                ``settings`` block was malformed (present but not an object) and ignored,
                surfaced once above the per-setting rows; ``None`` when well-formed.
            config_view (Optional[ConfigView]): The target's products-config health,
                rendered as the leading 'Config' row of the section (and logged once by the
                silent strategy); ``None`` when unavailable (e.g. missing dependencies).
        """
        pass

    @abstractmethod
    def start_scraping(self, name: str, attempt: int = 1, max_retries: int = 1) -> None:
        """Called when scraping for a specific product begins.

        Args:
            name (str): The product name being scraped.
            attempt (int): The 1-based attempt number; values above 1 indicate a retry.
            max_retries (int): The total number of attempts that will be made.
        """
        pass

    @abstractmethod
    def complete_scraping(self) -> None:
        """Called when scraping for a specific product ends."""
        pass

    @abstractmethod
    def log_result(self, icon: str, name: str, value: str, notes: Notes = None, attempt_notes: Notes = None) -> None:
        """Logs a successful or informational result.

        Args:
            attempt_notes (Notes): Per-attempt footnotes for preceding failed retries.
                Interactive renders them; silent ignores them (already streamed via
                log_attempt) to avoid duplicating per-attempt detail in the log file.
        """
        pass

    @abstractmethod
    def log_price_result(self, name: str, price: float, currency: str, target: float, outcome: PriceOutcome, notes: Notes = None, attempt_notes: Notes = None) -> None:
        """Logs a completed price scrape, choosing icon/formatting from the outcome.

        The orchestrator passes semantic values only; each strategy owns how the
        price/target are styled (colors for interactive, plain text for silent).

        Args:
            name (str): The product name.
            price (float): The scraped current price.
            currency (str): The currency symbol/label (e.g. ``"€"``).
            target (float): The product's target price (0.0 means no real threshold).
            outcome (PriceOutcome): Which bucket the result falls into.
            notes (Notes): Result footnotes (e.g. notification status).
            attempt_notes (Notes): Per-attempt footnotes for preceding failed retries.
        """
        pass

    @abstractmethod
    def log_warning(self, name: str, warning_str: str, notes: Notes = None, attempt_notes: Notes = None) -> None:
        """Logs a warning message for a specific product.

        Args:
            attempt_notes (Notes): Per-attempt footnotes for preceding failed retries.
                Interactive renders them; silent ignores them (already streamed via
                log_attempt).
        """
        pass

    @abstractmethod
    def log_error(self, name: str, error_str: str, notes: Notes = None) -> None:
        """Logs an error message for a specific product."""
        pass

    @abstractmethod
    def log_attempt(self, name: str, attempt: int, max_retries: int, detail: str) -> None:
        """Reports a single failed scrape attempt that will be retried or is terminal.

        The interactive strategy collapses these into the product's single row and
        ignores this call; the silent strategy logs one line per attempt so that
        background-run logs retain full per-attempt detail.
        """
        pass

    @abstractmethod
    def log_failure(self, name: str, error_type: str, attempt_notes: Notes = None, extra_notes: Notes = None) -> None:
        """Logs the terminal failure of a product after all retries are exhausted.

        Args:
            name (str): The product name.
            error_type (str): The exception type of the final failed attempt.
            attempt_notes (Notes): Per-attempt footnotes for the collapsed interactive
                row (already streamed to the log file by the silent strategy).
            extra_notes (Notes): Additional notes (e.g. a stale-tracking warning) shown
                by every strategy.
        """
        pass

    @abstractmethod
    def start_sleep(self, total_delay: float, retry_attempt: int = 0, max_retries: int = 0) -> None:
        """Called when a sleep/delay period begins.

        Args:
            total_delay (float): The total delay in seconds.
            retry_attempt (int): The 1-based number of the upcoming retry attempt, or 0
                for the normal pacing delay between products.
            max_retries (int): The total number of attempts (used with retry_attempt).
        """
        pass

    @abstractmethod
    def update_sleep(self, remaining: float) -> None:
        """Called to update the progress of an ongoing sleep period."""
        pass

    @abstractmethod
    def complete_sleep(self, actual_delay: float) -> None:
        """Called when a sleep/delay period ends."""
        pass

    @abstractmethod
    def complete_target(self) -> None:
        """Called when a scraping target completes its execution."""
        pass

    @abstractmethod
    def log_interrupt(self, message: str) -> None:
        """Logs an interruption or early termination signal."""
        pass


class InteractiveExecutionStrategy(ExecutionStrategy):
    """Execution strategy that updates a rich console live display for interactive usage."""
    def __init__(self):
        """Initializes the interactive strategy state."""
        self.console = Console()
        self.live = None
        self.rows = []
        self.settings_rows = []
        self.notes = []
        self.target_name = ""
        self.sleep_total = 0.0
        self.sleep_remaining = 0.0
        self.sleep_label = "Sleeping"
        self.is_sleeping = False
        self.scraping_name = ""
        self.scraping_attempt = 1
        self.scraping_max = 1
        self.is_complete = False

    def start_target(self, target_name: str, target_logger: logging.Logger,
                     settings_view: Sequence[SettingView] = (),
                     block_warning: Optional[str] = None,
                     config_view: Optional[ConfigView] = None) -> None:
        """Starts a new live display session for the given target."""
        if self.live:
            self.live.stop()

        self.target_name = target_name
        self.rows = []
        self.notes = []
        self.is_sleeping = False
        self.scraping_name = ""
        self.scraping_attempt = 1
        self.scraping_max = 1
        self.sleep_label = "Sleeping"
        self.is_complete = False

        # Build the static settings section after resetting notes, so its invalid-value
        # footnotes take the first reference numbers, ahead of the scraping rows.
        self.settings_rows = self._build_settings_rows(settings_view, block_warning, config_view)

        self.live = Live(self._generate_panel(), refresh_per_second=10)
        self.live.start()

    def _build_settings_rows(self, settings_view: Sequence[SettingView],
                             block_warning: Optional[str] = None,
                             config_view: Optional[ConfigView] = None) -> List[tuple]:
        """Renders the products-config health + resolved settings into ``(icon, label, value)`` rows.

        The 'Config' row (products-config health) leads the section when ``config_view`` is
        set. A valid setting shows as ``✅``; an unset value (or missing config) shows its
        active default as ``✅`` with a dim ``(default)`` marker; an invalid value shows
        the default it fell back to as ``🟡`` plus a footnote naming the problem. A
        malformed ``settings`` block (when ``block_warning`` is set) is surfaced once as a
        leading ``🟡`` row, since every per-setting row below it then shows its default.
        """
        rows: List[tuple] = []
        if config_view is not None:
            refs = self._build_note_refs(config_view.footnote) if config_view.footnote else ""
            rows.append((config_view.icon, "Config", f"{config_view.value}{refs}"))
        if block_warning:
            rows.append(("🟡", "Settings", f"[yellow]Block ignored[/yellow]{self._build_note_refs(block_warning)}"))
        for view in settings_view:
            if view.has_warning:
                value = f"{escape(view.display_value)}{self._build_note_refs(view.footnote)}"
            else:
                value = escape(view.display_value)
                if view.is_default:
                    value += " [dim](default)[/dim]"
            rows.append((view.icon, escape(view.label), value))
        return rows

    @staticmethod
    def _new_display_table(col_widths: Optional[dict] = None) -> Table:
        """Builds an empty 3-column (icon, name, value) display table.

        Args:
            col_widths (Optional[dict]): Fixed widths per column index, shared between the
                settings and scraping tables so their value columns line up across the divider.
                A ``None`` width leaves the column auto-sized.
        """
        col_widths = col_widths or {}
        table = Table(show_header=False, box=None, padding=(0, 2))
        table.add_column("Icon", justify="center", width=col_widths.get(0))
        table.add_column("Name", style="bold", width=col_widths.get(1))
        table.add_column("Value")
        return table

    def start_scraping(self, name: str, attempt: int = 1, max_retries: int = 1) -> None:
        """Starts scraping the specified product and updates the live display.

        The spinner row stays visible across retries; from the second attempt on it
        shows an ``(attempt/max)`` counter so a single evolving row conveys progress.
        """
        self.scraping_name = self._truncate_name(name)
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

    def _truncate_name(self, name: str, max_len: int = 30) -> str:
        """Truncates a name string to fit within the live display panel."""
        if len(name) > max_len:
            return name[:max_len - 3] + "..."
        return name

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
        normalized = self._normalize_notes(notes)
        if not normalized:
            return ""
        refs = []
        for note in normalized:
            self.notes.append(note)
            refs.append(f"[dim default][{len(self.notes)}][/dim default]")
        return " " + " ".join(refs)

    def _generate_panel(self) -> Panel:
        """Generates the rich panel to be rendered on the live display."""
        # Assemble the live scraping rows (results, then the transient sleep/scraping row)
        # before building the table, so their labels feed the shared column sizing below.
        display_rows: List[tuple] = list(self.rows)
        if self.is_sleeping:
            grid = Table.grid(padding=(0, 1))
            grid.add_row(
                ProgressBar(total=self.sleep_total, completed=self.sleep_remaining, width=30, style="grey37", complete_style="cyan", finished_style="cyan"),
                f"[cyan]{self.sleep_remaining:.1f}s[/cyan]"
            )
            display_rows.append(("⏳", self.sleep_label, grid))
        elif self.scraping_name:
            if self.scraping_attempt > 1:
                scrape_text = f"[cyan]Scraping ({self.scraping_attempt}/{self.scraping_max})...[/cyan]"
            else:
                scrape_text = "[cyan]Scraping...[/cyan]"
            display_rows.append((Spinner("dots", style="cyan"), escape(self.scraping_name), scrape_text))

        # Size the icon/label columns once across both sections so the value column starts at
        # the same position above and below the divider. The transient sleep/scraping row is
        # included so the columns don't jump as it appears and disappears.
        widths = uniform_column_widths(self.settings_rows + display_rows)

        display_table = self._new_display_table(widths)
        for row in display_rows:
            display_table.add_row(*row)

        # The static settings section (set at start_target) renders above a divider,
        # then the live scraping rows below it.
        if self.settings_rows:
            settings_table = self._new_display_table(widths)
            for row in self.settings_rows:
                settings_table.add_row(*row)
            body = Group(settings_table, Rule(style="dim"), display_table)
        else:
            body = display_table

        if self.notes:
            notes_group = [""]
            for i, note in enumerate(self.notes, 1):
                notes_group.append(f"  [{i}] {escape(note)}")
            renderable = Group(body, Text.from_markup("\n".join(notes_group), style="dim"))
        else:
            renderable = body

        has_green = False
        has_red = False
        has_yellow = False

        # Settings rows count toward the border color too, so an invalid setting tints
        # the panel yellow.
        for row in (self.settings_rows + self.rows):
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

        return Panel(renderable, title=f"[bold]{self.target_name.capitalize()} Scraping[/bold]", border_style=panel_color, width=75)

    def log_result(self, icon: str, name: str, value: str, notes: Notes = None, attempt_notes: Notes = None) -> None:
        """Logs a standard result directly into the rich table."""
        refs = self._build_note_refs(self._normalize_notes(attempt_notes) + self._normalize_notes(notes))
        self.rows.append((icon, escape(self._truncate_name(name)), f"{value}{refs}"))
        if self.live:
            self.live.update(self._generate_panel())

    def log_price_result(self, name: str, price: float, currency: str, target: float, outcome: PriceOutcome, notes: Notes = None, attempt_notes: Notes = None) -> None:
        """Renders a price result row, coloring the price/target per the outcome."""
        price_str = f"{price} {currency}"
        target_str = f"(Target: {target} {currency})"
        if outcome == PriceOutcome.DROP:
            value = f"[bold green]{price_str}[/bold green] {target_str}"
        elif outcome == PriceOutcome.NO_TARGET:
            value = f"{price_str} [yellow]{target_str}[/yellow]"
        else:
            value = f"{price_str} {target_str}"
        self.log_result(self._outcome_icon(outcome), name, value, notes, attempt_notes)

    def log_warning(self, name: str, warning_str: str, notes: Notes = None, attempt_notes: Notes = None) -> None:
        """Logs a warning entry to the live display."""
        refs = self._build_note_refs(self._normalize_notes(attempt_notes) + self._normalize_notes(notes))
        self.rows.append(("🟡", escape(self._truncate_name(name)), f"[yellow]{escape(warning_str)}{refs}[/yellow]"))
        if self.live:
            self.live.update(self._generate_panel())

    def log_error(self, name: str, error_str: str, notes: Notes = None) -> None:
        """Logs an error entry to the live display."""
        refs = self._build_note_refs(notes)
        self.rows.append(("❗", escape(self._truncate_name(name)), f"{escape(error_str)}{refs}"))
        if self.live:
            self.live.update(self._generate_panel())

    def log_attempt(self, name: str, attempt: int, max_retries: int, detail: str) -> None:
        """Ignored: failed attempts are collapsed into the product's single row."""
        pass

    def log_failure(self, name: str, error_type: str, attempt_notes: Notes = None, extra_notes: Notes = None) -> None:
        """Logs the terminal failure as a single red row with one footnote per attempt."""
        notes = self._normalize_notes(attempt_notes) + self._normalize_notes(extra_notes)
        self.log_error(name, error_type, notes)

    def start_sleep(self, total_delay: float, retry_attempt: int = 0, max_retries: int = 0) -> None:
        """Starts the sleep state and renders a progress bar."""
        self.is_sleeping = True
        self.sleep_total = total_delay
        self.sleep_remaining = total_delay
        self.sleep_label = f"Retrying ({retry_attempt}/{max_retries})" if retry_attempt else "Sleeping"
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


class SilentExecutionStrategy(ExecutionStrategy):
    """Execution strategy that operates without an active rich live UI, logging purely to a target logger."""
    def __init__(self):
        """Initializes the silent strategy execution."""
        self.target_logger = None

    @staticmethod
    def _format_notes_suffix(notes_list: list[str]) -> str:
        """Formats a list of note strings into a parenthesized suffix for log lines.

        Each note is wrapped in its own parentheses and appended to the line,
        e.g., ' (First note) (Second note)'.

        Args:
            notes_list (list[str]): The normalized list of note strings.

        Returns:
            str: The formatted suffix, or an empty string when the list is empty.
        """
        if not notes_list:
            return ""
        return " " + " ".join(f"({n})" for n in notes_list)

    def start_target(self, target_name: str, target_logger: logging.Logger,
                     settings_view: Sequence[SettingView] = (),
                     block_warning: Optional[str] = None,
                     config_view: Optional[ConfigView] = None) -> None:
        """Sets the logger context and records the effective config + settings to the file log.

        Logging the products-config health and the resolved settings once at target start
        gives background (service) runs a record of what was in effect, and surfaces a
        faulty/failed config or an invalid setting as a warning (replacing the ad-hoc
        retention warning the logger used to emit). A malformed ``settings`` block is
        logged once as a warning ahead of the rows.
        """
        self.target_logger = target_logger
        if config_view is not None:
            value = Text.from_markup(config_view.value).plain
            if config_view.has_warning:
                note = f" ({config_view.footnote})" if config_view.footnote else ""
                target_logger.warning(f"❗ Config: {value}{note}")
            else:
                target_logger.info(f"🗄️  Config: {value}")
        if block_warning:
            target_logger.warning(f"❗ Settings: {block_warning}")
        for view in settings_view:
            if view.has_warning:
                target_logger.warning(f"❗ {view.label}: {view.display_value} ({view.footnote})")
            else:
                suffix = " (default)" if view.is_default else ""
                target_logger.info(f"⚙️  {view.label}: {view.display_value}{suffix}")

    def start_scraping(self, name: str, attempt: int = 1, max_retries: int = 1) -> None:
        """Does nothing in silent mode."""
        pass

    def complete_scraping(self) -> None:
        """Does nothing in silent mode."""
        pass

    def log_result(self, icon: str, name: str, value: str, notes: Notes = None, attempt_notes: Notes = None) -> None:
        """Logs an informational result to the target logger.

        ``attempt_notes`` are ignored here; the silent strategy already streamed each
        failed attempt via log_attempt, so re-listing them would duplicate log lines.
        """
        if self.target_logger:
            clean_value = Text.from_markup(value).plain
            suffix = self._format_notes_suffix(self._normalize_notes(notes))
            self.target_logger.info(f"{icon} {name}: {clean_value}{suffix}")

    def log_price_result(self, name: str, price: float, currency: str, target: float, outcome: PriceOutcome, notes: Notes = None, attempt_notes: Notes = None) -> None:
        """Logs a price result as plain text (``attempt_notes`` ignored; already streamed)."""
        if self.target_logger:
            value = f"{price} {currency} (Target: {target} {currency})"
            suffix = self._format_notes_suffix(self._normalize_notes(notes))
            self.target_logger.info(f"{self._outcome_icon(outcome)} {name}: {value}{suffix}")

    def log_warning(self, name: str, warning_str: str, notes: Notes = None, attempt_notes: Notes = None) -> None:
        """Logs a warning to the target logger (``attempt_notes`` ignored; already streamed)."""
        if self.target_logger:
            suffix = self._format_notes_suffix(self._normalize_notes(notes))
            self.target_logger.warning(f"❗ {name}: {warning_str}{suffix}")

    def log_error(self, name: str, error_str: str, notes: Notes = None) -> None:
        """Logs an error to the target logger."""
        if self.target_logger:
            suffix = self._format_notes_suffix(self._normalize_notes(notes))
            self.target_logger.error(f"❗ {name}: {error_str}{suffix}")

    def log_attempt(self, name: str, attempt: int, max_retries: int, detail: str) -> None:
        """Logs a single failed attempt, preserving per-attempt detail in the log file."""
        if self.target_logger:
            self.target_logger.warning(f"❗ {name}: Attempt {attempt}/{max_retries} FAILED ({detail})")

    def log_failure(self, name: str, error_type: str, attempt_notes: Notes = None, extra_notes: Notes = None) -> None:
        """Logs the terminal failure line; per-attempt detail was already streamed via log_attempt."""
        if self.target_logger:
            suffix = self._format_notes_suffix(self._normalize_notes(extra_notes))
            self.target_logger.error(f"❗ {name}: All attempts failed ({error_type}){suffix}")

    def start_sleep(self, total_delay: float, retry_attempt: int = 0, max_retries: int = 0) -> None:
        """Does nothing in silent mode."""
        pass

    def update_sleep(self, remaining: float) -> None:
        """Does nothing in silent mode."""
        pass

    def complete_sleep(self, actual_delay: float) -> None:
        """Does nothing in silent mode."""
        pass


    def complete_target(self) -> None:
        """Clears the target logger context."""
        self.target_logger = None

    def log_interrupt(self, message: str) -> None:
        """Logs an interrupt message to the target logger or root logger."""
        if self.target_logger:
            self.target_logger.info(f"🛑 {message}")
        else:
            logging.info(f"🛑 {message}")
