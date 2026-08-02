"""Safe, additive scaffolding for an in-repository scraper plugin."""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from core.scrapers.domain import normalize_domain
from core.scrapers.framework.configuration import SCHEMA_VERSION
from core.scrapers.framework.intervals import SUPPORTED_INTERVALS
from core.scrapers.framework.naming import (
    FRAMEWORK_ITEM_KEYS,
    RESERVED_PLUGIN_NAMES,
    SNAKE_CASE_KEY,
)
from core.scrapers.framework.settings import framework_setting_specs
from core.scrapers.tooling import SCAFFOLD_TEST_TODO

ResultType = Literal["price", "listing"]
Transport = Literal["bare", "http"]
VALUE_TYPES = (
    "text",
    "integer",
    "number",
    "nonnegative-number",
    "boolean",
    "text-list",
)
_REQUIRED = object()
_PUBLIC_COMMAND_NAMES = frozenset(
    {
        "run",
        "ping",
        "status",
        "install",
        "enable",
        "disable",
        "stop",
        "schedule",
        "update",
        "uninstall",
    }
)


@dataclass(frozen=True)
class CustomValueSpec:
    key: str
    value_type: str
    example: object
    default: object = _REQUIRED
    sensitive: bool = False

    @property
    def required(self) -> bool:
        return self.default is _REQUIRED


@dataclass(frozen=True)
class ScaffoldRequest:
    target: str
    display_name: str
    domains: tuple[str, ...]
    url_prefix: str
    result_type: ResultType = "price"
    default_interval: str = "1h"
    transport: Transport = "bare"
    item_fields: tuple[CustomValueSpec, ...] = ()
    settings: tuple[CustomValueSpec, ...] = ()
    dependencies: tuple[str, ...] = ()
    include_tests: bool = True


@dataclass(frozen=True)
class ScaffoldResult:
    source: Path
    tests: Path | None


@dataclass(frozen=True)
class _ScaffoldDestinations:
    source: Path
    tests: Path


@dataclass(frozen=True)
class _CreatedDirectory:
    path: Path
    device: int
    inode: int


class ScaffoldRollbackError(RuntimeError):
    """A scaffold failed and one or more new directories could not be removed."""


def _safe_display_name(value: str) -> str:
    result = value.strip()
    if not result or any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise ValueError("display name must be nonblank and contain no control characters")
    return result


def _target_name(value: str) -> str:
    result = value.strip()
    if not result:
        raise ValueError("target name must not be empty")
    lowercase = result.lower()
    if lowercase != result and SNAKE_CASE_KEY.fullmatch(lowercase) is not None:
        raise ValueError(
            f"target name must use lowercase letters; try {lowercase!r} instead of {result!r}"
        )
    if not "a" <= result[0] <= "z":
        raise ValueError("target name must begin with a lowercase letter")
    if SNAKE_CASE_KEY.fullmatch(result) is None:
        raise ValueError(
            "target name may contain only lowercase letters, digits, and underscores; "
            "use underscores between words"
        )
    if result in RESERVED_PLUGIN_NAMES:
        raise ValueError(f"target name {result!r} is reserved; choose a store-specific name")
    if result in _PUBLIC_COMMAND_NAMES:
        raise ValueError(
            f"target name {result!r} matches a Scrooge Alert command; "
            "choose the store or service name instead"
        )
    return result


def _url_prefix(value: str) -> str:
    result = value.strip()
    if not result.startswith("/") or any(char in result for char in "?#"):
        raise ValueError("URL prefix must start with '/' and contain no query or fragment")
    if re.search(r"\s", result):
        raise ValueError("URL prefix must not contain whitespace")
    return result if result.endswith("/") else result + "/"


def _decode_value(value_type: str, raw: object) -> object:
    if value_type == "text":
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("must be a nonblank string")
        return raw.strip()
    if value_type == "integer":
        if isinstance(raw, bool) or not isinstance(raw, int):
            raise ValueError("must be an integer")
        return raw
    if value_type in {"number", "nonnegative-number"}:
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise ValueError("must be a number")
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("must be finite")
        if value_type == "nonnegative-number" and value < 0:
            raise ValueError("must be non-negative")
        return value
    if value_type == "boolean":
        if not isinstance(raw, bool):
            raise ValueError("must be a boolean")
        return raw
    if value_type == "text-list":
        if not isinstance(raw, (list, tuple)) or any(
            not isinstance(value, str) or not value.strip() for value in raw
        ):
            raise ValueError("must be an array of nonblank strings")
        return tuple(value.strip() for value in raw)
    raise ValueError(f"type must be one of {', '.join(VALUE_TYPES)}")


