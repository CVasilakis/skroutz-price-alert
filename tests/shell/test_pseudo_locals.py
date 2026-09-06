"""Drift guards for the shell layer's pseudo-local variable convention.

POSIX sh has no ``local``, so a function's variables are process globals. The
convention that keeps that safe is documented at the top of ``scripts/lib/common.sh``;
these tests pin the three rules it states, none of which ShellCheck can see:

1. A function assigns every pseudo-local it reads, so no call silently depends on
   what its caller happens to be holding.
2. Within one entry point's source set, no pseudo-local name is assigned by two
   different functions. Prefixes may repeat; names may not.
3. Every function-local in ``scripts/lib`` is prefixed, because library functions
   run inside a caller's namespace and cannot see what it holds. This is what lets
   an entry point, which owns its whole process, keep using bare names.

A collision here is silent: the shell reports nothing and the inner function's
value simply survives into the outer one.
"""

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

LIBRARIES = sorted((REPO_ROOT / "scripts/lib").glob("*.sh"))
#: The root dispatcher. An entry point like the others, but the only one that
#: sources no library: it stays standalone so it can still report a damaged
#: checkout, and repeats the few common.sh pieces it needs. Its own file header
#: states why. Named here so the exemption below is pinned rather than implied.
DISPATCHER = REPO_ROOT / "scrooge-alert"
#: Every script that is executed rather than sourced, and so owns its own process.
ENTRY_POINTS = sorted(
    list((REPO_ROOT / "scripts").glob("*.sh"))
    + list((REPO_ROOT / "scripts/dev").glob("*.sh"))
    + [DISPATCHER]
)

# A function definition, capturing its indentation so the matching close brace can be
# recognized. update.sh nests its whole body inside main(), so this has to nest too.
_FUNCTION = re.compile(r"^(\s*)([A-Za-z_][A-Za-z0-9_]*)\(\)\s*\{\s*$")
# An assignment to a _<abbrev>_ name. The name must be followed immediately by "=",
# which is what separates an assignment from a "$_x_y" = comparison.
_ASSIGNED = re.compile(r"(?:^|[^\w$])(_[a-z0-9]+_[a-z0-9_]*)=")
_LOOP_VARIABLE = re.compile(r"^\s*for\s+(_[a-z0-9]+_[a-z0-9_]*)\s")
_READ_VARIABLE = re.compile(r"\bread\s+(?:-r\s+)?(_[a-z0-9]+_[a-z0-9_]*)\b")
_USED = re.compile(r"\$\{?(_[a-z0-9]+_[a-z0-9_]*)")
# A bare (unprefixed) assignment. Anchored at the start of the line so that prose
# inside a continued printf argument, which begins with its quote, cannot match.
_BARE_ASSIGNED = re.compile(r"^\s*([a-z][a-z0-9_]*)=")
_SOURCE_LINE = re.compile(r'^\s*\.\s+"[^"]*/lib/([a-z_]+)\.sh"\s*$')


class Function:
    """One shell function's pseudo-local assignments, bare assignments, and reads."""

    def __init__(self, name: str, script: Path):
        self.name = name
        self.script = script
        self.assigned: set[str] = set()
        self.bare: set[str] = set()
        self.read: list[tuple[str, int]] = []

    @property
    def where(self) -> str:
        return f"{self.script.relative_to(REPO_ROOT)}:{self.name}"


def functions_of(script: Path) -> list[Function]:
    """Returns every function defined in a script, including nested definitions."""
    open_frames: list[tuple[Function, int]] = []
    found: list[Function] = []
    for number, line in enumerate(script.read_text().splitlines(), 1):
        definition = _FUNCTION.match(line)
        if definition:
            open_frames.append((Function(definition.group(2), script), len(definition.group(1))))
            continue
        if not open_frames:
            continue
        function, indent = open_frames[-1]
        if line == " " * indent + "}":
            found.append(function)
            open_frames.pop()
            continue
        function.assigned |= set(_ASSIGNED.findall(line))
        function.assigned |= set(_LOOP_VARIABLE.findall(line))
        function.assigned |= set(_READ_VARIABLE.findall(line))
        function.bare |= set(_BARE_ASSIGNED.findall(line))
        function.read += [(name, number) for name in _USED.findall(line)]
    return found


def libraries_sourced_by(script: Path) -> list[Path]:
    """Returns the libraries a script dot-sources, in order and without repeats."""
    names = [
        match.group(1)
        for line in script.read_text().splitlines()
        for match in [_SOURCE_LINE.match(line)]
        if match
    ]
    return [REPO_ROOT / "scripts/lib" / f"{name}.sh" for name in dict.fromkeys(names)]


class TestPseudoLocals(unittest.TestCase):
    def test_the_shell_layer_was_found(self):
        # Guard against a silent-green pass if the layout or the parser drifts.
        self.assertGreaterEqual(len(LIBRARIES), 5, LIBRARIES)
        self.assertGreaterEqual(len(ENTRY_POINTS), 16, ENTRY_POINTS)
        parsed = sum(len(functions_of(script)) for script in LIBRARIES + ENTRY_POINTS)
        self.assertGreaterEqual(parsed, 190, parsed)
        for script in ENTRY_POINTS:
            sourced = libraries_sourced_by(script)
            if script == DISPATCHER:
                self.assertEqual([], sourced, script)
            else:
                self.assertTrue(sourced, script)

    def test_every_function_assigns_the_pseudo_locals_it_reads(self):
        leaked = [
            f"{function.where} reads ${name} at line {number} without assigning it"
            for script in LIBRARIES + ENTRY_POINTS
            for function in functions_of(script)
            for name, number in function.read
            if name not in function.assigned
        ]
        self.assertEqual(
            [],
            leaked,
            "A function is reading a pseudo-local it never assigns, so it depends on "
            "whatever its caller left in that name. Assign it locally, or pass the value "
            "as a positional argument.",
        )

    def test_no_pseudo_local_name_is_assigned_by_two_functions_in_one_process(self):
        collisions: list[str] = []
        for entry_point in ENTRY_POINTS:
            owner: dict[str, str] = {}
            for script in libraries_sourced_by(entry_point) + [entry_point]:
                for function in functions_of(script):
                    for name in sorted(function.assigned):
                        claimed = owner.get(name)
                        if claimed is not None and claimed != function.where:
                            collisions.append(
                                f"{entry_point.name} loads both {claimed} and "
                                f"{function.where}, which assign ${name}"
                            )
                        owner[name] = function.where
        self.assertEqual(
            [],
            collisions,
            "Two functions loaded into the same process assign the same pseudo-local. "
            "POSIX sh has no 'local', so whichever runs inner overwrites the outer "
            "value with no error. Rename one, extending its abbreviation as "
            "restore_captured_states (_rcst_) does.",
        )

    def test_library_functions_use_only_prefixed_locals(self):
        unprefixed = [
            f"{function.where} assigns bare {sorted(function.bare)}"
            for script in LIBRARIES
            for function in functions_of(script)
            if function.bare
        ]
        self.assertEqual(
            [],
            unprefixed,
            "A scripts/lib function assigns an unprefixed variable. Library functions "
            "run inside a caller's namespace and cannot see what it holds, and entry "
            "points rely on that to keep using bare names safely.",
        )
