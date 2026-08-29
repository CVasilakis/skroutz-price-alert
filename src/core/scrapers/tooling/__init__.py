"""Contributor tooling for catalog metadata, verification, and plugin scaffolding.

Commands that act on one plugin, or on the catalog of plugins: the TSV bridge the
shell scripts read, the focused plugin verifier, and the scaffold wizard.

Not to be confused with :mod:`core.tooling`, which owns installation-lifecycle
commands such as schema migration. The names are similar because both are
command-line tooling; the split is by what they operate on — a single plugin here,
a whole install there. Neither imports the other.

Import-light contract: consumers import the specific tooling submodule they need.
"""

SCAFFOLD_TEST_TODO = "SCROOGE_SCAFFOLD_TODO"
"""Marker the scaffold leaves in its placeholder test, shared by the generator that
writes it and the verifier that warns while it is still there."""

__all__ = ["SCAFFOLD_TEST_TODO"]
