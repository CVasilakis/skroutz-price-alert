"""Drift guards for shell-shadowed and internal reserved plugin names.

``SHELL_RESERVED_PLUGIN_NAMES`` exists because target-selecting management scripts match
their built-in flags (``--help``, ``--quiet``, ``--ping``, and ``--status``)
*before* the per-plugin ``--*`` branch, so a plugin named after one of them would
register fine yet never be dispatchable from the command line. The authoritative set is
the ``case`` ladders in those shell scripts; this test parses their literal flag
branches and asserts set-equality, so adding a new built-in target-selection flag
without reserving the name (or vice versa) fails loudly instead of drifting. Flags
owned only by non-target interfaces such as migrate.sh's ``--check`` and hidden
``--machine`` protocol do not shadow target names and are deliberately excluded. Framework
pseudo-targets are reserved separately and combined into ``RESERVED_PLUGIN_NAMES``.
"""

import re
import unittest
from pathlib import Path

from core.scrapers.framework.naming import (
    INTERNAL_RESERVED_PLUGIN_NAMES,
    RESERVED_PLUGIN_NAMES,
    SHELL_RESERVED_PLUGIN_NAMES,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Every executable script that parses command-line flags.
SCRIPTS = sorted([REPO_ROOT / "scrooge-alert"] + list((REPO_ROOT / "scripts").glob("*.sh")))
TARGET_SELECTING_SCRIPTS = tuple(
    script for script in SCRIPTS if script.name not in {"scrooge-alert", "migrate.sh", "update.sh"}
)
SHARED_PARSERS = (REPO_ROOT / "scripts/lib/common.sh",)

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
        self.assertGreaterEqual(len(TARGET_SELECTING_SCRIPTS), 6, TARGET_SELECTING_SCRIPTS)

    def test_shell_reserved_names_match_the_scripts_builtin_flags(self):
        claimed: set[str] = set()
        for script in (*TARGET_SELECTING_SCRIPTS, *SHARED_PARSERS):
            claimed |= builtin_flags_of(script)
        self.assertEqual(
            claimed,
            set(SHELL_RESERVED_PLUGIN_NAMES),
            "SHELL_RESERVED_PLUGIN_NAMES (framework/naming.py) and the scripts' built-in "
            "'--<flag>' "
            "case branches have drifted apart. Add the new name to whichever side is "
            "missing it - an unreserved built-in flag silently shadows any plugin "
            "registered under that name.",
        )

    def test_all_reserved_names_are_the_shell_and_internal_sets(self):
        self.assertEqual(
            SHELL_RESERVED_PLUGIN_NAMES | INTERNAL_RESERVED_PLUGIN_NAMES,
            RESERVED_PLUGIN_NAMES,
        )
        self.assertEqual({"general", "migration", "reminder"}, INTERNAL_RESERVED_PLUGIN_NAMES)
