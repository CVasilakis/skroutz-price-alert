import os
import logging
from dataclasses import dataclass
from collections.abc import Sequence

from rich.console import Console

from core.constants import CONFIG_DIR, EXIT_CODE_ENV_ERROR
from core.exceptions import EnvFileError, UpdateCheckError
from core.utils import check_env_file, check_for_updates, classify_notification_urls
from core.general import resolve_general_settings
from core.logger import get_target_logger
from core.ui.panel import StatusPanelBuilder


@dataclass(frozen=True)
class ConfigView:
    """Presentation summary of a target's products-config health (the 'Config' row).

    The single rendering-agnostic model behind the 'Config' row shown atop each Service
    Status panel (``--status``) and Scraping panel (a run). Built by :func:`config_view`
    so the icon/value/footnote decision lives in one place, and consumed by
    :func:`add_config_row` (StatusPanelBuilder panels) and the interactive strategy.

    Attributes:
        icon (str): The status icon (``✅`` / ``🟡`` / ``❗``).
        value (str): The row value as Rich markup, without any footnote reference.
        footnote (str | None): The explanatory note, or ``None`` when healthy.
        has_warning (bool): True for a faulty or failed load (drives the silent-log level).
    """
    icon: str
    value: str
    footnote: str | None = None
    has_warning: bool = False


def config_view(count: int, faulty_indices: Sequence[int] = (), error: str | None = None) -> ConfigView:
    """Builds the :class:`ConfigView` for a target from its load outcome.

    Args:
        count (int): The number of loaded items (ignored when ``error`` is set).
        faulty_indices (Sequence[int]): 1-based indices of items failing validation.
        error (str | None): The storage failure message, if the load failed.

    Returns:
        ConfigView: The icon/value/footnote for the 'Config' row.
    """
    if error is not None:
        return ConfigView("❗", "[red]Failed[/red]", error, has_warning=True)
    if faulty_indices:
        note = f"Problematic items found at JSON index: {', '.join(map(str, faulty_indices))}."
        value = f"{count} loaded, [yellow]{len(faulty_indices)} misconfigured[/yellow]"
        return ConfigView("🟡", value, note, has_warning=True)
    return ConfigView("✅", f"{count} loaded", None, has_warning=False)


def add_config_row(panel: StatusPanelBuilder, view: ConfigView) -> None:
    """Renders a :class:`ConfigView` as the 'Config' row on a StatusPanelBuilder panel.

    Used atop the ``--status`` Service Status panel; the interactive Scraping panel
    renders the same view through the strategy's own footnote mechanism.

    Args:
        panel (StatusPanelBuilder): The panel being built.
        view (ConfigView): The resolved products-config health.
    """
    ref = panel.add_note_ref(view.footnote) if view.footnote else ""
    panel.add_row(view.icon, "Monitored Items", f"{view.value}{ref}")


def add_setting_row(panel: StatusPanelBuilder, view) -> None:
    """Renders one resolved setting as a row in the panel's settings section.

    A valid, explicitly-set value shows as ``✅``. An unset value (or a missing config)
    shows its active default as ``✅`` with a dim ``(default)`` marker. An invalid value
    shows the default it fell back to as ``🟡`` plus a footnote naming the problem.

    Shared by the per-scraper Service Status panels (``--status``) and the general
    settings rows of the Configuration Check panel, so every settings row renders
    identically.

    Args:
        panel (StatusPanelBuilder): The panel being built.
        view (SettingView): The resolved setting (label, display value, status, footnote).
    """
    note_ref = panel.add_note_ref(view.footnote) if view.has_warning else ""
    value = view.render_value(note_ref, default_marker=" [dim](default)[/dim]")
    panel.add_row(view.icon, view.label, value)


def _append_version_row(panel: StatusPanelBuilder) -> None:
    """Appends the software-version row, querying the remote for updates."""
    try:
        if check_for_updates():
            ref = panel.add_note_ref("Run `./update.sh` to install the latest version.")
            panel.add_row("🟡", "Software Version", f"Update available!{ref}")
        else:
            panel.add_row("✅", "Software Version", "Up to date")
    except UpdateCheckError:
        ref = panel.add_note_ref("Check your internet connection and retry shortly.")
        panel.add_row("🟡", "Software Version", f"Could not check for updates{ref}")


