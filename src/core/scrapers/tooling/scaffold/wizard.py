"""Rich presentation and keyboard-driven prompt flow for plugin scaffolding."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from core.scrapers.domain import normalize_domain
from core.scrapers.framework.intervals import SUPPORTED_INTERVALS
from core.scrapers.framework.naming import FRAMEWORK_ITEM_KEYS, SNAKE_CASE_KEY
from core.scrapers.framework.setting_specs import framework_setting_specs
from core.scrapers.tooling.scaffold.contracts import (
    VALUE_TYPES,
    CustomValueSpec,
    ResultType,
    ScaffoldRequest,
    ScaffoldResult,
    Transport,
    decode_value,
    parse_strict_json,
    safe_display_name,
    target_name,
    url_prefix,
    validate_request,
)
from core.scrapers.tooling.scaffold.storage import scaffold_collisions
from core.scrapers.tooling.scaffold.terminal import (
    ABORT as _ABORT,
)
from core.scrapers.tooling.scaffold.terminal import (
    ACCEPT as _ACCEPT,
)
from core.scrapers.tooling.scaffold.terminal import (
    BACK as _BACK,
)
from core.scrapers.tooling.scaffold.terminal import (
    BACKSPACE as _BACKSPACE,
)
from core.scrapers.tooling.scaffold.terminal import (
    DELETE as _DELETE,
)
from core.scrapers.tooling.scaffold.terminal import (
    END as _END,
)
from core.scrapers.tooling.scaffold.terminal import (
    HOME as _HOME,
)
from core.scrapers.tooling.scaffold.terminal import (
    LEFT as _LEFT,
)
from core.scrapers.tooling.scaffold.terminal import (
    REFRESH as _REFRESH,
)
from core.scrapers.tooling.scaffold.terminal import (
    RIGHT as _RIGHT,
)
from core.scrapers.tooling.scaffold.terminal import (
    InteractiveTerminalUnavailable,
    KeyReader,
    UnsupportedTerminalError,
)
from core.scrapers.tooling.scaffold.terminal import (
    terminal_reader as _terminal_reader,
)

Answer = object
Answers = dict[str, Answer]
Parser = Callable[[str], Answer]

_CODE_STYLE = "cyan"


@dataclass(frozen=True, kw_only=True)
class _Question:
    key: str
    title: str
    guidance: str
    expected: str
    example: str
    parser: Parser
    default: str | None = None
    choices: tuple[str, ...] = ()


def _inline_code_text(value: str, *, style: str = "") -> Text:
    """Render paired backtick spans as code-like text without parsing Rich markup."""
    body = Text(style=style)
    if value.count("`") % 2:
        body.append(value)
        return body

    for index, part in enumerate(value.split("`")):
        body.append(part, style=_CODE_STYLE if index % 2 else style)
    return body


def _section(title: str, value: str) -> Text:
    body = Text()
    body.append(f"\n{title}\n", style="bold cyan")
    body.append_text(_inline_code_text(value))
    return body


def _answer_line(question: _Question, value: str, cursor: int) -> Text:
    body = Text()
    body.append("\nYour answer\n", style="bold cyan")
    body.append("> ", style="bold")
    if not value and question.default is not None:
        body.append(question.default or "empty", style="dim")
        body.append(" ")
        body.append(" ", style="reverse")
        body.append(" (default)", style="dim")
        return body

    body.append(value[:cursor])
    current = value[cursor : cursor + 1]
    body.append(current or " ", style="reverse")
    body.append(value[cursor + 1 :])
    return body


def _nonblank(value: str) -> str:
    result = value.strip()
    if not result:
        raise ValueError("enter a nonblank value")
    return result


def _choice(*choices: str) -> Parser:
    allowed = tuple(choices)

    def parse(raw: str) -> str:
        value = raw.strip().lower()
        if value not in allowed:
            raise ValueError(f"choose one of: {', '.join(allowed)}")
        return value

    return parse


def _yes_no(raw: str) -> bool:
    value = raw.strip().lower()
    if value in {"y", "yes"}:
        return True
    if value in {"n", "no"}:
        return False
    raise ValueError("enter yes or no")


def _domains(raw: str) -> tuple[str, ...]:
    values = [value.strip() for value in raw.split(",") if value.strip()]
    if not values:
        raise ValueError("enter at least one hostname or IP address")
    result: list[str] = []
    for value in values:
        domain = normalize_domain(value)
        if domain not in result:
            result.append(domain)
    return tuple(result)


def _wizard_target_parser(repo_root: Path) -> Parser:
    def parse(raw: str) -> str:
        target = target_name(raw)
        if scaffold_collisions(repo_root, target):
            raise ValueError(
                f"target name {target!r} is already used by a checked-in plugin; "
                "choose another name"
            )
        return target

    return parse


def _dependencies(raw: str) -> tuple[str, ...]:
    values = tuple(value.strip() for value in raw.split(",") if value.strip())
    for value in values:
        _nonblank(value)
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("requirements must not contain control characters")
    return tuple(dict.fromkeys(values))


def _json_parser(value_type: str) -> Parser:
    def parse(raw: str) -> object:
        try:
            decoded = parse_strict_json(raw)
        except ValueError as exc:
            raise ValueError(f"enter valid JSON ({exc})") from exc
        return decode_value(value_type, decoded)

    return parse


def _spec_key_parser(*, kind: str, reserved: frozenset[str], existing: frozenset[str]) -> Parser:
    def parse(raw: str) -> str:
        key = raw.strip()
        if SNAKE_CASE_KEY.fullmatch(key) is None or key in reserved:
            raise ValueError(f"enter a non-reserved snake_case {kind} key")
        if key in existing:
            raise ValueError(f"{kind} {key!r} was already added")
        return key

    return parse


def _answer_text(value: Answer, *, json_value: bool = False) -> str:
    if json_value:
        return json.dumps(value)
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, tuple):
        if all(isinstance(item, str) for item in value):
            return ", ".join(cast(tuple[str, ...], value))
        return json.dumps(value)
    if isinstance(value, (list, dict, int, float)) or value is None:
        return json.dumps(value)
    return str(value)


def _field_questions(
    answers: Mapping[str, Answer],
    *,
    setting: bool,
    reserved: frozenset[str],
) -> list[_Question]:
    noun = "setting" if setting else "item field"
    prefix = "setting" if setting else "field"
    questions: list[_Question] = []
    index = 0
    while True:
        add_key = f"{prefix}.{index}.add"
        prior_keys = frozenset(
            cast(str, answers[f"{prefix}.{prior}.key"])
            for prior in range(index)
            if f"{prefix}.{prior}.key" in answers
        )
        if setting:
            guidance = (
                "Add a plugin-wide custom setting only when the standard settings are insufficient. "
                "Every plugin already receives `execution_interval`, `log_retention_days`, "
                "`notify_scraping_errors`, and `suppress_repeated_price_alerts`. Custom settings "
                "belong in the `settings` object and are shared by every tracked item of the plugin."
            )
            expected = (
                "Choosing `yes` adds a typed declaration and example settings entry. Choosing "
                "`no` continues to client transport without adding framework behavior."
            )
            example = (
                "Insomnia uses `min_advert_price` to suppress offers below a user-defined threshold "
                "because they may be too good to be true; choose `no` if the standard four suffice."
            )
        else:
            guidance = (
                "Add a per-item custom field only when `URL`, `id`, `name`, `target_price`, and "
                "optional `skip` "
                "do not fully describe one tracked item. Each custom field is decoded from every "
                "item row and is available to `Client.scrape` through its generated declaration."
            )
            expected = (
                "Choosing `yes` adds a typed field to `plugin.py` and `config.example.json`. "
                "Choosing `no` continues without adding configuration that contributors must maintain."
            )
            example = (
                "Insomnia adds `title_include` and `title_exclude` lists because each tracked "
                "classifieds search needs its own title filters. Skroutz needs no custom item "
                "fields because its product URL is sufficient."
            )
        questions.append(
            _Question(
                key=add_key,
                title=f"Add custom {noun} {index + 1}?",
                guidance=guidance,
                expected=expected,
                example=example,
                parser=_yes_no,
                default="no",
                choices=("yes", "no"),
            )
        )
        if answers.get(add_key) is not True:
            break

        key_key = f"{prefix}.{index}.key"
        questions.append(
            _Question(
                key=key_key,
                title=f"Custom {noun} key",
                guidance="This is the stable machine-readable JSON key. Use lowercase letters, digits, "
                "and underscores; begin with a letter. Framework-owned names and duplicate keys "
                "are rejected. Renaming it later is a configuration migration.",
                expected=f"The scaffold declares `{('SETTING' if setting else 'ITEM')}_<KEY>` in "
                f"`plugin.py` and writes `<key>` into the `config.example.json` "
                f"{'settings object' if setting else 'item row'}.",
                example=(
                    "Insomnia uses `min_advert_price` for its minimum plausible advert price"
                    if setting
                    else "Insomnia uses `title_include` and `title_exclude`"
                ),
                parser=_spec_key_parser(kind=noun, reserved=reserved, existing=prior_keys),
            )
        )
        type_key = f"{prefix}.{index}.type"
        questions.append(
            _Question(
                key=type_key,
                title=f"Custom {noun} value type",
                guidance="The type controls strict JSON decoding: `text` is a nonblank string; "
                "`integer` rejects booleans; `number` must be finite; `nonnegative-number` also "
                "rejects values below zero; `boolean` accepts `true`/`false`; `text-list` is an "
                "array of nonblank strings.",
                expected="The generated descriptor receives the matching decoder and both the default and "
                "example values must satisfy it.",
                example=(
                    "Insomnia uses `nonnegative-number` for `min_advert_price` because a negative "
                    "price floor is invalid"
                    if setting
                    else 'Insomnia title filters use `text-list`, such as `["laptop", "ThinkPad"]`'
                ),
                parser=_choice(*VALUE_TYPES),
                default="text",
                choices=tuple(VALUE_TYPES),
            )
        )
        required_key = f"{prefix}.{index}.required"
        questions.append(
            _Question(
                key=required_key,
                title=f"Require this {noun}?",
                guidance="A required value must appear in every applicable configuration object. An "
                "optional value receives the default you choose next when it is omitted.",
                expected="Required values make missing configuration invalid. Optional values keep older "
                "or shorter item rows usable with an explicit generated default.",
                example=(
                    "Insomnia does not require `min_advert_price` because omitting it safely uses "
                    "the disabled default"
                    if setting
                    else "Insomnia makes `title_include` and `title_exclude` optional so each can "
                    "default to `[]`."
                ),
                parser=_yes_no,
                default="no",
                choices=("yes", "no"),
            )
        )
        value_type = cast(str, answers.get(type_key, "text"))
        if answers.get(required_key) is False:
            questions.append(
                _Question(
                    key=f"{prefix}.{index}.default",
                    title=f"Default {noun} value",
                    guidance="Type the value exactly as it should appear in `config.example.json`, not "
                    "as Python source. Strings need double quotes, booleans are `true` or `false`, "
                    "and lists use JSON brackets.",
                    expected="The generated declaration uses this value whenever the key is omitted from "
                    "a configuration object.",
                    example=(
                        "Insomnia uses `0` so omitting `min_advert_price` disables the filter"
                        if setting
                        else "Insomnia uses `[]` for both optional title-filter lists"
                    ),
                    parser=_json_parser(value_type),
                )
            )
        questions.append(
            _Question(
                key=f"{prefix}.{index}.example",
                title=f"Example {noun} value",
                guidance="Type a realistic valid JSON value exactly as it should appear in "
                "`config.example.json`. It may equal the default, but a representative value is "
                "usually more helpful.",
                expected=f"The value is written into `config.example.json` under the "
                f"{'settings object' if setting else 'sample item row'}.",
                example=(
                    "Insomnia uses `30` to demonstrate a realistic `min_advert_price` threshold"
                    if setting
                    else 'Insomnia could show `["laptop", "ThinkPad"]` for `title_include`'
                ),
                parser=_json_parser(value_type),
            )
        )
        if setting:
            questions.append(
                _Question(
                    key=f"{prefix}.{index}.sensitive",
                    title="Sensitive setting?",
                    guidance="Mark secrets such as API tokens sensitive. This records the setting's "
                    "presentation policy so tooling can avoid exposing its value. Do not mark "
                    "ordinary filters or numeric thresholds sensitive.",
                    expected="Choosing `yes` emits `sensitive=True` on the `SettingSpec` declaration; the "
                    "example still contains only the non-secret placeholder you provided.",
                    example="Insomnia's `min_advert_price` is not sensitive because an ordinary numeric "
                    "filter is not secret. An `api_token` should be sensitive because credentials must "
                    "not be exposed.",
                    parser=_yes_no,
                    default="no",
                    choices=("yes", "no"),
                )
            )
        index += 1
    return questions


def _questions(answers: Mapping[str, Answer], repo_root: Path) -> list[_Question]:
    target = cast(str, answers.get("target", "<target>"))
    default_interval = cast(str, answers.get("default_interval", "1h"))
    questions = [
        _Question(
            key="target",
            title="Target name",
            guidance="The target name is the plugin's unique identifier. It may be one word, such as "
            "`skroutz` or `insomnia`, or multiple snake_case words, such as `acme_store`. Use only "
            "lowercase letters, digits, and underscores, begin with a letter, and avoid reserved "
            "framework names and Scrooge Alert command names such as `status`. Do not reuse the name "
            "of another checked-in plugin, such as `insomnia`. Treat it as permanent once published.",
            expected="It names `src/core/scrapers/plugins/<target>/`, `tests/plugins/<target>/`, "
            "`config/<target>.json`, `state/<target>.json`, `--<target>` command flags, logs, locks, "
            "and systemd units.",
            example="`skroutz`, `insomnia`, or `acme_store`",
            parser=_wizard_target_parser(repo_root),
        ),
        _Question(
            key="display_name",
            title="Store display name",
            guidance="This is the human-readable store or service name. Capitalization and spaces are "
            "welcome. Keep it short and recognizable; it is presentation text, not a Python or "
            "filesystem identifier.",
            expected="It becomes `ScraperPlugin.display_name` and appears in user-facing status, scraping "
            "output, errors, and notifications. It does not affect paths or command flags.",
            example="Skroutz, Insomnia, or Acme Store",
            parser=safe_display_name,
        ),
        _Question(
            key="domains",
            title="Supported domains",
            guidance="List every hostname or IP address this plugin accepts, separated by commas. Enter "
            "hostnames only: no scheme, port, path, query, credentials, or wildcard. Subdomains "
            "must be listed separately when users can paste them into a working tracked URL "
            "(for example, enter both `example.com` and `shop.example.com` if product pages work on "
            "both hosts). The wizard checks every entry and will keep this panel open if it is not "
            "a valid host-only value.",
            expected="Each tracked item in `config/<target>.json` has a `url` value for the page to check. "
            "Scrooge Alert compares that URL's hostname with this list so it can reject unsupported "
            "URLs and route supported ones to this plugin. The scaffold also builds the example "
            "tracked URL in `config.example.json` from the first hostname you enter here and the "
            "path prefix requested next.",
            example="Skroutz supports `skroutz.gr`, `skroutz.cy`, `skroutz.ro`, `skroutz.bg`, "
            "`skroutz.de`.\nInsomnia uses the single domain `insomnia.gr`.",
            parser=_domains,
        ),
        _Question(
            key="url_prefix",
            title="Accepted URL path prefix",
            guidance="Enter the path portion that comes immediately after the domain and identifies the "
            "kind of page this plugin accepts. It must start with `/` and contain no whitespace, "
            "query, or fragment; the wizard checks these rules before continuing. The scaffold "
            "adds a trailing slash when omitted. An answer is required: enter `/` explicitly only "
            "when the plugin should accept nearly every path on the domain.",
            expected="The scaffold combines the first domain and prefix to create its example URL, then "
            "uses `<sample>` to represent the page-specific part that follows it. When a user adds "
            "a URL to the configuration, the plugin accepts it only when the part after the "
            "domain begins with this prefix. This keeps unrelated pages from the same website "
            "out of the plugin.",
            example="Skroutz uses `/s/`: `https://skroutz.gr/s/<sample>`.\nInsomnia uses "
            "`/classifieds/`: `https://insomnia.gr/classifieds/<sample>`.",
            parser=url_prefix,
        ),
        _Question(
            key="result_type",
            title="Scrape result shape",
            guidance="The result shape describes what the plugin returns after successfully checking one "
            "configured URL. Choose `price` when one page represents one tracked product and "
            "produces one price. Choose `listing` when a search or category page produces multiple "
            "independently alertable offers with their own titles, prices, and canonical URLs.",
            expected="The generated client returns `PriceResult` for `price` or `ListingResult` containing "
            "`Offer` values for `listing`; this determines alert-history behavior and starter code.",
            example="Skroutz uses `price` because one product page yields one price.\nInsomnia uses "
            "`listing` because one classifieds search page yields multiple offers.",
            parser=_choice("price", "listing"),
            default="price",
            choices=("price", "listing"),
        ),
        _Question(
            key="default_interval",
            title="Canonical default interval",
            guidance="Choose the normal background check interval. Users may override it in their target "
            "configuration. Prefer a respectful interval for the remote service; the framework "
            "still applies sequential request pacing within a run.",
            expected="The value becomes `ScraperPlugin.default_interval`, the example "
            "`execution_interval`, "
            "and the default systemd timer schedule when no valid user configuration override exists.",
            example="`1h` for hourly checks; supported values: `"
            + "`, `".join(SUPPORTED_INTERVALS)
            + "`",
            parser=_choice(*SUPPORTED_INTERVALS),
            default="1h",
            choices=tuple(SUPPORTED_INTERVALS),
        ),
    ]
    questions.extend(
        _field_questions(
            answers,
            setting=False,
            reserved=FRAMEWORK_ITEM_KEYS | frozenset({"url"}),
        )
    )
    questions.extend(
        _field_questions(
            answers,
            setting=True,
            reserved=frozenset(spec.key for spec in framework_setting_specs(default_interval)),
        )
    )
    questions.extend(
        [
            _Question(
                key="transport",
                title="Client transport",
                guidance="Client transport means how the generated scraper client will retrieve page or API "
                "data from the site. The shared HTTP transport provides bounded `GET` requests, "
                "standard HTTP status mapping, retry identity rotation, and clean shutdown. The "
                "`bare` client leaves the scraping approach entirely up to you.",
                expected="`http` subclasses `HttpScraperClient` and automatically adds `tls-client`. "
                "`bare` subclasses `ScraperClient` and performs no network request until you implement it.",
                example="Choose `bare` when you have not chosen a scraping approach yet; `http` when you "
                "know the shared HTTP client fits the site you want to scrape.",
                parser=_choice("http", "bare"),
                default="bare",
                choices=("http", "bare"),
            ),
            _Question(
                key="dependencies",
                title="Additional private dependencies",
                guidance="Enter comma-separated Python requirement strings needed only by this plugin, or "
                "leave the answer empty. Project-wide packages are listed in the repository-root "
                "`requirements.txt`; do not repeat them here. If you are unsure, leave this empty "
                f"and later create or edit `src/core/scrapers/plugins/{target}/requirements.txt` "
                "manually.",
                expected=f"Nonempty values are written one per line to "
                f"`src/core/scrapers/plugins/{target}/requirements.txt`. Setup installs them for "
                "this plugin, and the project checks that the plugin declares every extra package "
                "it imports.",
                example="`beautifulsoup4`, `lxml`; leave empty when the standard library and transport suffice",
                parser=_dependencies,
                default="",
            ),
            _Question(
                key="include_tests",
                title="Generate example tests?",
                guidance="The example tests demonstrate strict configuration decoding. They also contain "
                "one deliberately skipped placeholder test marked `TODO`. After implementing the "
                "client, replace that placeholder with tests that use mocked responses to cover "
                "successful scraping and parsing failures. You may omit the entire test package; "
                "the verifier will show a non-blocking warning because plugin behavior is then "
                "untested.",
                expected=f"`yes` creates `tests/plugins/{target}/` with a passing configuration test and a "
                "skipped placeholder for the behavior tests you still need to write. `no` creates "
                "only the source package. Either choice passes the initial scaffold checks.",
                example="`yes` is recommended, even for a small plugin",
                parser=_yes_no,
                default="yes",
                choices=("yes", "no"),
            ),
        ]
    )
    return questions


def _specs(answers: Mapping[str, Answer], *, setting: bool) -> tuple[CustomValueSpec, ...]:
    prefix = "setting" if setting else "field"
    specs: list[CustomValueSpec] = []
    index = 0
    while answers.get(f"{prefix}.{index}.add") is True:
        required = cast(bool, answers[f"{prefix}.{index}.required"])
        key = cast(str, answers[f"{prefix}.{index}.key"])
        value_type = cast(str, answers[f"{prefix}.{index}.type"])
        example = answers[f"{prefix}.{index}.example"]
        sensitive = setting and cast(bool, answers[f"{prefix}.{index}.sensitive"])
        if required:
            specs.append(CustomValueSpec(key, value_type, example, sensitive=sensitive))
        else:
            specs.append(
                CustomValueSpec(
                    key,
                    value_type,
                    example,
                    answers[f"{prefix}.{index}.default"],
                    sensitive,
                )
            )
        index += 1
    return tuple(specs)


def _request(answers: Mapping[str, Answer]) -> ScaffoldRequest:
    return validate_request(
        ScaffoldRequest(
            target=cast(str, answers["target"]),
            display_name=cast(str, answers["display_name"]),
            domains=cast(tuple[str, ...], answers["domains"]),
            url_prefix=cast(str, answers["url_prefix"]),
            result_type=cast(ResultType, answers["result_type"]),
            default_interval=cast(str, answers["default_interval"]),
            transport=cast(Transport, answers["transport"]),
            item_fields=_specs(answers, setting=False),
            settings=_specs(answers, setting=True),
            dependencies=cast(tuple[str, ...], answers["dependencies"]),
            include_tests=cast(bool, answers["include_tests"]),
        )
    )


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
        "Custom item fields", ", ".join(spec.key for spec in request.item_fields) or "none"
    )
    table.add_row("Custom settings", ", ".join(spec.key for spec in request.settings) or "none")
    dependencies = list(request.dependencies)
    if request.transport == "http":
        dependencies.insert(0, "tls-client (automatic)")
    table.add_row("Dependencies", ", ".join(dependencies) or "none")
    table.add_row(
        "Example tests",
        "generated" if request.include_tests else "skipped (non-blocking warning)",
    )
    return table


def _question_content(
    question: _Question,
    value: str,
    cursor: int,
    *,
    error: str | None,
    position: int,
    total: int,
) -> RenderableType:
    sections: list[RenderableType] = [
        _inline_code_text(question.guidance),
        _section("Expected result", question.expected),
        _section("Representative example", question.example),
    ]
    if question.choices:
        choices = Text()
        choices.append("\nChoices\n", style="bold cyan")
        choices.append(", ".join(question.choices))
        sections.append(choices)
    sections.append(_answer_line(question, value, cursor))
    if error:
        error_text = Text("\nPlease try again:", style="bold red")
        error_text.append(f" {error}")
        sections.append(error_text)
    sections.append(
        Text.from_markup(
            f"\n[dim]Step {position} of {total}  •  Enter/↓ accept  •  ↑ previous  •  "
            "Esc abort  •  ←/→ edit[/dim]"
        )
    )
    return Group(*sections)


def _question_panel(
    question: _Question,
    value: str,
    cursor: int,
    *,
    error: str | None,
    position: int,
    total: int,
) -> Panel:
    return Panel(
        _question_content(question, value, cursor, error=error, position=position, total=total),
        title=Text(question.title, style="bold"),
        border_style="cyan",
    )


def _review_panel(request: ScaffoldRequest, *, position: int, total: int) -> Panel:
    return Panel(
        Group(
            Text.from_markup(
                "Review the generated paths and contracts below. Press [bold]Enter[/bold] or "
                "[bold]↓[/bold] to create them, [bold]↑[/bold] to revisit the advanced guidance, or "
                "[bold]Esc[/bold] to abort without creating any plugin."
            ),
            Text(""),
            _summary(request),
            Text.from_markup(
                f"\n[dim]Step {position} of {total}  •  Enter/↓ create  •  ↑ guidance  •  Esc abort[/dim]"
            ),
        ),
        title="[bold]Review scaffold[/bold]",
        border_style="cyan",
    )


def _remaining_work(request: ScaffoldRequest) -> str:
    if request.transport == "http":
        transport = (
            "The generated `HttpScraperClient` already performs a bounded `GET` and standard status "
            "mapping; parse `_response`"
        )
    else:
        transport = (
            "The generated bare `ScraperClient` does not fetch anything; choose and implement the "
            "transport, then parse its response"
        )

    if request.result_type == "price":
        result = "return a validated `PriceResult` with the real price and currency"
    else:
        result = "build one `Offer` per alertable result and return a validated `ListingResult`"

    selected = [
        *(f"item field `{spec.key}`" for spec in request.item_fields),
        *(f"setting `{spec.key}`" for spec in request.settings),
    ]
    selected_work = (
        " Use the generated " + ", ".join(selected) + " where the request or filtering needs them."
        if selected
        else ""
    )
    steps = [
        "Confirm the generated `UrlField`, path predicate, and sample URL match pages the real "
        "client can handle.",
        f"Finish `Client.scrape()`. {transport}, then {result}. Raise the modeled scraper "
        f"exceptions for expected failures.{selected_work}",
    ]
    if request.include_tests:
        steps.append(
            "In the generated test file, replace the deliberately skipped `TODO` behavior test "
            "with mocked success, failure, URL-shape, and cleanup tests."
        )
        verification = (
            "Complete the package `README.md`, then run `./scripts/dev/setup.sh --<target>`, "
            "`./scripts/dev/plugin-check.sh --<target>`, and `./scripts/dev/check.sh --debug`."
        )
    else:
        verification = (
            "Complete the package `README.md`, then run `./scripts/dev/setup.sh --<target>`, "
            "`./scripts/dev/plugin-check.sh --<target>`, and `./scripts/dev/check.sh --debug`. "
            "The missing-tests warning from plugin-check is non-blocking."
        )
    steps.append(verification)
    return "\n".join(f"{index}. {step}" for index, step in enumerate(steps, start=1))


def _advanced_panel(request: ScaffoldRequest, *, position: int, total: int) -> Panel:
    return Panel(
        Group(
            _inline_code_text(
                "The wizard intentionally creates the common one-URL plugin shape. The framework also "
                "supports these advanced designs when a source genuinely needs them:"
            ),
            _section(
                "Advanced designs",
                "• URL-less or multi-URL items — useful when an item is identified by values such as `sku` "
                "and `region` instead of a page, or when scraping uses a product page together with an API "
                "endpoint or regional page.\n"
                "• Configuration migrations — when a published field or setting changes shape; increment "
                "`config_schema_version` and add pure transforms in `migrations.py` so updates convert user "
                "configuration atomically. New version-1 plugins need no migration.\n"
                "• Client hooks — use `prepare_retry()` to rotate sessions, `diagnostic_context()` for "
                "non-secret troubleshooting details, and `close()` to release browsers or sessions.\n"
                "• Setting presentation — add a custom display or warning when raw values need friendlier "
                "words, such as showing numeric `0` as `disabled`; keep secrets `sensitive=True`.",
            ),
            _section("What remains for this scaffold", _remaining_work(request)),
            Text(""),
            _inline_code_text(
                "Refer to `CONTRIBUTING.md` before implementing any advanced path; it contains the full "
                "contracts, examples, safety rules, and required tests."
            ),
            Text.from_markup(
                f"\n[dim]Step {position} of {total}  •  Enter/↓ continue  •  ↑ previous  •  Esc abort[/dim]"
            ),
        ),
        title="[bold]Beyond the scaffold[/bold]",
        border_style="cyan",
    )


def _edit_question(
    console: Console,
    question: _Question,
    existing: Answer | None,
    read_key: KeyReader,
    *,
    position: int,
    total: int,
) -> tuple[str, Answer | None]:
    json_value = question.key.endswith((".default", ".example"))
    value = _answer_text(existing, json_value=json_value) if existing is not None else ""
    cursor = len(value)
    error: str | None = None
    with Live(
        _question_panel(question, value, cursor, error=error, position=position, total=total),
        console=console,
        auto_refresh=False,
        transient=True,
    ) as live:
        while True:
            key = read_key()
            if key == _ABORT:
                return _ABORT, None
            if key == _BACK:
                return _BACK, None
            if key == _ACCEPT:
                raw = value if value else question.default
                if raw is None:
                    error = "enter a value before continuing"
                else:
                    try:
                        return _ACCEPT, question.parser(raw)
                    except (TypeError, ValueError) as exc:
                        error = str(exc)
            elif key == _LEFT:
                cursor = max(0, cursor - 1)
            elif key == _RIGHT:
                cursor = min(len(value), cursor + 1)
            elif key == _HOME:
                cursor = 0
            elif key == _END:
                cursor = len(value)
            elif key == _BACKSPACE and cursor:
                value = value[: cursor - 1] + value[cursor:]
                cursor -= 1
                error = None
            elif key == _DELETE and cursor < len(value):
                value = value[:cursor] + value[cursor + 1 :]
                error = None
            elif key == _REFRESH:
                pass
            elif len(key) == 1 and key.isprintable():
                value = value[:cursor] + key + value[cursor:]
                cursor += 1
                error = None
            live.update(
                _question_panel(
                    question,
                    value,
                    cursor,
                    error=error,
                    position=position,
                    total=total,
                ),
                refresh=True,
            )


def render_cancellation(console: Console | None = None) -> None:
    """Render the shared interactive cancellation result after terminal cleanup."""
    console = console or Console()
    console.print(
        Panel(
            "[yellow]Plugin creation aborted.[/yellow] No plugin was created.",
            title="[bold]Scaffold cancelled[/bold]",
            border_style="yellow",
        )
    )
    console.print()


def _welcome_panel() -> Panel:
    return Panel(
        "This wizard creates only a new plugin source package and, optionally, a matching "
        "test package. It does not edit runtime framework code.\n\n"
        "Press [bold]Enter[/bold] or [bold]↓[/bold] to continue, [bold]↑[/bold] "
        "to revisit an earlier answer, or [bold]Esc[/bold] to abort at any time.",
        title="[bold]Scrooge-Alert Plugin Wizard[/bold]",
        border_style="cyan",
    )


def collect_request(
    repo_root: Path,
    console: Console | None = None,
    *,
    read_key: KeyReader | None = None,
) -> ScaffoldRequest | None:
    """Collect and confirm one complete scaffold request."""
    console = console or Console()
    console.print()
    console.print(_welcome_panel())

    @contextmanager
    def supplied_reader() -> Iterator[KeyReader]:
        assert read_key is not None
        yield read_key

    answers: Answers = {}
    index = 0
    reader_context = supplied_reader() if read_key is not None else _terminal_reader()
    try:
        with reader_context as keys:
            console.print()
            while True:
                questions = _questions(answers, repo_root)
                total = len(questions) + 2
                if index >= len(questions):
                    try:
                        request = _request(answers)
                    except (KeyError, TypeError, ValueError) as exc:
                        raise RuntimeError(f"the collected scaffold is invalid: {exc}") from exc
                    if index == len(questions):
                        with Live(
                            _advanced_panel(request, position=index + 1, total=total),
                            console=console,
                            auto_refresh=False,
                            transient=True,
                        ):
                            key = keys()
                        if key == _ABORT:
                            render_cancellation(console)
                            return None
                        if key == _BACK:
                            index = max(0, index - 1)
                            continue
                        if key == _ACCEPT:
                            index += 1
                        continue
                    with Live(
                        _review_panel(request, position=total, total=total),
                        console=console,
                        auto_refresh=False,
                        transient=True,
                    ):
                        key = keys()
                    if key == _ABORT:
                        render_cancellation(console)
                        return None
                    if key == _BACK:
                        index = max(0, index - 1)
                        continue
                    if key == _ACCEPT:
                        return request
                    continue

                question = questions[index]
                action, answer = _edit_question(
                    console,
                    question,
                    answers.get(question.key),
                    keys,
                    position=index + 1,
                    total=total,
                )
                if action == _ABORT:
                    render_cancellation(console)
                    return None
                if action == _BACK:
                    index = max(0, index - 1)
                    continue
                assert answer is not None
                answers[question.key] = answer
                index += 1
    except InteractiveTerminalUnavailable as exc:
        console.print()
        console.print(
            Panel(
                f"[red]Cannot start the guided wizard:[/red] {escape(str(exc))}",
                title=(
                    "[bold]Unsupported terminal[/bold]"
                    if isinstance(exc, UnsupportedTerminalError)
                    else "[bold]Interactive terminal required[/bold]"
                ),
                border_style="red",
            )
        )
        console.print()
        raise


def render_completion(
    request: ScaffoldRequest,
    result: ScaffoldResult,
    console: Console | None = None,
) -> None:
    """Show what was created and the exact next steps for the new plugin.

    The scaffold is deliberately incomplete — its client raises — so the closing
    panel names the acceptance commands rather than implying the work is done.
    """
    console = console or Console()
    body = Text()
    body.append("Created", style="green")
    body.append(" ")
    body.append(str(result.source), style=_CODE_STYLE)
    if result.tests is not None:
        body.append("\nCreated", style="green")
        body.append(" ")
        body.append(str(result.tests), style=_CODE_STYLE)
    else:
        body.append("\nTests skipped:", style="yellow")
        body.append(" plugin-check will report a non-blocking warning.")
    body.append("\n\n1. ")
    body.append(f"./scripts/dev/setup.sh --{request.target}", style=_CODE_STYLE)
    body.append("\n2. ")
    body.append(f"./scripts/dev/plugin-check.sh --{request.target}", style=_CODE_STYLE)
    body.append("\n3. ")
    body.append("./scripts/dev/check.sh --debug", style=_CODE_STYLE)
    console.print()
    console.print(
        Panel(
            body,
            title="[bold]Scaffold created[/bold]",
            border_style="green" if result.tests is not None else "yellow",
        )
    )
    console.print()


__all__ = ["collect_request", "render_cancellation", "render_completion"]
