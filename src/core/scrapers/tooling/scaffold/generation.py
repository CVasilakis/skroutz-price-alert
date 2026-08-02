"""Pure source rendering for checked-in scraper plugin scaffolds."""

from __future__ import annotations

import json
from dataclasses import dataclass

from core.scrapers.framework.configuration import SCHEMA_VERSION
from core.scrapers.tooling import SCAFFOLD_TEST_TODO
from core.scrapers.tooling.scaffold.contracts import CustomValueSpec, ScaffoldRequest


@dataclass(frozen=True)
class GeneratedFile:
    relative_path: str
    contents: str


@dataclass(frozen=True)
class ScaffoldFiles:
    source: tuple[GeneratedFile, ...]
    tests: tuple[GeneratedFile, ...] | None


_DECODER_NAMES = {
    "text": "decode_text",
    "integer": "decode_integer",
    "number": "decode_number",
    "nonnegative-number": "decode_nonnegative_number",
    "boolean": "decode_boolean",
    "text-list": "decode_text_list",
}
_TYPE_HINTS = {
    "text": "str",
    "integer": "int",
    "number": "float",
    "nonnegative-number": "float",
    "boolean": "bool",
    "text-list": "tuple[str, ...]",
}


def _decoder_source(value_types: set[str]) -> str:
    blocks: list[str] = []
    if "text" in value_types:
        blocks.append("""def decode_text(raw: object) -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("must be a nonblank string")
    return raw.strip()
""")
    if "integer" in value_types:
        blocks.append("""def decode_integer(raw: object) -> int:
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ValueError("must be an integer")
    return raw
""")
    if "number" in value_types:
        blocks.append("""def decode_number(raw: object) -> float:
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise ValueError("must be a number")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError("must be finite")
    return value
""")
    if "nonnegative-number" in value_types:
        blocks.append("""def decode_nonnegative_number(raw: object) -> float:
    value = decode_number(raw)
    if value < 0:
        raise ValueError("must be non-negative")
    return value
""")
    if "boolean" in value_types:
        blocks.append("""def decode_boolean(raw: object) -> bool:
    if not isinstance(raw, bool):
        raise ValueError("must be a boolean")
    return raw
""")
    if "text-list" in value_types:
        blocks.append("""def decode_text_list(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, (list, tuple)):
        raise ValueError("must be an array of nonblank strings")
    if any(not isinstance(value, str) or not value.strip() for value in raw):
        raise ValueError("must contain only nonblank strings")
    return tuple(value.strip() for value in raw)
""")
    return "\n\n".join(blocks)


def _python_literal(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, tuple):
        members = ", ".join(_python_literal(member) for member in value)
        return f"({members}{',' if len(value) == 1 else ''})"
    if isinstance(value, list):
        return "[" + ", ".join(_python_literal(member) for member in value) + "]"
    if isinstance(value, dict):
        members = ", ".join(
            f"{_python_literal(key)}: {_python_literal(member)}" for key, member in value.items()
        )
        return "{" + members + "}"
    return repr(value)


def _declaration_source(spec: CustomValueSpec, *, setting: bool) -> str:
    prefix = "SETTING" if setting else "ITEM"
    constructor = "SettingSpec" if setting else "ItemField"
    lines = [
        f"{prefix}_{spec.key.upper()} = {constructor}[{_TYPE_HINTS[spec.value_type]}](",
        f"    key={_python_literal(spec.key)},",
        f"    decode={_DECODER_NAMES[spec.value_type]},",
    ]
    if not spec.required:
        lines.append(f"    default={_python_literal(spec.default)},")
    if setting and spec.sensitive:
        lines.append("    sensitive=True,")
    lines.append(")")
    return "\n".join(lines)


def _plugin_source(request: ScaffoldRequest) -> str:
    value_types = {spec.value_type for spec in (*request.item_fields, *request.settings)}
    if "nonnegative-number" in value_types:
        value_types.add("number")
    imports = ["ScraperPlugin", "UrlField"]
    if request.item_fields:
        imports.append("ItemField")
    if request.settings:
        imports.append("SettingSpec")
    imports.sort()
    math_import = "import math\n" if value_types & {"number", "nonnegative-number"} else ""
    decoder_source = _decoder_source(value_types)
    declarations = [
        *(_declaration_source(spec, setting=False) for spec in request.item_fields),
        *(_declaration_source(spec, setting=True) for spec in request.settings),
    ]
    item_names = ["URL", *(f"ITEM_{spec.key.upper()}" for spec in request.item_fields)]
    setting_names = [f"SETTING_{spec.key.upper()}" for spec in request.settings]
    import_block = (
        f"{math_import}from urllib.parse import SplitResult\n\n"
        f"from core.scrapers.api import {', '.join(imports)}"
    )
    blocks = [f'''"""Import-light descriptor for {request.display_name}."""\n\n{import_block}''']
    if decoder_source:
        blocks.append(decoder_source.rstrip())
    blocks.extend(
        [
            f"""def accepts_url(url: SplitResult) -> bool:
    return url.path.startswith({_python_literal(request.url_prefix)})
""",
            f"""URL = UrlField(
    key="url",
    domains={_python_literal(request.domains)},
    accepts_url=accepts_url,
)
""",
            *declarations,
            f"""PLUGIN = ScraperPlugin(
    display_name={_python_literal(request.display_name)},
    item_fields=({", ".join(item_names)}{"," if len(item_names) == 1 else ""}),
    settings=({", ".join(setting_names)}{"," if len(setting_names) == 1 else ""}),
    reference_url=URL,
    default_interval={_python_literal(request.default_interval)},
)
""",
        ]
    )
    return "\n\n\n".join(block.rstrip() for block in blocks) + "\n"


