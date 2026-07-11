"""Render the full UI scenario gallery for human review.

Drives the *same* catalog as the snapshot tests, but renders with full color so you can
eyeball every panel after a change — replacing the old ritual of running the app and
inducing failure states by hand.

Usage (standalone; no env vars needed):

    ./venv/bin/python3 tests/ui/gallery.py                 # print every scenario (ANSI)
    ./venv/bin/python3 tests/ui/gallery.py --surface run   # only the interactive-run panels
    ./venv/bin/python3 tests/ui/gallery.py --surface sh-install  # one shell script's transcripts
    ./venv/bin/python3 tests/ui/gallery.py --tag interrupt # only scenarios tagged 'interrupt'
    ./venv/bin/python3 tests/ui/gallery.py --html /tmp/ui.html   # write a shareable page
    ./venv/bin/python3 tests/ui/gallery.py --list          # list scenario keys + descriptions

The shell surfaces (sh-install, sh-update, sh-schedule, sh-enable, sh-disable,
sh-stop, sh-run, sh-uninstall) render the management scripts' terminal transcripts,
captured from sandboxed runs of the real scripts (see harness/shell.py).

Test-only scenarios (``in_gallery=False``, currently the STARTUP layout guards) are
hidden from the default (unfiltered) output and HTML report; an explicit filter that
matches them (``--surface startup``, or a ``--tag`` they carry) renders them anyway.
"""

import argparse
import os
import sys

# Make src/ (the `core` package root) and the tests dir importable when run
# as a standalone script.
_HERE = os.path.dirname(os.path.abspath(__file__))      # tests/ui
_TESTS = os.path.dirname(_HERE)                          # tests
_REPO = os.path.dirname(_TESTS)                          # repo root
sys.path.insert(0, os.path.join(_REPO, "src"))
sys.path.insert(0, _TESTS)

from rich.console import Console                          # noqa: E402
from rich.rule import Rule                                # noqa: E402
from rich.text import Text                                # noqa: E402

from ui.catalog import ALL_SCENARIOS, Surface, SURFACE_INFO, TAG_VOCABULARY  # noqa: E402
from ui.harness.html_report import write_report            # noqa: E402
from ui.harness.rendering import paint                     # noqa: E402


def _filtered(surface, tag):
    out = []
    for sc in ALL_SCENARIOS:
        # Test-only scenarios (in_gallery=False, e.g. the STARTUP layout guards) are
        # hidden only from the unfiltered everything-view; an explicit --surface or
        # --tag that matches them (checked below) still reveals them.
        if not sc.in_gallery and not surface and not tag:
            continue
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
            console.print(Rule(SURFACE_INFO[current].label.upper(), style="magenta"))
        console.print()
        console.print(_header(sc))
        paint(console, sc.build())


def main() -> None:
    parser = argparse.ArgumentParser(description="Render the UI scenario gallery.")
    parser.add_argument("--surface", choices=[s.value for s in Surface], help="Only this surface.")
    parser.add_argument("--tag", choices=sorted(TAG_VOCABULARY),
                        help="Only scenarios carrying this tag.")
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
        write_report(scenarios, args.html)
        print(f"Wrote {len(scenarios)} scenario(s) to {args.html}")
    else:
        _render_all(Console(), scenarios)


if __name__ == "__main__":
    main()
