"""Shared inputs for the shell surfaces: canned sandbox worlds and the case registrar.

Mirrors :mod:`ui.catalog.inputs` for the panel surfaces: scenario modules read
declaratively by picking a named :class:`ShellWorld` preset (or deriving one with
``dataclasses.replace``) instead of restating sandbox state inline.

The cast, used consistently across every preset so transcripts stay comparable:

* ``skroutz`` - the one healthy registered-and-installed plugin.
* ``amazon``  - a second *registered* plugin, installed only where a scenario says so
  (the "registered but not installed" foil).
* ``ghost``   - an *orphan*: unit files still on disk, no longer in the catalog
  (its plugin was removed upstream).

All identifiers, paths, and error strings are synthetic fixtures - nothing here is
read from the live system.
"""

from functools import cache

from ui.catalog._base import Surface, scenario
from ui.harness.shell import ShellWorld, drive_shell

#: The one-line error catalog_diagnose surfaces when plugin discovery fails.
DISCOVERY_ERROR = "PluginDiscoveryError: plugin package 'zzzbroken' failed to import: boom"

#: skroutz installed and armed: timer enabled and active (the steady state).
WORLD_HEALTHY = ShellWorld(
    installed_timers=("skroutz",),
    installed_services=("skroutz",),
    enabled_timers=("skroutz",),
    active_timers=("skroutz",),
)

#: skroutz installed but not armed (fresh install.sh output, timer never enabled).
WORLD_INSTALLED = ShellWorld(installed_timers=("skroutz",), installed_services=("skroutz",))

#: skroutz registered, nothing installed (before any ./scrooge-alert install run).
WORLD_EMPTY = ShellWorld()

#: Two registered plugins, only skroutz installed - 'amazon' is the
#: "registered but not installed" foil for the teardown/enable/schedule notices.
WORLD_AMAZON_UNINSTALLED = ShellWorld(
    plugins=("skroutz", "amazon"),
    installed_timers=("skroutz",),
    installed_services=("skroutz",),
)

#: skroutz healthy plus the 'ghost' orphan (units on disk, plugin removed from the catalog).
WORLD_ORPHAN = ShellWorld(
    installed_timers=("skroutz", "ghost"),
    installed_services=("skroutz", "ghost"),
)

#: Only the 'ghost' orphan remains installed.
WORLD_ALL_ORPHANS = ShellWorld(installed_timers=("ghost",), installed_services=("ghost",))

#: Units installed but the venv is gone - catalog_diagnose's "environment missing" branch.
WORLD_NO_VENV = ShellWorld(
    venv=False,
    installed_timers=("skroutz",),
    installed_services=("skroutz",),
)

#: Units installed, venv fine, but plugin discovery raises - diagnose branch 2.
WORLD_BROKEN_CATALOG = ShellWorld(
    plugins=(),
    discovery_error=DISCOVERY_ERROR,
    installed_timers=("skroutz",),
    installed_services=("skroutz",),
)

#: Discovery raises before anything is installed. Nothing on disk hints at the
#: cause, so only the catalog's own status can name it.
WORLD_BROKEN_CATALOG_EMPTY = ShellWorld(plugins=(), discovery_error=DISCOVERY_ERROR)

#: The catalog loads and legitimately registers nothing, with an orphan unit
#: left behind - the foil for WORLD_BROKEN_CATALOG, identical on disk.
WORLD_EMPTY_CATALOG_ORPHAN = ShellWorld(
    plugins=(),
    installed_timers=("ghost",),
    installed_services=("ghost",),
)


def shell_case(surface: Surface, script: str):
    """A terse per-script registrar: each call registers one drive_shell scenario.

    The build is memoized (``functools.cache``): drive_shell is deterministic by
    contract, and the snapshot and color tests each build every scenario, so caching
    halves the subprocess runs without changing any output.

    Usage::

        _case = shell_case(Surface.SH_ENABLE, "scripts/enable.sh")
        _case("enable_success", "Arms an installed timer.", world=WORLD_INSTALLED)
    """

    def register(
        name: str,
        description: str,
        *args: str,
        world: ShellWorld = ShellWorld(),
        stdin: str = "",
        tags: tuple[str, ...] = (),
        shell_debug: bool | None = None,
    ) -> None:
        # --debug means "show underlying command output" in every script that owns the
        # flag, so deriving the exemption from the argument is right for all of them
        # but run.sh, which merely forwards --debug to run.py and keeps printing its
        # own operational sections. That one caller passes the answer explicitly.
        exempt = ("--debug" in args) if shell_debug is None else shell_debug

        @scenario(surface, name, description, tags, shell_debug=exempt)
        @cache
        def _build():
            return drive_shell(script, *args, world=world, stdin=stdin)

    return register