def _client_source(request: ScaffoldRequest) -> str:
    result_class = "PriceResult" if request.result_type == "price" else "ListingResult"
    declarations = sorted(
        [
            "URL",
            *(f"ITEM_{spec.key.upper()}" for spec in request.item_fields),
            *(f"SETTING_{spec.key.upper()}" for spec in request.settings),
        ]
    )
    source_url_name = "source_url" if request.transport == "http" else "_source_url"
    access_lines = [f"        {source_url_name} = item[URL]"]
    access_lines.extend(
        f"        _{spec.key} = item[ITEM_{spec.key.upper()}]" for spec in request.item_fields
    )
    access_lines.extend(
        f"        _{spec.key} = self.settings[SETTING_{spec.key.upper()}]"
        for spec in request.settings
    )
    if request.transport == "http":
        api_import = f"from core.scrapers.api import {result_class}, TrackedItem"
        support_import = "from core.scrapers.support.http import HttpScraperClient"
        base_class = "HttpScraperClient"
        access_lines.extend(
            [
                "        response = self.get(source_url, headers=self.current_headers)",
                "        self.raise_for_status(response.status_code)",
                "        _response = response",
            ]
        )
    else:
        api_import = f"from core.scrapers.api import {result_class}, ScraperClient, TrackedItem"
        support_import = ""
        base_class = "ScraperClient"
    action = (
        "parse the response and return PriceResult(price=..., currency=...)"
        if request.result_type == "price"
        else "parse the response and return ListingResult(currency=..., offers=(Offer(...), ...))"
    )
    declaration_imports = ",\n    ".join(declarations) + ","
    import_lines = [
        api_import,
        f"""from core.scrapers.plugins.{request.target}.plugin import (
    {declaration_imports}
)""",
    ]
    if support_import:
        import_lines.append(support_import)
    imports_source = "\n".join(import_lines)
    access_source = "\n".join(access_lines)
    return f'''"""Client implementation for {request.display_name}."""

{imports_source}


class Client({base_class}):
    def scrape(self, item: TrackedItem) -> {result_class}:
{access_source}
        raise NotImplementedError(
            {_python_literal(action)}
        )
'''


def _config_document(request: ScaffoldRequest) -> dict[str, object]:
    settings: dict[str, object] = {
        "execution_interval": request.default_interval,
        "log_retention_days": 7,
        "notify_scraping_errors": True,
        "suppress_repeated_price_alerts": False,
    }
    settings.update({spec.key: spec.example for spec in request.settings})
    item: dict[str, object] = {
        "id": "sample-item",
        "name": "Sample item",
        "url": f"https://{request.domains[0]}{request.url_prefix}sample",
        "target_price": 100.0,
        "skip": False,
    }
    item.update({spec.key: spec.example for spec in request.item_fields})
    return {
        "schema_version": SCHEMA_VERSION,
        "plugin_schema_version": 1,
        "settings": settings,
        "items": [item],
    }


