"""Rich presentation and prompt flow for plugin scaffolding."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import TypeVar, cast

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from core.scrapers.domain import normalize_domain
from core.scrapers.framework.intervals import SUPPORTED_INTERVALS
from core.scrapers.framework.naming import FRAMEWORK_ITEM_KEYS, SNAKE_CASE_KEY
from core.scrapers.framework.settings import framework_setting_specs
from core.scrapers.tooling.scaffold import (
    VALUE_TYPES,
    CustomValueSpec,
    ResultType,
    ScaffoldRequest,
    ScaffoldResult,
    Transport,
    _decode_value,
    _safe_display_name,
    _target_name,
    _url_prefix,
    validate_request,
)

T = TypeVar("T")


def _ask_validated(
    console: Console,
    label: str,
    parse: Callable[[str], T],
    *,
    default: str | None = None,
) -> T:
    while True:
        raw = Prompt.ask(label, default=default, console=console)
        if raw is None:
            console.print("[red]Please enter a value.[/red]")
            continue
        try:
            return parse(raw)
        except (TypeError, ValueError) as exc:
            console.print(f"[red]Please try again:[/red] {escape(str(exc))}")


def _nonblank(value: str) -> str:
    result = value.strip()
    if not result:
        raise ValueError("enter a nonblank value")
    return result


def _collect_spec(
    console: Console,
    *,
    setting: bool,
    existing: frozenset[str],
    reserved: frozenset[str],
) -> CustomValueSpec:
    kind = "setting" if setting else "item field"

    def key_value(raw: str) -> str:
        key = raw.strip()
        if SNAKE_CASE_KEY.fullmatch(key) is None or key in reserved:
            raise ValueError(f"enter a non-reserved snake_case {kind} key")
        if key in existing:
            raise ValueError(f"{kind} {key!r} was already added")
        return key

    key = _ask_validated(
        console,
        f"{kind.title()} key [dim](snake_case, e.g. region)[/dim]",
        key_value,
    )
    value_type = Prompt.ask(
        "Value type",
        choices=list(VALUE_TYPES),
        default="text",
        console=console,
    )
    required = Confirm.ask(
        f"Is [bold]{escape(key)}[/bold] required?",
        default=False,
        console=console,
    )

    def typed_json(raw: str) -> object:
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"enter valid JSON ({exc.msg})") from exc
        return _decode_value(value_type, decoded)

    default: object
    if required:
        default = object()
    else:
        default = _ask_validated(
            console,
            'Default as JSON [dim](examples: 0, false, [], "global")[/dim]',
            typed_json,
        )
    example = _ask_validated(
        console,
        "Example config value as JSON",
        typed_json,
    )
    sensitive = setting and Confirm.ask(
        "Is this value sensitive (for example, an API token)?",
        default=False,
        console=console,
    )
    if required:
        return CustomValueSpec(key, value_type, example, sensitive=sensitive)
    return CustomValueSpec(key, value_type, example, default, sensitive)


def _collect_specs(
    console: Console, *, setting: bool, reserved: frozenset[str]
) -> tuple[CustomValueSpec, ...]:
    kind = "custom setting" if setting else "custom item field"
    specs: list[CustomValueSpec] = []
    while Confirm.ask(f"Add a {kind}?", default=False, console=console):
        specs.append(
            _collect_spec(
                console,
                setting=setting,
                existing=frozenset(spec.key for spec in specs),
                reserved=reserved,
            )
        )
    return tuple(specs)


def _summary(request: ScaffoldRequest) -> Table:
    table = Table(show_header=False, box=None, padding=(0, 1))
    table.add_column("Choice", style="bold cyan")
    table.add_column("Value")
    table.add_row("Target", request.target)
    table.add_row("Display name", request.display_name)
    table.add_row("Domains", ", ".join(request.domains))
    table.add_row("Accepted paths", request.url_prefix + "…")
    table.add_row("Result", "single price" if request.result_type == "price" else "listing offers")
    table.add_row("Default interval", request.default_interval)
    table.add_row(
        "Client", "shared HTTP transport" if request.transport == "http" else "bare client"
    )
    table.add_row(
        "Custom item fields",
        ", ".join(spec.key for spec in request.item_fields) or "none",
    )
    table.add_row(
        "Custom settings",
        ", ".join(spec.key for spec in request.settings) or "none",
    )
    dependencies = list(request.dependencies)
    if request.transport == "http":
        dependencies.insert(0, "tls-client (automatic)")
    table.add_row("Dependencies", ", ".join(dependencies) or "none")
    table.add_row(
        "Starter tests", "generated" if request.include_tests else "skipped (warning only)"
    )
    return table


def collect_request(console: Console | None = None) -> ScaffoldRequest | None:
    """Collect and confirm one complete scaffold request."""
    console = console or Console()
    console.print()
    console.print(
        Panel.fit(
            "This wizard creates only a new plugin source package and, optionally, a "
            "matching test package. It does not edit runtime framework code.\n\n"
            "[dim]Tip: examples shown in prompts describe the stored configuration value, "
            "not Python source.[/dim]",
            title="[bold]New scraper plugin[/bold]",
            border_style="cyan",
        )
    )

    target = _ask_validated(
        console,
        "Target name [dim](snake_case, e.g. acme_store)[/dim]",
        _target_name,
    )
    display_name = _ask_validated(
        console,
        "Store display name [dim](e.g. Acme Store)[/dim]",
        _safe_display_name,
    )
    domains = [
        _ask_validated(
            console,
            "Supported domain [dim](hostname only, e.g. store.example)[/dim]",
            normalize_domain,
        )
    ]
    while Confirm.ask("Add another domain?", default=False, console=console):
        domains.append(_ask_validated(console, "Additional domain", normalize_domain))
    url_prefix = _ask_validated(
        console,
        "Accepted URL path prefix [dim](e.g. /products/)[/dim]",
        _url_prefix,
    )

    console.print(
        "\n[bold]Result shape[/bold]\n"
        "  [cyan]price[/cyan] tracks one resource price. "
        "[cyan]listing[/cyan] returns independently alertable offers from a search page."
    )
    result_type = Prompt.ask(
        "Result type", choices=["price", "listing"], default="price", console=console
    )
    default_interval = Prompt.ask(
        "Canonical default interval",
        choices=list(SUPPORTED_INTERVALS),
        default="1h",
        console=console,
    )

    console.print(
        "\n[bold]Configuration fields[/bold]\n"
        "Every item already has [cyan]id[/cyan], [cyan]name[/cyan], "
        "[cyan]target_price[/cyan], optional [cyan]skip[/cyan], and this scaffold's "
        "[cyan]url[/cyan]. Add item fields only for per-item inputs such as title filters."
    )
    item_fields = _collect_specs(
        console,
        setting=False,
        reserved=FRAMEWORK_ITEM_KEYS | frozenset({"url"}),
    )
    console.print(
        "\n[bold]Settings[/bold]\n"
        "The framework already includes execution interval, log retention, scraping-error "
        "notifications, and repeated-alert suppression. Add settings only for plugin-wide "
        "values such as a minimum listing price or API token."
    )
    settings = _collect_specs(
        console,
        setting=True,
        reserved=frozenset(spec.key for spec in framework_setting_specs(default_interval)),
    )

    console.print(
        "\n[bold]Client transport[/bold]\n"
        "The shared HTTP client provides bounded GET requests, standard status mapping, "
        "retry identity rotation, and clean shutdown. Choose bare only when the plugin uses "
        "a different transport or SDK."
    )
    transport = Prompt.ask(
        "Client transport", choices=["http", "bare"], default="http", console=console
    )
    dependencies: list[str] = []
    while Confirm.ask("Add an extra private dependency?", default=False, console=console):
        dependencies.append(
            _ask_validated(
                console,
                "Requirement [dim](e.g. beautifulsoup4)[/dim]",
                _nonblank,
            )
        )

    console.print(
        "\n[bold]Tests[/bold]\n"
        "Generated tests demonstrate configuration decoding and deliberately fail until "
        "you replace the behavior placeholder. Tests may be skipped, but plugin-check will "
        "show a warning because scraper behavior is then unverified."
    )
    include_tests = Confirm.ask("Generate starter tests?", default=True, console=console)

    try:
        request = validate_request(
            ScaffoldRequest(
                target=target,
                display_name=display_name,
                domains=tuple(domains),
                url_prefix=url_prefix,
                result_type=cast(ResultType, result_type),
                default_interval=default_interval,
                transport=cast(Transport, transport),
                item_fields=item_fields,
                settings=settings,
                dependencies=tuple(dependencies),
                include_tests=include_tests,
            )
        )
    except ValueError as exc:
        console.print(f"\n[red]The collected scaffold is invalid:[/red] {escape(str(exc))}")
        console.print("No files were created. Run the wizard again after correcting the input.")
        return None

    console.print()
    console.print(
        Panel(
            _summary(request),
            title="[bold]Review scaffold[/bold]",
            border_style="cyan",
        )
    )
    if not Confirm.ask("Create this scaffold?", default=True, console=console):
        console.print("[yellow]Cancelled.[/yellow] No files were created.")
        return None
    return request


def render_completion(
    request: ScaffoldRequest,
    result: ScaffoldResult,
    console: Console | None = None,
) -> None:
    console = console or Console()
    lines = [f"[green]Created[/green] {escape(str(result.source))}"]
    if result.tests is not None:
        lines.append(f"[green]Created[/green] {escape(str(result.tests))}")
    else:
        lines.append(
            "[yellow]Tests skipped:[/yellow] plugin-check will allow the plugin with a warning."
        )
    lines.extend(
        [
            "",
            f"1. [cyan]./scripts/dev/setup.sh --{escape(request.target)}[/cyan]",
            f"2. [cyan]./scripts/dev/plugin-check.sh --{escape(request.target)}[/cyan]",
            "3. [cyan]./scripts/dev/check.sh --debug[/cyan]",
        ]
    )
    console.print()
    console.print(
        Panel(
            "\n".join(lines),
            title="[bold]Scaffold created[/bold]",
            border_style="green" if result.tests is not None else "yellow",
        )
    )


__all__ = ["collect_request", "render_completion"]