def _validate_specs(
    specs: tuple[CustomValueSpec, ...], *, kind: str, reserved: frozenset[str]
) -> tuple[CustomValueSpec, ...]:
    result: list[CustomValueSpec] = []
    seen: set[str] = set()
    for spec in specs:
        key = spec.key.strip()
        if SNAKE_CASE_KEY.fullmatch(key) is None or key in reserved:
            raise ValueError(f"{kind} key {key!r} must be a non-reserved snake_case name")
        if key in seen:
            raise ValueError(f"duplicate {kind} key {key!r}")
        seen.add(key)
        try:
            example = _decode_value(spec.value_type, spec.example)
            default = _REQUIRED if spec.required else _decode_value(spec.value_type, spec.default)
        except ValueError as exc:
            raise ValueError(f"{kind} {key!r} {exc}") from exc
        result.append(
            CustomValueSpec(
                key=key,
                value_type=spec.value_type,
                example=example,
                default=default,
                sensitive=spec.sensitive,
            )
        )
    return tuple(result)


def _safe_dependencies(values: tuple[str, ...]) -> tuple[str, ...]:
    result: list[str] = []
    for value in values:
        requirement = value.strip()
        if not requirement or any(ord(char) < 32 or ord(char) == 127 for char in requirement):
            raise ValueError("dependencies must be nonblank single-line requirement strings")
        if requirement not in result:
            result.append(requirement)
    return tuple(result)


def validate_request(request: ScaffoldRequest) -> ScaffoldRequest:
    target = _target_name(request.target)
    if not request.domains:
        raise ValueError("at least one domain is required")
    domains: list[str] = []
    for raw_domain in request.domains:
        domain = normalize_domain(raw_domain)
        if domain not in domains:
            domains.append(domain)
    if request.result_type not in {"price", "listing"}:
        raise ValueError("result type must be 'price' or 'listing'")
    if request.transport not in {"bare", "http"}:
        raise ValueError("transport must be 'bare' or 'http'")
    if request.default_interval not in SUPPORTED_INTERVALS:
        raise ValueError("default interval must be one of " + ", ".join(SUPPORTED_INTERVALS))
    framework_setting_keys = frozenset(
        spec.key for spec in framework_setting_specs(request.default_interval)
    )
    return ScaffoldRequest(
        target=target,
        display_name=_safe_display_name(request.display_name),
        domains=tuple(domains),
        url_prefix=_url_prefix(request.url_prefix),
        result_type=request.result_type,
        default_interval=request.default_interval,
        transport=request.transport,
        item_fields=_validate_specs(
            request.item_fields,
            kind="item field",
            reserved=FRAMEWORK_ITEM_KEYS | frozenset({"url"}),
        ),
        settings=_validate_specs(
            request.settings,
            kind="setting",
            reserved=framework_setting_keys,
        ),
        dependencies=_safe_dependencies(request.dependencies),
        include_tests=request.include_tests,
    )


def _scaffold_destinations(repo_root: Path, target: str) -> _ScaffoldDestinations:
    root = repo_root.resolve()
    return _ScaffoldDestinations(
        source=root / "src" / "core" / "scrapers" / "plugins" / target,
        tests=root / "tests" / "plugins" / target,
    )


