"""SH_UPDATE scenarios: every user-facing transcript update.sh can produce.

update.sh chains into a real nested `install.sh --update --<plugin>` run inside the
same sandbox, so the success transcripts intentionally include the install output -
that is exactly what the user sees.
"""

from dataclasses import replace

from ui.catalog._base import Surface
from ui.catalog.shell_inputs import ShellWorld, shell_case

_case = shell_case(Surface.SH_UPDATE, "update.sh")

#: skroutz installed and fully configured, so the nested install stays note-free.
_BASE = ShellWorld(
    installed_timers=("skroutz",), installed_services=("skroutz",),
    config_files=("skroutz.json",), env_file=True,
)

_case("help", "The short usage text.", "--help", world=_BASE, tags=("help",))

_case("clean_happy_path", "Clean tree already on 'main': the plainest, most common update.",
      world=_BASE, tags=("ok",))

_case("invalid_argument", "Any argument other than -h/--help is rejected.",
      "foo", world=_BASE, tags=("error",))

_case("no_systemctl", "A systemd-less host is refused before the destructive git reset.",
      world=replace(_BASE, tools="no-systemctl"), tags=("error",))

_case("dirty_declined", "Uncommitted changes detected; the user answers no.",
      world=replace(_BASE, git_dirty=True), stdin="n\n", tags=("error",))

_case("dirty_confirmed", "Uncommitted changes discarded on confirmation; full update runs.",
      world=replace(_BASE, git_dirty=True), stdin="y\n")

_case("branch_switch_notice", "A clean tree on another branch is switched to 'main'.",
      world=replace(_BASE, git_branch="beta"))

_case("checkout_fails", "git checkout main fails.",
      world=replace(_BASE, git_fail=("checkout",)), tags=("error",))

_case("fetch_fails", "git fetch fails.",
      world=replace(_BASE, git_fail=("fetch",)), tags=("error",))

_case("reset_fails", "git reset --hard origin/main fails.",
      world=replace(_BASE, git_fail=("reset",)), tags=("error",))

_case("install_fails_during_update", "The nested install.sh run fails mid-update.",
      world=replace(_BASE, pip_fail="upgrade"), tags=("error",))

_case("new_scrapers_available", "The new version ships a scraper the user has not installed.",
      world=replace(_BASE, plugins=("skroutz", "amazon")))