def _readme_source(request: ScaffoldRequest) -> str:
    result = "a `PriceResult`" if request.result_type == "price" else "a `ListingResult`"
    transport = (
        "the shared bounded `HttpScraperClient` transport"
        if request.transport == "http"
        else "a bare `ScraperClient` for a plugin-owned transport"
    )
    fields = ", ".join(f"`{spec.key}`" for spec in request.item_fields) or "none"
    settings = ", ".join(f"`{spec.key}`" for spec in request.settings) or "none"
    test_text = (
        "Delete the generated skipped placeholder and add mocked Client.scrape response and "
        "parser coverage."
        if request.include_tests
        else "No tests were generated. The verifier reports their absence as a non-blocking warning."
    )
    return f"""# {request.display_name} plugin

Tracks URLs on {", ".join(f"`{domain}`" for domain in request.domains)} whose paths begin
with `{request.url_prefix}` and returns {result}.

## Configuration

Rows use shared `id`, `name`, `target_price`, and optional `skip` fields plus the
required `url` input. Custom item fields: {fields}. Custom settings: {settings}.
The framework also supplies execution interval, log retention, error notification,
and repeated-alert settings. Copy `config.example.json` to
`config/{request.target}.json`.

## Implementation and dependencies

The generated client uses {transport}. Replace its `NotImplementedError` with bounded,
modeled scraping behavior. Add or adjust package-local dependencies only in
`requirements.txt`.

## Tests

{test_text}

## Verification

Run `./scripts/dev/setup.sh --{request.target}`, then
`./scripts/dev/plugin-check.sh --{request.target}`, and finally
`./scripts/dev/check.sh --debug`. See
[`CONTRIBUTING.md`](../../../../../CONTRIBUTING.md) for the complete contract,
including advanced URL-less or multi-URL inputs and configuration migrations.
"""


def _source_files(request: ScaffoldRequest) -> dict[str, str]:
    files = {
        "__init__.py": '"""Import-light package marker."""\n',
        "plugin.py": _plugin_source(request),
        "client.py": _client_source(request),
        "README.md": _readme_source(request),
        "config.example.json": json.dumps(_config_document(request), indent=2) + "\n",
    }
    dependencies = list(request.dependencies)
    if request.transport == "http" and "tls-client" not in dependencies:
        dependencies.insert(0, "tls-client")
    if dependencies:
        files["requirements.txt"] = "\n".join(dependencies) + "\n"
    return files


def _example_assertion(expression: str, expected: object) -> str:
    operator = "is" if isinstance(expected, bool) else "=="
    return f"    assert {expression} {operator} {_python_literal(expected)}"


def _test_files(request: ScaffoldRequest) -> dict[str, str]:
    imports = sorted(
        [
            "PLUGIN",
            "URL",
            *(f"ITEM_{spec.key.upper()}" for spec in request.item_fields),
            *(f"SETTING_{spec.key.upper()}" for spec in request.settings),
        ]
    )
    item = {
        "id": "sample-item",
        "name": "Sample item",
        "target_price": 100.0,
        "url": f"https://{request.domains[0]}{request.url_prefix}sample",
        **{spec.key: spec.example for spec in request.item_fields},
    }
    raw_settings = {spec.key: spec.example for spec in request.settings}
    assertions = [f"    assert values.items[0][URL] == {_python_literal(item['url'])}"]
    assertions.extend(
        _example_assertion(f"values.items[0][ITEM_{spec.key.upper()}]", spec.example)
        for spec in request.item_fields
    )
    assertions.extend(
        _example_assertion(f"values.settings[SETTING_{spec.key.upper()}]", spec.example)
        for spec in request.settings
    )
    if raw_settings:
        setting_lines = "\n".join(
            f"            {_python_literal(key)}: {_python_literal(value)},"
            for key, value in raw_settings.items()
        )
        settings_source = f"{{\n{setting_lines}\n        }}"
    else:
        settings_source = "{}"
    item_lines = "\n".join(
        f"                {_python_literal(key)}: {_python_literal(value)},"
        for key, value in item.items()
    )
    declaration_imports = ",\n    ".join(imports)
    assertion_source = "\n".join(assertions)
    return {
        "__init__.py": "",
        "test_client.py": f'''"""Behavior tests for the {request.target} plugin."""

import pytest
from support import decode_test_config

from core.scrapers.plugins.{request.target}.plugin import (
    {declaration_imports},
)


def test_example_values_decode_through_the_runtime_contract() -> None:
    values = decode_test_config(
        PLUGIN,
        {_python_literal(request.target)},
        settings={settings_source},
        items=[
            {{
{item_lines}
            }}
        ],
    )

{assertion_source}


def test_replace_placeholder_with_mocked_client_scrape_behavior() -> None:
    # {SCAFFOLD_TEST_TODO}: delete this placeholder after adding mocked Client.scrape tests.
    pytest.skip(
        "add mocked Client.scrape tests for successful and malformed responses, unavailable or "
        "unmatched pages, relevant HTTP statuses, URL validation, codecs, and clean shutdown; "
        "then delete this placeholder"
    )
''',
    }


def render_scaffold(request: ScaffoldRequest) -> ScaffoldFiles:
    source = tuple(
        GeneratedFile(path, contents) for path, contents in _source_files(request).items()
    )
    tests = None
    if request.include_tests:
        tests = tuple(
            GeneratedFile(path, contents) for path, contents in _test_files(request).items()
        )
    return ScaffoldFiles(source=source, tests=tests)


__all__ = ["GeneratedFile", "ScaffoldFiles", "render_scaffold"]