def _path_entry_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _scaffold_collisions(repo_root: Path, target: str) -> tuple[Path, ...]:
    destinations = _scaffold_destinations(repo_root, target)
    return tuple(
        path for path in (destinations.source, destinations.tests) if _path_entry_exists(path)
    )


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
    domains_source = _python_literal(request.domains)
    import_block = (
        f"{math_import}from urllib.parse import SplitResult\n\n"
        f"from core.scrapers.api import {', '.join(imports)}"
    )
    blocks = [
        f'''"""Import-light descriptor for {request.display_name}."""\n\n{import_block}''',
    ]
    if decoder_source:
        blocks.append(decoder_source.rstrip())
    blocks.extend(
        [
            f"""def accepts_url(url: SplitResult) -> bool:
    return url.path.startswith({_python_literal(request.url_prefix)})
""",
            f"""URL = UrlField(
    key="url",
    domains={domains_source},
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
    access_source = "\n".join(access_lines)
    import_lines = [
        api_import,
        f"""from core.scrapers.plugins.{request.target}.plugin import (
    {declaration_imports}
)""",
    ]
    if support_import:
        import_lines.append(support_import)
    imports_source = "\n".join(import_lines)
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
        else "No tests were generated. Add mocked target-owned tests when practical; the verifier will warn but will not block solely because they are absent."
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


def _test_files(request: ScaffoldRequest) -> dict[str, str]:
    field_imports = [f"ITEM_{spec.key.upper()}" for spec in request.item_fields]
    setting_imports = [f"SETTING_{spec.key.upper()}" for spec in request.settings]
    imports = sorted(["PLUGIN", "URL", *field_imports, *setting_imports])
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
    declaration_imports = ",\n    ".join(imports)
    assertion_source = "\n".join(assertions)
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


def _example_assertion(expression: str, expected: object) -> str:
    operator = "is" if isinstance(expected, bool) else "=="
    return f"    assert {expression} {operator} {_python_literal(expected)}"


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, contents in files.items():
        with (root / relative).open("x", encoding="utf-8") as output:
            output.write(contents)


def _record_created_directory(path: Path) -> _CreatedDirectory:
    details = path.stat(follow_symlinks=False)
    return _CreatedDirectory(path, details.st_dev, details.st_ino)


def _rollback_created_directories(created: list[_CreatedDirectory]) -> tuple[str, ...]:
    failures: list[str] = []
    for entry in reversed(created):
        try:
            details = entry.path.stat(follow_symlinks=False)
            if (
                not stat.S_ISDIR(details.st_mode)
                or details.st_dev != entry.device
                or details.st_ino != entry.inode
            ):
                raise OSError("path was replaced after creation; refusing recursive cleanup")
            shutil.rmtree(entry.path)
        except FileNotFoundError:
            # Another cleanup path may already have removed this exact entry.
            # The rollback invariant is satisfied when nothing remains there.
            continue
        except OSError as exc:
            failures.append(f"{entry.path}: {exc}")
    return tuple(failures)


def create_plugin(repo_root: Path, request: ScaffoldRequest) -> ScaffoldResult:
    """Create only new plugin-owned paths, rolling back partial output."""
    request = validate_request(request)
    destinations = _scaffold_destinations(repo_root, request.target)
    source = destinations.source
    tests = destinations.tests if request.include_tests else None
    collisions = _scaffold_collisions(repo_root, request.target)
    if collisions:
        joined = ", ".join(str(path) for path in collisions)
        raise FileExistsError(f"refusing to overwrite existing path(s): {joined}")

    source_files = _source_files(request)
    test_files = _test_files(request) if tests is not None else None
    created: list[_CreatedDirectory] = []
    try:
        source.mkdir(parents=True)
        created.append(_record_created_directory(source))
        _write_tree(source, source_files)
        if tests is not None and test_files is not None:
            tests.mkdir(parents=True)
            created.append(_record_created_directory(tests))
            _write_tree(tests, test_files)
    except BaseException as exc:
        rollback_failures = _rollback_created_directories(created)
        if rollback_failures:
            detail = "; ".join(rollback_failures)
            raise ScaffoldRollbackError(
                f"scaffold failed and rollback was incomplete; recovery paths: {detail}"
            ) from exc
        raise
    return ScaffoldResult(source, tests)


def _json_value(raw: str, *, context: str) -> object:
    try:
        return _parse_strict_json(raw)
    except ValueError as exc:
        raise ValueError(f"{context} must be valid JSON: {exc}") from exc


def _reject_json_constant(value: str) -> object:
    raise ValueError(f"{value} is not permitted by strict JSON")


def _parse_strict_json(raw: str) -> object:
    """Decode standards-compliant JSON, rejecting Python's non-finite extensions."""
    try:
        return json.loads(raw, parse_constant=_reject_json_constant)
    except json.JSONDecodeError as exc:
        raise ValueError(exc.msg) from exc


def _argument_specs(
    required: list[list[str]] | None,
    optional: list[list[str]] | None,
    *,
    kind: str,
    sensitive: frozenset[str] = frozenset(),
) -> tuple[CustomValueSpec, ...]:
    specs: list[CustomValueSpec] = []
    for key, value_type, example in required or []:
        specs.append(
            CustomValueSpec(
                key,
                value_type,
                _json_value(example, context=f"{kind} {key!r} example"),
                sensitive=key in sensitive,
            )
        )
    for key, value_type, default, example in optional or []:
        specs.append(
            CustomValueSpec(
                key,
                value_type,
                _json_value(example, context=f"{kind} {key!r} example"),
                _json_value(default, context=f"{kind} {key!r} default"),
                key in sensitive,
            )
        )
    unknown_sensitive = sensitive - {spec.key for spec in specs}
    if unknown_sensitive:
        raise ValueError(
            "sensitive setting does not name a declared setting: "
            + ", ".join(sorted(unknown_sensitive))
        )
    return tuple(specs)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="./scripts/dev/plugin-create.sh",
        description="Create a guided additive in-repository scraper target scaffold.",
    )
    parser.add_argument("target", nargs="?")
    parser.add_argument("--display-name")
    parser.add_argument("--domain", action="append", dest="domains")
    parser.add_argument("--url-prefix")
    parser.add_argument("--result-type", choices=("price", "listing"))
    parser.add_argument("--default-interval", choices=tuple(SUPPORTED_INTERVALS))
    parser.add_argument("--transport", choices=("http", "bare"))
    tests = parser.add_mutually_exclusive_group()
    tests.add_argument("--with-tests", action="store_true", dest="include_tests")
    tests.add_argument("--without-tests", action="store_false", dest="include_tests")
    parser.set_defaults(include_tests=None)
    parser.add_argument(
        "--required-item-field", nargs=3, action="append", metavar=("KEY", "TYPE", "EXAMPLE_JSON")
    )
    parser.add_argument(
        "--optional-item-field",
        nargs=4,
        action="append",
        metavar=("KEY", "TYPE", "DEFAULT_JSON", "EXAMPLE_JSON"),
    )
    parser.add_argument(
        "--required-setting", nargs=3, action="append", metavar=("KEY", "TYPE", "EXAMPLE_JSON")
    )
    parser.add_argument(
        "--optional-setting",
        nargs=4,
        action="append",
        metavar=("KEY", "TYPE", "DEFAULT_JSON", "EXAMPLE_JSON"),
    )
    parser.add_argument("--sensitive-setting", action="append", default=[])
    parser.add_argument("--dependency", action="append", default=[])
    parser.add_argument(
        "--repo-root", default=str(Path(__file__).resolve().parents[4]), help=argparse.SUPPRESS
    )
    parser.add_argument("--shell-output", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--interactive", action="store_true", help=argparse.SUPPRESS)
    return parser


def _request_from_args(
    parser: argparse.ArgumentParser, args: argparse.Namespace
) -> ScaffoldRequest:
    required = {
        "target": args.target,
        "--display-name": args.display_name,
        "--domain": args.domains,
        "--url-prefix": args.url_prefix,
        "--result-type": args.result_type,
        "--default-interval": args.default_interval,
        "--transport": args.transport,
        "--with-tests or --without-tests": args.include_tests,
    }
    missing = [name for name, value in required.items() if value is None]
    if missing:
        parser.error("non-interactive mode requires " + ", ".join(missing))
    settings = _argument_specs(
        args.required_setting,
        args.optional_setting,
        kind="setting",
        sensitive=frozenset(args.sensitive_setting),
    )
    return ScaffoldRequest(
        target=args.target,
        display_name=args.display_name,
        domains=tuple(args.domains),
        url_prefix=args.url_prefix,
        result_type=args.result_type,
        default_interval=args.default_interval,
        transport=args.transport,
        item_fields=_argument_specs(
            args.required_item_field, args.optional_item_field, kind="item field"
        ),
        settings=settings,
        dependencies=tuple(args.dependency),
        include_tests=args.include_tests,
    )


def main(argv: list[str] | None = None) -> int:
    from core.scrapers.tooling.scaffold_terminal import (
        ScaffoldInterrupted,
        interruption_guard,
    )

    parser = _parser()
    args = parser.parse_args(argv)
    result: ScaffoldResult | None = None
    try:
        with interruption_guard():
            repo_root = Path(args.repo_root)
            if args.interactive:
                from core.scrapers.tooling.scaffold_wizard import collect_request

                request = collect_request(repo_root)
                if request is None:
                    if args.shell_output:
                        print("scaffold\t0\t\t0")
                    return 0
            else:
                request = _request_from_args(parser, args)
            result = create_plugin(repo_root, request)
            if args.interactive:
                from core.scrapers.tooling.scaffold_wizard import render_completion

                render_completion(request, result)
                return 0
    except (KeyboardInterrupt, ScaffoldInterrupted):
        if result is None:
            detail = "no new scaffold was confirmed"
        else:
            detail = (
                f"the scaffold was created at {result.source}, but final output was interrupted"
            )
        print(f"\nTarget scaffold interrupted; {detail}.", file=sys.stderr)
        return 130
    except (EOFError, OSError, RuntimeError, ValueError) as exc:
        print(f"Target scaffold failed: {exc}", file=sys.stderr)
        return 1
    if args.shell_output:
        assert result is not None
        print(f"scaffold\t1\t{result.source.name}\t{int(result.tests is not None)}")
    else:
        assert result is not None
        print(f"Created {result.source}")
        if result.tests is not None:
            print(f"Created {result.tests}")
        else:
            print("Tests were not generated; plugin-check will report a warning.")
        print(f"Next: ./scripts/dev/setup.sh --{result.source.name}")
        print(f"Then: ./scripts/dev/plugin-check.sh --{result.source.name}")
        print("Finally: ./scripts/dev/check.sh --debug")
    return 0


if __name__ == "__main__":
    sys.exit(main())
