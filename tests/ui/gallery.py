"""Render the full UI scenario gallery for human review.

Drives the *same* catalog as the snapshot tests, but renders with full color so you can
eyeball every panel after a change — replacing the old ritual of running the app and
inducing failure states by hand.

Usage (standalone; no env vars needed):

    ./venv/bin/python3 tests/ui/gallery.py                 # print every scenario (ANSI)
    ./venv/bin/python3 tests/ui/gallery.py --surface run   # only the interactive-run panels
    ./venv/bin/python3 tests/ui/gallery.py --tag interrupt # only scenarios tagged 'interrupt'
    ./venv/bin/python3 tests/ui/gallery.py --html /tmp/ui.html   # write a shareable page
    ./venv/bin/python3 tests/ui/gallery.py --list          # list scenario keys + descriptions
"""

import argparse
import os
import sys

# Make src/core and the tests dir importable when run as a standalone script.
_HERE = os.path.dirname(os.path.abspath(__file__))      # tests/ui
_TESTS = os.path.dirname(_HERE)                          # tests
_REPO = os.path.dirname(_TESTS)                          # repo root
sys.path.insert(0, os.path.join(_REPO, "src", "core"))
sys.path.insert(0, _TESTS)

from rich.console import Console                          # noqa: E402
from rich.rule import Rule                                # noqa: E402
from rich.text import Text                                # noqa: E402

from ui.catalog import ALL_SCENARIOS, Surface             # noqa: E402
from ui.harness.rendering import paint, make_recording_console  # noqa: E402


def _filtered(surface, tag):
    out = []
    for sc in ALL_SCENARIOS:
        if surface and sc.surface.value != surface:
            continue
        if tag and tag not in sc.tags:
            continue
        out.append(sc)
    return out


def _header(sc) -> Text:
    return Text.assemble(
        (f"[{sc.surface.value}] ", "bold cyan"),
        (sc.name, "bold"),
        (f"   —   {sc.description}", "dim"),
    )


def _render_all(console: Console, scenarios) -> None:
    current = None
    for sc in scenarios:
        if sc.surface != current:
            current = sc.surface
            console.print()
            console.print(Rule(f"{current.value.upper()} SURFACE", style="magenta"))
        console.print()
        console.print(_header(sc))
        paint(console, sc.build())


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the UI scenario gallery.")
    parser.add_argument("--surface", choices=[s.value for s in Surface], help="Only this surface.")
    parser.add_argument("--tag", help="Only scenarios carrying this tag.")
    parser.add_argument("--html", metavar="PATH", help="Write a self-contained HTML page instead of printing.")
    parser.add_argument("--list", action="store_true", help="List scenario keys + descriptions and exit.")
    args = parser.parse_args()

    scenarios = _filtered(args.surface, args.tag)

    if args.list:
        for sc in scenarios:
            print(f"{sc.snapshot_key:<40} {sc.description}")
        print(f"\n{len(scenarios)} scenario(s).")
        return

    if not scenarios:
        print("No scenarios match the given filter.")
        return

    if args.html:
        console = make_recording_console()
        _render_all(console, scenarios)
        console.save_html(args.html)
        print(f"Wrote {len(scenarios)} scenario(s) to {args.html}")
    else:
        _render_all(Console(), scenarios)


if __name__ == "__main__":
    main()
