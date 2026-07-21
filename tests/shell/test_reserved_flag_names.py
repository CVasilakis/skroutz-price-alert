"""Drift guard: RESERVED_PLUGIN_NAMES must equal the scripts' built-in '--<flag>' set.

``RESERVED_PLUGIN_NAMES`` exists because the management scripts match
their built-in flags (``--help``, ``--quiet``, ``--ping``, ``--status``, ``--update``)
*before* the per-plugin ``--*`` branch, so a plugin named after one of them would
register fine yet never be dispatchable from the command line. The authoritative set is
the ``case`` ladders in the shell scripts themselves; this test parses every script's
literal flag branches and asserts set-equality, so adding a new built-in flag to a
script without reserving the name (or vice versa) fails loudly instead of drifting.
"""

import re
import unittest
from pathlib import Path

from core.scrapers.framework.naming import RESERVED_PLUGIN_NAMES

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every script that parses command-line flags. lib/common.sh is sourced, not executed,
#: and parses no arguments.
SCRIPTS = sorted(
    [REPO_ROOT / "install.sh", REPO_ROOT / "update.sh"] + list((REPO_ROOT / "scripts").glob("*.sh"))
)

# A case branch whose pattern is made of literal flags, e.g. "-h|--help)" or "--ping)".
# Deliberately excludes the globs ("--*)", "*)") and the bare "--)" separator branch.
_CASE_BRANCH = re.compile(r"^\s*((?:-{1,2}[a-z][a-z-]*\|?)+)\)")
_FLAG_NAME = re.compile(r"--([a-z][a-z-]*)")


def builtin_flags_of(script: Path) -> set[str]:
    """Returns the long-flag names a script's case ladder claims as built-ins."""
    names: set[str] = set()
    for line in script.read_text().splitlines():
        match = _CASE_BRANCH.match(line)
        if match:
            names.update(_FLAG_NAME.findall(match.group(1)))
    return names


class TestReservedFlagNames(unittest.TestCase):
    def test_scripts_were_found(self):
        # Guard against a silent-green pass if the layout changes.
        self.assertGreaterEqual(len(SCRIPTS), 8, SCRIPTS)

    def test_reserved_names_match_the_scripts_builtin_flags(self):
        claimed: set[str] = set()
        for script in SCRIPTS:
            claimed |= builtin_flags_of(script)
        self.assertEqual(
            claimed | {"general"},
            set(RESERVED_PLUGIN_NAMES),
            "RESERVED_PLUGIN_NAMES (registry.py) and the scripts' built-in '--<flag>' "
            "case branches have drifted apart. Add the new name to whichever side is "
            "missing it - an unreserved built-in flag silently shadows any plugin "
            "registered under that name.",
        )
