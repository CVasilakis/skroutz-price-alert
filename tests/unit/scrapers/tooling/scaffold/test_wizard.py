import io
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest import mock

import pytest
from rich.console import Console

from core.scrapers.tooling.scaffold.contracts import ScaffoldRequest, ScaffoldResult
from core.scrapers.tooling.scaffold.terminal import read_terminal_key as _read_terminal_key
from core.scrapers.tooling.scaffold.wizard import (
    _ABORT,
    _ACCEPT,
    _BACK,
    _BACKSPACE,
    _json_parser,
    _questions,
    collect_request,
    render_completion,
)

REPO_ROOT = Path(__file__).resolve().parents[5]


class _SilentLive:
    def __init__(self, *_args, **_kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def update(self, *_args, **_kwargs):
        pass


def _key_reader(*entries: str) -> tuple[Callable[[], str], list[str]]:
    keys: list[str] = []
    for entry in entries:
        keys.extend(entry)
        keys.append(_ACCEPT)
    iterator: Iterator[str] = iter(keys)
    return iterator.__next__, keys


def _common_answers(*, target: str = "acme_store") -> tuple[str, ...]:
    return (
        target,
        "Acme Store",
        "store.example",
        "/products/",
        "",  # default price result
        "",  # default 1h interval
        "",  # no custom item fields
        "",  # no custom settings
        "",  # default bare client
        "",  # no extra dependencies
        "",  # generate tests
        "",  # confirm review
    )


def test_wizard_guides_reviews_and_confirms_a_common_bare_plugin():
    stream = io.StringIO()
    console = Console(file=stream, width=100, color_system=None, force_terminal=True)
    read_key, _ = _key_reader(*_common_answers())

    request = collect_request(REPO_ROOT, console, read_key=read_key)

    assert request == ScaffoldRequest(
        "acme_store",
        "Acme Store",
        ("store.example",),
        "/products/",
        transport="bare",
    )
    output = stream.getvalue()
    assert "Scrooge-Alert Plugin Wizard" in output
    assert output.startswith("\n╭")
    assert "asks for JSON" not in output
    assert "Target name" in output
    assert "src/core/scrapers/plugins/<target>/" in output
    assert "config/<target>.json" in output
    assert "Store display name" in output
    assert "notifications" in output
    assert "Expected result" in output
    assert "Representative example" in output
    assert "Your answer" in output
    assert "acme_store" in output
    assert "Enter/↓ accept" in output
    assert "Review scaffold" in output


def test_question_guidance_uses_real_plugins_and_configuration_paths():
    base = {question.key: question for question in _questions({}, REPO_ROOT)}

    assert (
        base["domains"].example
        == "Skroutz supports skroutz.gr, skroutz.cy, skroutz.ro, skroutz.bg, skroutz.de. "
        "Insomnia uses the single domain insomnia.gr."
    )
    assert "https://skroutz.gr/s/<sample>" in base["url_prefix"].example
    assert "https://insomnia.gr/classifieds/<sample>" in base["url_prefix"].example
    assert "accepts it only when the part after the domain begins" in base["url_prefix"].expected
    assert "Skroutz uses price" in base["result_type"].example
    assert "Insomnia uses listing" in base["result_type"].example
    assert "config/<target>.json has a url value" in base["domains"].expected
    assert "config.example.json" in base["domains"].expected
    assert "wizard checks every entry" in base["domains"].guidance
    assert "wizard checks these rules" in base["url_prefix"].guidance
    assert "enter / explicitly" in base["url_prefix"].guidance
    assert "describes what the plugin returns" in base["result_type"].guidance
    assert "min_advert_price" in base["setting.0.add"].example
    assert base["transport"].default == "bare"
    assert "means how the generated scraper client will retrieve" in base["transport"].guidance
    assert "repository-root requirements.txt" in base["dependencies"].guidance
    assert "project checks that the plugin declares" in base["dependencies"].expected
    assert base["include_tests"].title == "Generate example tests?"
    assert "non-blocking warning" in base["include_tests"].guidance

    item_questions = {
        question.key: question
        for question in _questions(
            {
                "field.0.add": True,
                "field.0.type": "text-list",
                "field.0.required": False,
            },
            REPO_ROOT,
        )
    }
    assert "Insomnia adds title_include and title_exclude" in item_questions["field.0.add"].example
    assert "config.example.json item row" in item_questions["field.0.key"].expected
    assert (
        "exactly as it should appear in config.example.json"
        in item_questions["field.0.default"].guidance
    )


def test_wizard_navigation_does_not_accumulate_blank_rows_between_panels():
    stream = io.StringIO()
    console = Console(file=stream, width=100, color_system=None)
    keys = iter([*"acme", _ACCEPT, *"Acme", _BACK, _ACCEPT, _BACK, _ABORT])

    with (
        mock.patch("core.scrapers.tooling.scaffold.wizard.Live", _SilentLive),
        mock.patch.object(console, "print", wraps=console.print) as print_spy,
    ):
        assert collect_request(REPO_ROOT, console, read_key=keys.__next__) is None

    blank_writes = [call for call in print_spy.call_args_list if not call.args]
    assert len(blank_writes) == 3  # before welcome, after welcome, after cancellation


def test_wizard_up_revisits_and_preserves_previous_answers():
    stream = io.StringIO()
    console = Console(file=stream, width=100, color_system=None, force_terminal=True)
    keys = [*"wrong", _ACCEPT, *"Wrong Store", _BACK]
    keys.extend(["backspace"] * len("wrong"))
    keys.extend([*"acme", _ACCEPT, *"Acme", _ACCEPT])
    for entry in _common_answers(target="")[2:]:
        keys.extend(entry)
        keys.append(_ACCEPT)

    request = collect_request(REPO_ROOT, console, read_key=iter(keys).__next__)

    assert request is not None
    assert request.target == "acme"
    assert request.display_name == "Acme"


def test_wizard_explains_and_collects_custom_fields_settings_and_dependencies():
    stream = io.StringIO()
    console = Console(file=stream, width=100, color_system=None, force_terminal=True)
    read_key, _ = _key_reader(
        "market_watch",
        "Market Watch",
        "market.example, www.market.example",
        "/search/",
        "listing",
        "2h",
        "yes",
        "title_terms",
        "text-list",
        "no",
        "[]",
        '["Pixel"]',
        "no",
        "yes",
        "api_token",
        "text",
        "yes",
        '"replace-me"',
        "yes",
        "no",
        "bare",
        "beautifulsoup4",
        "no",
        "",
    )

    request = collect_request(REPO_ROOT, console, read_key=read_key)

    assert request is not None
    assert request.result_type == "listing"
    assert request.domains == ("market.example", "www.market.example")
    assert request.item_fields[0].key == "title_terms"
    assert request.item_fields[0].default == ()
    assert request.settings[0].key == "api_token"
    assert request.settings[0].required
    assert request.settings[0].sensitive
    assert request.dependencies == ("beautifulsoup4",)
    assert not request.include_tests
    output = stream.getvalue()
    assert "execution_interval" in output
    assert "strict JSON decoding" in output
    assert "sensitive=True" in output
    assert "declares every extra package" in output


def test_wizard_explains_uppercase_target_error_and_accepts_the_correction():
    stream = io.StringIO()
    console = Console(file=stream, width=100, color_system=None, force_terminal=True)
    keys = [*"HAHA", _ACCEPT]
    keys.extend([_BACKSPACE] * 4)
    keys.extend([*"haha", _ACCEPT])
    for entry in _common_answers(target="")[1:]:
        keys.extend(entry)
        keys.append(_ACCEPT)

    request = collect_request(REPO_ROOT, console, read_key=iter(keys).__next__)

    assert request is not None
    assert request.target == "haha"
    assert "must use lowercase letters; try 'haha' instead of 'HAHA'" in stream.getvalue()


@pytest.mark.parametrize("target", ["status", "insomnia"])
def test_wizard_rejects_command_and_existing_plugin_target_names(target):
    target_question = _questions({}, REPO_ROOT)[0]

    with pytest.raises(ValueError):
        target_question.parser(target)


def test_wizard_rejects_a_boolean_example_string_and_accepts_valid_json_boolean():
    stream = io.StringIO()
    console = Console(file=stream, width=100, color_system=None, force_terminal=True)
    keys = []
    for entry in (
        "acme",
        "Acme",
        "store.example",
        "/items/",
        "",
        "",
        "yes",
        "enabled",
        "boolean",
        "yes",
    ):
        keys.extend(entry)
        keys.append(_ACCEPT)
    keys.extend([*'"haha"', _ACCEPT])
    keys.extend([_BACKSPACE] * len('"haha"'))
    keys.extend([*"true", _ACCEPT])
    for entry in ("no", "no", "", "", "", ""):
        keys.extend(entry)
        keys.append(_ACCEPT)

    request = collect_request(REPO_ROOT, console, read_key=iter(keys).__next__)

    assert request is not None
    assert request.item_fields[0].example is True
    assert "Please try again: must be a boolean" in stream.getvalue()


def test_wizard_escape_aborts_from_the_first_question():
    stream = io.StringIO()
    console = Console(file=stream, width=76, color_system=None, force_terminal=True)

    assert collect_request(REPO_ROOT, console, read_key=iter([_ABORT]).__next__) is None

    output = stream.getvalue()
    assert "Scaffold cancelled" in output
    assert "No plugin was created" in output


def test_wizard_escape_aborts_from_a_later_question():
    stream = io.StringIO()
    console = Console(file=stream, width=76, color_system=None, force_terminal=True)
    keys = iter([*"acme", _ACCEPT, _ABORT])

    assert collect_request(REPO_ROOT, console, read_key=keys.__next__) is None

    output = stream.getvalue()
    assert "Store display name" in output
    assert "No plugin was created" in output


def test_wizard_first_question_ignores_irrelevant_special_keys():
    stream = io.StringIO()
    console = Console(file=stream, width=76, color_system=None, force_terminal=True)
    keys = iter(["", "left", "right", "home", "end", _ABORT])

    assert collect_request(REPO_ROOT, console, read_key=keys.__next__) is None

    output = stream.getvalue()
    assert "Target name" in output
    assert "Scaffold cancelled" in output


@pytest.mark.parametrize(
    ("value_type", "raw"),
    [
        ("text", "'value'"),
        ("boolean", "True"),
        ("text", "None"),
        ("text-list", '["value",]'),
        ("number", "NaN"),
        ("number", "Infinity"),
        ("number", "-Infinity"),
    ],
)
def test_wizard_rejects_values_outside_strict_json(value_type, raw):
    with pytest.raises(ValueError, match="enter valid JSON"):
        _json_parser(value_type)(raw)


def test_terminal_reader_maps_arrow_and_standalone_escape():
    with (
        mock.patch(
            "core.scrapers.tooling.scaffold.terminal.os.read",
            side_effect=(b"\x1b", b"[", b"A"),
        ),
        mock.patch(
            "core.scrapers.tooling.scaffold.terminal.select.select",
            return_value=([object()], [], []),
        ),
    ):
        assert _read_terminal_key(7) == _BACK

    with (
        mock.patch(
            "core.scrapers.tooling.scaffold.terminal.os.read",
            side_effect=(b"\x1b", b"[", b"B"),
        ),
        mock.patch(
            "core.scrapers.tooling.scaffold.terminal.select.select",
            return_value=([object()], [], []),
        ),
    ):
        assert _read_terminal_key(7) == _ACCEPT

    with (
        mock.patch(
            "core.scrapers.tooling.scaffold.terminal.os.read",
            side_effect=(b"\x1b", b"O", b"P"),
        ),
        mock.patch(
            "core.scrapers.tooling.scaffold.terminal.select.select",
            return_value=([object()], [], []),
        ),
    ):
        assert _read_terminal_key(7) == ""

    with (
        mock.patch("core.scrapers.tooling.scaffold.terminal.os.read", return_value=b"\x1b"),
        mock.patch(
            "core.scrapers.tooling.scaffold.terminal.select.select", return_value=([], [], [])
        ),
    ):
        assert _read_terminal_key(7) == _ABORT


def test_terminal_reader_preserves_utf8_text_input():
    encoded = "Σ".encode()
    with (
        mock.patch(
            "core.scrapers.tooling.scaffold.terminal.os.read",
            side_effect=tuple(bytes((byte,)) for byte in encoded),
        ),
        mock.patch(
            "core.scrapers.tooling.scaffold.terminal.select.select",
            return_value=([object()], [], []),
        ),
    ):
        assert _read_terminal_key(7) == "Σ"


def test_completion_panel_warns_when_tests_were_skipped(tmp_path):
    stream = io.StringIO()
    console = Console(file=stream, width=76, color_system=None)
    request = ScaffoldRequest("acme", "Acme", ("store.example",), "/items/", include_tests=False)

    render_completion(
        request,
        ScaffoldResult(tmp_path / "src/acme", None),
        console,
    )

    output = stream.getvalue()
    assert "Scaffold created" in output
    assert "Tests skipped" in output
    assert "plugin-check.sh --acme" in output
    assert output.startswith("\n╭")
    assert output.endswith("\n\n")