def _append_general_rows(panel: StatusPanelBuilder) -> None:
    """Appends one row per project-wide setting, resolved from ``config/general.json``.

    Mirrors the per-scraper settings section of the Service Status panels (block-level
    warning first, then one row per setting), so the general settings get the same
    invalid-value UX. Iterates whatever ``GENERAL_SETTING_SPECS`` declares - a future
    general setting needs no change here.
    """
    resolved = resolve_general_settings(CONFIG_DIR)
    if resolved.block_warning:
        ref = panel.add_note_ref(resolved.block_warning)
        panel.add_row("🟡", "General Settings", f"[yellow]Block ignored{ref}[/yellow]")
    for view in resolved.views():
        add_setting_row(panel, view)


def _append_env_row(panel: StatusPanelBuilder) -> None:
    """Appends the .env row summarizing configured Apprise notification URLs."""
    env_error_msg = ""
    try:
        check_env_file()
    except EnvFileError as e:
        env_error_msg = str(e)

    valid_urls, invalid_urls = classify_notification_urls(os.environ.get("NOTIFICATION_URLS", ""))

    if valid_urls or invalid_urls:
        if not invalid_urls:
            panel.add_row("✅", "Notifications", f"{len(valid_urls)} valid URL(s)")
        else:
            ref = panel.add_note_ref("Run `./scripts/run.sh --ping` for more details.")
            panel.add_row("🟡", "Notifications", f"{len(valid_urls)} valid URL(s), [yellow]{len(invalid_urls)} invalid{ref}[/yellow]")
    else:
        ref = panel.add_note_ref(env_error_msg or "No notification URLs found.")
        panel.add_row("❗", "Notifications", f"[red]Not configured{ref}[/red]")


def render_config_panel(console: Console) -> None:
    """Builds and renders the shared 'Configuration Check' panel (global checks only).

    Runs the update check, the .env check and the general (project-wide) settings
    resolution behind a single spinner, then renders the panel (the general settings
    rows follow the .env row).
    Per-scraper products-config health is intentionally not shown here — it is surfaced
    as a 'Config' row atop each Service Status panel (``--status``) and Scraping panel
    (a run). This is the single presentation path shared by the interactive scraper run
    (main.py) and the health check (status.py); it performs no config-file I/O itself.

    Args:
        console (Console): The Rich console to render to.
    """
    panel = StatusPanelBuilder("Configuration Check")

    with console.status("[bold green]Checking for updates...[/bold green]", spinner="dots"):
        _append_version_row(panel)
        _append_env_row(panel)
        _append_general_rows(panel)

    panel.render(console)


def _silent_preflight(targets_to_run: list) -> int | None:
    """Validates the .env for a background (``--quiet``) run, logging to file.

    A missing/invalid ``.env`` is fatal for a service (it cannot notify), so it gates
    here. Per-target products-config failures are handled by the orchestrator — it skips
    just the broken target and surfaces the error in that scraper's log/panel, matching
    the interactive run — so they are not gated globally here.

    Args:
        targets_to_run (list): The targets being run (for per-target logging).

    Returns:
        int | None: A fatal exit code to abort on, or None to proceed.
    """
    try:
        check_env_file()
    except EnvFileError as e:
        for target in targets_to_run:
            get_target_logger(target, True).error(f"❗ Env configuration failed: {e}")
        logging.critical(f"Env configuration failed: {e}")
        return EXIT_CODE_ENV_ERROR

    _, invalid_urls = classify_notification_urls(os.environ.get("NOTIFICATION_URLS", ""))
    if invalid_urls:
        for target in targets_to_run:
            get_target_logger(target, True).warning(
                f"❗ {len(invalid_urls)} invalid notification URL(s) detected in .env file."
            )

    return None


def preflight(console: Console | None, targets_to_run: list, quiet: bool) -> int | None:
    """Single preflight-validation entry point shared by both run modes.

    Renders/logs the global configuration verdict and decides whether to abort. Storage
    was already read by ``load_targets``; per-target products-config failures are handled
    per-target by the orchestrator (skip just that scraper), so the only fatal gate here
    is a missing/invalid ``.env`` in quiet/service mode.

    Gating policy (intentionally mode-specific):
        * A missing/invalid ``.env`` is fatal only in quiet/service mode
          (``EXIT_CODE_ENV_ERROR``); interactively it is surfaced as a panel row so
          the user can see it and still proceed.

    Args:
        console (Console | None): The console for interactive rendering; unused
            (may be None) in quiet mode.
        targets_to_run (list): The targets being run.
        quiet (bool): Whether this is a silent/background run.

    Returns:
        int | None: A fatal exit code to abort on, or None to proceed.
    """
    if quiet:
        return _silent_preflight(targets_to_run)
    assert console is not None, "console is required for interactive (non-quiet) preflight"
    render_config_panel(console)
    return None
