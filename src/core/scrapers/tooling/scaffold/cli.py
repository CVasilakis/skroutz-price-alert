"""CLI orchestration for safe, additive scraper plugin scaffolding."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import NoReturn

from core.scrapers.framework.intervals import SUPPORTED_INTERVALS
from core.scrapers.tooling.scaffold.api import create_plugin
from core.scrapers.tooling.scaffold.contracts import (
    CustomValueSpec,
    ScaffoldRequest,
    ScaffoldResult,
    json_value,
)


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
                json_value(example, context=f"{kind} {key!r} example"),
                sensitive=key in sensitive,
            )
        )
    for key, value_type, default, example in optional or []:
        specs.append(
            CustomValueSpec(
                key,
                value_type,
                json_value(example, context=f"{kind} {key!r} example"),
                json_value(default, context=f"{kind} {key!r} default"),
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


def _terminal_safe(value: object) -> str:
    printable = "".join(character if character.isprintable() else " " for character in str(value))
    return " ".join(printable.split()) or type(value).__name__


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        super().error(_terminal_safe(message))


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(
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


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[5]


def main(argv: list[str] | None = None, *, repo_root: Path | None = None) -> int:
    from core.scrapers.tooling.scaffold.terminal import (
        InteractiveTerminalUnavailable,
        ScaffoldInterrupted,
        TerminalStateError,
        interruption_guard,
    )

    parser = _parser()
    args = parser.parse_args(argv)
    result: ScaffoldResult | None = None
    try:
        with interruption_guard():
            resolved_root = (repo_root or _repository_root()).resolve()
            if args.interactive:
                from core.scrapers.tooling.scaffold.wizard import collect_request

                request = collect_request(resolved_root)
                if request is None:
                    return 0
            else:
                request = _request_from_args(parser, args)
            result = create_plugin(resolved_root, request)
            if args.interactive:
                from core.scrapers.tooling.scaffold.wizard import render_completion

                render_completion(request, result)
                return 0
    except (KeyboardInterrupt, ScaffoldInterrupted):
        if args.interactive and result is None:
            from core.scrapers.tooling.scaffold.wizard import render_cancellation

            render_cancellation()
            return 130
        detail = "no new scaffold was confirmed"
        if result is not None:
            detail = (
                f"the scaffold was created at {result.source}, but final output was interrupted"
            )
        print(f"\nTarget scaffold interrupted; {detail}.", file=sys.stderr)
        return 130
    except InteractiveTerminalUnavailable:
        # The wizard already rendered its Rich diagnostic while it still owned
        # presentation; the process contract must nevertheless report failure.
        return 1
    except TerminalStateError as exc:
        print(f"Target scaffold failed: {_terminal_safe(exc)}", file=sys.stderr)
        print(
            "If this terminal is not behaving normally, run 'stty sane' in it before retrying.",
            file=sys.stderr,
        )
        return 1
    except (EOFError, OSError, RuntimeError, ValueError) as exc:
        print(f"Target scaffold failed: {_terminal_safe(exc)}", file=sys.stderr)
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


__all__ = ["main"]
