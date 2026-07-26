"""Safe, additive scaffolding for an in-repository scraper plugin."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from core.scrapers.domain import normalize_domain
from core.scrapers.framework.configuration import SCHEMA_VERSION
from core.scrapers.framework.naming import RESERVED_PLUGIN_NAMES, SNAKE_CASE_KEY


@dataclass(frozen=True)
class ScaffoldRequest:
    target: str
    display_name: str
    domain: str
    url_prefix: str


def _safe_display_name(value: str) -> str:
    result = value.strip()
    if not result or any(ord(char) < 32 or ord(char) == 127 for char in result):
        raise ValueError("display name must be nonblank and contain no control characters")
    return result


def _url_prefix(value: str) -> str:
    result = value.strip()
    if not result.startswith("/") or any(char in result for char in "?#"):
        raise ValueError("URL prefix must start with '/' and contain no query or fragment")
    if re.search(r"\s", result):
        raise ValueError("URL prefix must not contain whitespace")
    return result if result.endswith("/") else result + "/"


def validate_request(request: ScaffoldRequest) -> ScaffoldRequest:
    target = request.target.strip()
    if SNAKE_CASE_KEY.fullmatch(target) is None or target in RESERVED_PLUGIN_NAMES:
        raise ValueError("target must be a non-reserved snake_case name")
    return ScaffoldRequest(
        target=target,
        display_name=_safe_display_name(request.display_name),
        domain=normalize_domain(request.domain),
        url_prefix=_url_prefix(request.url_prefix),
    )


def _source_files(request: ScaffoldRequest) -> dict[str, str]:
    sample_url = f"https://{request.domain}{request.url_prefix}sample"
    config = {
        "schema_version": SCHEMA_VERSION,
        "plugin_schema_version": 1,
        "settings": {
            "execution_interval": "1h",
            "log_retention_days": 7,
            "notify_scraping_errors": True,
            "suppress_repeated_price_alerts": False,
        },
        "items": [
            {
                "id": "sample-item",
                "name": "Sample item",
                "url": sample_url,
                "target_price": 100.0,
                "skip": False,
            }
        ],
    }
    return {
        "__init__.py": '"""Import-light package marker."""\n',
        "plugin.py": f'''"""Import-light descriptor for {request.display_name}."""

from urllib.parse import SplitResult

from core.scrapers.api import ScraperPlugin, UrlField


def accepts_url(url: SplitResult) -> bool:
    return url.path.startswith({request.url_prefix!r})


URL = UrlField(
    key="url",
    domains=({request.domain!r},),
    accepts_url=accepts_url,
)


PLUGIN = ScraperPlugin(
    display_name={request.display_name!r},
    item_fields=(URL,),
    reference_url=URL,
    default_interval="1h",
)
''',
        "client.py": f'''"""Client implementation for {request.display_name}."""

from core.scrapers.api import PriceResult, ScraperClient, TrackedItem
from core.scrapers.plugins.{request.target}.plugin import URL


class Client(ScraperClient):
    def scrape(self, item: TrackedItem) -> PriceResult:
        _source_url = item[URL]
        raise NotImplementedError("replace the scaffold with a mocked, tested scraper")
''',
        "README.md": f"""# {request.display_name} plugin

Tracks resource pages on `{request.domain}` whose paths begin with
`{request.url_prefix}` and returns a `PriceResult`.

## Configuration

Rows use shared `id`, `name`, `target_price`, and optional `skip` fields. This
plugin declares a required `url` input through `URL`. Copy
`config.example.json` to `config/{request.target}.json`.

## Implementation

Replace the scaffolded `Client.scrape` method with bounded network access and
fixture-driven parsing.

## Dependencies

Add a package-local `requirements.txt` only if the client needs dependencies
outside the core environment.

## Tests

Replace the generated failing test with mocked success, malformed response,
unavailable/no-match, relevant HTTP statuses, field and setting codec cases,
URL-shape cases, and clean client shutdown.

## Verification

Run `./scripts/dev/plugin-check.sh --{request.target}`, then
`./scripts/dev/check.sh`. See `CONTRIBUTING.md` for optional custom fields,
settings, listing results, HTTP helpers, migrations, and presentation rules.
""",
        "config.example.json": json.dumps(config, indent=2) + "\n",
    }


def _test_files(request: ScaffoldRequest) -> dict[str, str]:
    return {
        "__init__.py": "",
        "test_client.py": f'''"""Behavior tests for the {request.target} plugin."""

import pytest


def test_replace_scaffold_with_mocked_scraper_behavior() -> None:
    pytest.fail(
        "cover success, malformed response, unavailable/no-match, relevant HTTP "
        "statuses, field/setting codecs, and clean client shutdown"
    )
''',
    }


def _write_tree(root: Path, files: dict[str, str]) -> None:
    for relative, contents in files.items():
        (root / relative).write_text(contents, encoding="utf-8")


def create_plugin(repo_root: Path, request: ScaffoldRequest) -> tuple[Path, Path]:
    """Create only the new source and test packages, rolling back partial output."""
    request = validate_request(request)
    repo_root = repo_root.resolve()
    source = repo_root / "src" / "core" / "scrapers" / "plugins" / request.target
    tests = repo_root / "tests" / "plugins" / request.target
    collisions = [path for path in (source, tests) if path.exists()]
    if collisions:
        joined = ", ".join(str(path) for path in collisions)
        raise FileExistsError(f"refusing to overwrite existing path(s): {joined}")

    created: list[Path] = []
    try:
        source.mkdir(parents=True)
        created.append(source)
        _write_tree(source, _source_files(request))
        tests.mkdir(parents=True)
        created.append(tests)
        _write_tree(tests, _test_files(request))
    except Exception:
        for path in reversed(created):
            shutil.rmtree(path)
        raise
    return source, tests


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="./scripts/dev/plugin-create.sh",
        description="Create an additive in-repository scraper plugin scaffold.",
    )
    parser.add_argument("target")
    parser.add_argument("--display-name", required=True)
    parser.add_argument("--domain", required=True)
    parser.add_argument("--url-prefix", required=True)
    parser.add_argument(
        "--repo-root", default=str(Path(__file__).resolve().parents[4]), help=argparse.SUPPRESS
    )
    args = parser.parse_args(argv)
    try:
        source, tests = create_plugin(
            Path(args.repo_root),
            ScaffoldRequest(args.target, args.display_name, args.domain, args.url_prefix),
        )
    except (OSError, ValueError) as exc:
        print(f"Plugin scaffold failed: {exc}", file=sys.stderr)
        return 1
    print(f"Created {source}")
    print(f"Created {tests}")
    print(f"Next: ./scripts/dev/setup.sh --{args.target}")
    print(f"Then: ./scripts/dev/plugin-check.sh --{args.target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
