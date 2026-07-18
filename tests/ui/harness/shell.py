"""Deterministic sandboxed execution of the management shell scripts.

The shell surfaces snapshot the transcript a management script (install.sh, update.sh,
scripts/*.sh) prints to the terminal. To reproduce every message without systemd, git,
the network, or a real venv, :func:`drive_shell` copies the *real* scripts into a
throwaway install tree, replaces every external command they touch with an
env-var-driven fake, runs one script with ``/bin/sh``, and returns a
:class:`BuildResult` whose renderable is the captured ANSI transcript
(``Text.from_ansi``) and whose ``exit_code`` feeds the ``# exit:`` snapshot header.

What is real: the scripts themselves and lib/common.sh (copied verbatim from the
repo), the unit files on disk, and the shell logic that stitches messages together.
What is faked (each a tiny POSIX-sh shim on a sandbox-only PATH):

* ``systemctl`` / ``loginctl`` - unit state answered from FAKE_* membership lists;
  mutating verbs succeed silently or fail on demand (FAKE_SYSTEMCTL_FAIL).
* ``git`` - branch/dirty answers for update.sh; checkout/fetch/reset failable.
* ``python3`` - answers install.sh's prerequisite probes and plants the venv
  responder when asked to create a venv.
* ``venv/bin/python3`` - the registry responder: recognizes each inline heredoc
  program common.sh pipes to it (by a marker string unique to that snippet) and
  prints canned plugin/config/timer data instead of importing the ScraperRegistry.

Determinism: the environment is built from scratch (never inherited), HOME and
SYSTEMD_USER_DIR live inside the sandbox, LC_ALL=C, all shim output is canned, and
the sandbox path is normalized to ``<BASE_DIR>`` in the captured text.
"""

import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from rich.text import Text

from ui.catalog._base import BuildResult

REPO_ROOT = Path(__file__).resolve().parents[3]

# The real files copied into every sandbox, at the same relative positions as a
# checkout, so each script's $0-based BASE_DIR discovery and `. lib/common.sh`
# sourcing work unchanged.
_SCRIPT_FILES = (
    "install.sh",
    "update.sh",
    "scripts/run.sh",
    "scripts/schedule.sh",
    "scripts/enable.sh",
    "scripts/disable.sh",
    "scripts/stop.sh",
    "scripts/uninstall.sh",
    "scripts/lib/common.sh",
)

# The coreutils allowlist symlinked into the sandbox bin/ in EVERY mode. PATH is
# sandbox-only, so these symlinks plus the shims are the entire command universe a
# script can reach — the host's genuine systemctl/python3 (or any unshimmed
# external) can never leak in.
_REAL_TOOLS = ("dirname", "cut", "cat", "chmod", "mkdir", "rm", "cp", "mv", "id")


@dataclass(frozen=True)
class ShellWorld:
    """Everything variable about the sandbox, defaulting to one healthy 'skroutz' install.

    Registry answers (served by the venv responder):
        plugins: Registered plugin names, in registry order.
        requirements: plugin -> absolute requirements path (plugins with none omitted).
        schedules: plugin -> resolved OnCalendar value; default hourly.
        interval_status: plugin -> ok|default|invalid|nocfg; default ok.
        supported_intervals: The one-line cadence vocabulary shown in help text.
        discovery_error: When set, the registry is unreadable: list_plugins prints
            nothing and registry_diagnose reports this one-line error.

    Venv state and failure injection:
        venv: Pre-create venv/bin/python3 (the responder) in the sandbox.
        ensurepip_missing: python3 -c "import ensurepip" fails (install.sh prereq).
        venv_create_fails: python3 -m venv fails.
        pip_fail: None | "upgrade" | "requirements" | "plugin" - which pip call fails.
        requirements_txt: The root requirements.txt exists.

    Installed unit state (real files in the sandbox SYSTEMD_USER_DIR):
        installed_timers / installed_services: Plugins with a unit file on disk.
        installed_blocks: plugin -> the on-disk timer's [Timer] trigger block;
            default OnCalendar=hourly (i.e. matching the default resolved cadence).
        unit_dir_readonly: Make SYSTEMD_USER_DIR and the seeded unit files
            unwritable, so atomic unit staging fails.

    systemctl / loginctl behavior:
        enabled_timers / active_timers: Plugins whose timer reports enabled / active.
        active_services / activating_services: Plugins whose service ActiveState.
        systemctl_fail: Verbs that exit 1 (e.g. ("enable",)).
        systemctl_noop: Mutating verbs that return success without changing state,
            exercising production postcondition checks.
        linger: The Linger= answer ("yes"/"no"); linger_enable_fails: enable-linger fails.

    git (update.sh): git_branch, git_dirty, git_fail (("checkout"|"fetch"|"reset", ...)).

    User config artifacts: config_files (created under config/), env_file (.env exists).

    tools: which shims exist. "full" = all four; "no-systemctl"/"no-python3" drops
        that one shim (PATH is sandbox-only in every mode, with _REAL_TOOLS symlinked in).
    """

    plugins: tuple[str, ...] = ("skroutz",)
    requirements: dict[str, str] | None = None
    schedules: dict[str, str] | None = None
    interval_status: dict[str, str] | None = None
    supported_intervals: str = "15m, 30m, 1h, 2h, 4h, 8h, 12h, 24h"
    discovery_error: str | None = None

    venv: bool = True
    ensurepip_missing: bool = False
    venv_create_fails: bool = False
    pip_fail: str | None = None
    requirements_txt: bool = True

    installed_timers: tuple[str, ...] = ()
    installed_services: tuple[str, ...] = ()
    installed_blocks: dict[str, str] | None = None
    unit_dir_readonly: bool = False

    enabled_timers: tuple[str, ...] = ()
    active_timers: tuple[str, ...] = ()
    active_services: tuple[str, ...] = ()
    activating_services: tuple[str, ...] = ()
    systemctl_fail: tuple[str, ...] = ()
    systemctl_noop: tuple[str, ...] = ()
    linger: str = "yes"
    linger_enable_fails: bool = False

    git_branch: str = "main"
    git_dirty: bool = False
    git_fail: tuple[str, ...] = ()

    config_files: tuple[str, ...] = ()
    env_file: bool = False

    tools: str = "full"


# ------------------------------------------------------------------------------
# PATH SHIMS (env-var-driven fakes; pure POSIX sh, shell builtins only)
# ------------------------------------------------------------------------------

_SYSTEMCTL_SHIM = """#!/bin/sh
# Stateful systemctl stand-in. Unit files live in the sandbox's real user-unit
# directory; mutable enabled/active markers live under FAKE_SYSTEMD_STATE_DIR.
[ "${1:-}" = "--user" ] && shift
verb="${1:-}"
[ $# -gt 0 ] && shift

stem() {
    _u="${1##*/}"
    _u="${_u%.timer}"
    _u="${_u%.service}"
    printf '%s' "${_u%-scraper}"
}
marker() {
    printf '%s/%s.%s' "$FAKE_SYSTEMD_STATE_DIR" "$1" "$(stem "$2")"
}

case " ${FAKE_SYSTEMCTL_FAIL:-} " in
    *" $verb "*) exit 1 ;;
esac

case "$verb" in
    show)
        # invoked as: show -p ActiveState <unit>
        property="$2"
        unit="$3"
        case "$property" in
            LoadState)
                if [ -e "$XDG_CONFIG_HOME/systemd/user/$unit" ]; then
                    echo "LoadState=loaded"
                else
                    echo "LoadState=not-found"
                fi ;;
            UnitFileState)
                if [ -e "$(marker enabled "$unit")" ]; then
                    echo "UnitFileState=enabled"
                else
                    echo "UnitFileState=disabled"
                fi ;;
            ActiveState)
                case "$unit" in
                    *.timer)
                        if [ -e "$(marker timer_active "$unit")" ]; then
                            echo "ActiveState=active"
                        else
                            echo "ActiveState=inactive"
                        fi ;;
                    *.service)
                        if [ -e "$(marker service_active "$unit")" ]; then
                            echo "ActiveState=active"
                        elif [ -e "$(marker service_activating "$unit")" ]; then
                            echo "ActiveState=activating"
                        else
                            echo "ActiveState=inactive"
                        fi ;;
                esac ;;
        esac ;;
    enable)
        for unit in "$@"; do :; done
        case " ${FAKE_SYSTEMCTL_NOOP:-} " in *" $verb "*) exit 0 ;; esac
        : > "$(marker enabled "$unit")"
        : > "$(marker timer_active "$unit")" ;;
    stop)
        unit="$1"
        case " ${FAKE_SYSTEMCTL_NOOP:-} " in *" $verb "*) exit 0 ;; esac
        case "$unit" in
            *.timer) rm -f "$(marker timer_active "$unit")" ;;
            *.service)
                rm -f "$(marker service_active "$unit")" \
                      "$(marker service_activating "$unit")" ;;
        esac ;;
    disable)
        unit="$1"
        case " ${FAKE_SYSTEMCTL_NOOP:-} " in *" $verb "*) exit 0 ;; esac
        rm -f "$(marker enabled "$unit")" ;;
    restart)
        unit="$1"
        case " ${FAKE_SYSTEMCTL_NOOP:-} " in *" $verb "*) exit 0 ;; esac
        : > "$(marker timer_active "$unit")" ;;
esac
exit 0
"""

_LOGINCTL_SHIM = """#!/bin/sh
case "${1:-}" in
    show-user) printf 'Linger=%s\\n' "${FAKE_LINGER:-yes}" ;;
    enable-linger) [ "${FAKE_LINGER_ENABLE_FAILS:-0}" = "1" ] && exit 1 ;;
esac
exit 0
"""

_GIT_SHIM = """#!/bin/sh
case " ${FAKE_GIT_FAIL:-} " in
    *" ${1:-} "*) exit 1 ;;
esac
case "${1:-}" in
    rev-parse) printf '%s\\n' "${FAKE_GIT_BRANCH:-main}" ;;
    status) [ "${FAKE_GIT_DIRTY:-0}" = "1" ] && printf ' M src/core/main.py\\n' ;;
esac
exit 0
"""

_PYTHON3_SHIM = """#!/bin/sh
# PATH python3: answers install.sh's prerequisite probes; `-m venv <dir>` plants
# the venv responder (or fails on demand).
case "${1:-}" in
    -c)
        [ "${FAKE_NO_ENSUREPIP:-0}" = "1" ] && exit 1 ;;
    -m)
        if [ "${2:-}" = "venv" ]; then
            [ "${FAKE_VENV_CREATE_FAILS:-0}" = "1" ] && exit 1
            mkdir -p "$3/bin"
            cp "$FAKE_VENV_TEMPLATE" "$3/bin/python3"
            chmod 755 "$3/bin/python3"
        fi ;;
esac
exit 0
"""

# Registry CLI invocations recognized by the venv responder. Guard-tested against
# common.sh so the snapshot harness cannot silently drift from the shell bridge.
VENV_RESPONDER_MARKERS: tuple[str, ...] = (
    "plugins --view targets",
    "plugins --view examples",
    "plugins --view requirements",
    "schedules --view calendar",
    "schedules --view status",
    "intervals",
    "diagnose",
)

# The venv responder implements the small machine-readable registry CLI used by
# common.sh, plus pip failure injection and run.sh's final dispatch marker.
_VENV_PYTHON_SHIM = """#!/bin/sh
# venv python responder: canned registry answers, pip failure injection, and a
# dispatch marker for run.sh's final exec.
case "${1:-}" in
    -m)
        shift
        case "$*" in
            "core.scrapers.cli plugins --view targets")
                [ -n "${FAKE_DISCOVERY_ERROR:-}" ] && exit 1
                for _p in ${FAKE_PLUGINS:-}; do printf '%s\\n' "$_p"; done ;;
            "core.scrapers.cli plugins --view examples")
                [ -n "${FAKE_DISCOVERY_ERROR:-}" ] && exit 1
                [ -n "${FAKE_PLUGIN_EXAMPLES:-}" ] && printf '%s\\n' "$FAKE_PLUGIN_EXAMPLES" ;;
            "core.scrapers.cli plugins --view requirements")
                [ -n "${FAKE_DISCOVERY_ERROR:-}" ] && exit 1
                [ -n "${FAKE_PLUGIN_REQUIREMENTS:-}" ] && printf '%s\\n' "$FAKE_PLUGIN_REQUIREMENTS" ;;
            "core.scrapers.cli schedules --view calendar"*)
                [ -n "${FAKE_DISCOVERY_ERROR:-}" ] && exit 1
                [ -n "${FAKE_SCHEDULES:-}" ] && printf '%s\\n' "$FAKE_SCHEDULES" ;;
            "core.scrapers.cli schedules --view status"*)
                [ -n "${FAKE_DISCOVERY_ERROR:-}" ] && exit 1
                [ -n "${FAKE_INTERVAL_STATUS:-}" ] && printf '%s\\n' "$FAKE_INTERVAL_STATUS" ;;
            "core.scrapers.cli intervals") printf '%s\\n' "${FAKE_SUPPORTED_INTERVALS:-}" ;;
            "core.scrapers.cli diagnose")
                if [ -n "${FAKE_DISCOVERY_ERROR:-}" ]; then
                    printf '  %s\\n' "$FAKE_DISCOVERY_ERROR"
                    exit 1
                fi
                _n=0
                for _p in ${FAKE_PLUGINS:-}; do _n=$((_n + 1)); done
                printf '  (discovery succeeded on retry: %s scraper(s) registered)\\n' "$_n" ;;
            *" -r requirements.txt") [ "${FAKE_PIP_FAIL:-}" = "requirements" ] && exit 1 ;;
            *" -r /"*)               [ "${FAKE_PIP_FAIL:-}" = "plugin" ] && exit 1 ;;
            *" pip")                 [ "${FAKE_PIP_FAIL:-}" = "upgrade" ] && exit 1 ;;
        esac ;;
    *)
        # run.sh's final exec: leave a marker line the golden can lock.
        printf '[exec] python3 %s\\n' "$*" ;;
esac
exit 0
"""


# ------------------------------------------------------------------------------
# SANDBOX ASSEMBLY
# ------------------------------------------------------------------------------

def _unit_text_timer(plugin: str, block: str) -> str:
    """A <plugin>-scraper.timer in the exact shape the shared timer renderer emits, so
    read_timer_block round-trips and schedule.sh's changed/unchanged compare works."""
    return (
        "[Unit]\n"
        f"Description=Run {plugin} scraper\n"
        "\n"
        "[Timer]\n"
        f"{block}\n"
        "RandomizedDelaySec=180s\n"
        "Persistent=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def _unit_text_service(plugin: str, base_dir: Path) -> str:
    return (
        "[Unit]\n"
        f"Description=Scrooge Alert notification task for {plugin}\n"
        "\n"
        "[Service]\n"
        "Type=oneshot\n"
        f"WorkingDirectory={base_dir}\n"
        f'ExecStart="{base_dir}/scripts/run.sh" --quiet --{plugin}\n'
    )


def _write_shims(bin_dir: Path, world: ShellWorld) -> None:
    shims = {
        "systemctl": _SYSTEMCTL_SHIM,
        "loginctl": _LOGINCTL_SHIM,
        "git": _GIT_SHIM,
        "python3": _PYTHON3_SHIM,
    }
    # The "missing tool" modes drop the shim AND /usr/bin from PATH (see _fake_env),
    # so the host's real binary can never satisfy `command -v` by accident.
    if world.tools == "no-systemctl":
        del shims["systemctl"]
    elif world.tools == "no-python3":
        del shims["python3"]

    for name, body in shims.items():
        shim = bin_dir / name
        shim.write_text(body)
        shim.chmod(0o755)

    # Always present: `python3 -m venv` copies it into the venv it "creates".
    (bin_dir / "venv-python-template").write_text(_VENV_PYTHON_SHIM)

    # Allowlist by construction (every mode): PATH is sandbox-only, so the only
    # real binaries a script can reach are the coreutils explicitly symlinked
    # here. An unshimmed external (a future `curl`, `date`, ...) fails loudly
    # instead of silently running the host's binary.
    for tool in _REAL_TOOLS:
        real = shutil.which(tool, path="/usr/bin:/bin")
        if real:
            (bin_dir / tool).symlink_to(real)


def _build_sandbox(world: ShellWorld) -> Path:
    sandbox = Path(tempfile.mkdtemp(prefix="scrooge-shell-")).resolve()

    for rel in _SCRIPT_FILES:
        dst = sandbox / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, dst)

    (sandbox / "home").mkdir()
    (sandbox / "config").mkdir()
    for cfg in world.config_files:
        (sandbox / "config" / cfg).touch()
    if world.requirements_txt:
        (sandbox / "requirements.txt").touch()
    if world.env_file:
        (sandbox / ".env").touch()

    bin_dir = sandbox / "bin"
    bin_dir.mkdir()
    _write_shims(bin_dir, world)

    if world.venv:
        venv_bin = sandbox / "venv" / "bin"
        venv_bin.mkdir(parents=True)
        shutil.copy(bin_dir / "venv-python-template", venv_bin / "python3")
        (venv_bin / "python3").chmod(0o755)

    unit_dir = sandbox / "xdg" / "systemd" / "user"
    unit_dir.mkdir(parents=True)
    blocks = world.installed_blocks or {}
    for plugin in world.installed_timers:
        block = blocks.get(plugin, "OnCalendar=hourly")
        (unit_dir / f"{plugin}-scraper.timer").write_text(_unit_text_timer(plugin, block))
    for plugin in world.installed_services:
        (unit_dir / f"{plugin}-scraper.service").write_text(_unit_text_service(plugin, sandbox))
    if world.unit_dir_readonly:
        # Keep both the directory and seeded files readonly so every unit-writing
        # implementation is exercised under a genuinely unwritable destination.
        for unit in unit_dir.iterdir():
            unit.chmod(0o444)
        unit_dir.chmod(0o555)

    state_dir = sandbox / "systemd-state"
    state_dir.mkdir()
    for plugin in world.enabled_timers:
        (state_dir / f"enabled.{plugin}").touch()
    for plugin in world.active_timers:
        (state_dir / f"timer_active.{plugin}").touch()
    for plugin in world.active_services:
        (state_dir / f"service_active.{plugin}").touch()
    for plugin in world.activating_services:
        (state_dir / f"service_activating.{plugin}").touch()

    return sandbox


def _fake_env(sandbox: Path, world: ShellWorld) -> dict[str, str]:
    """The complete child environment - built from scratch, never inherited."""
    plugins = world.plugins
    schedules = world.schedules if world.schedules is not None else {p: "hourly" for p in plugins}
    interval_status = (world.interval_status if world.interval_status is not None
                       else {p: "ok" for p in plugins})

    return {
        # Sandbox-only in every mode (an allowlist, not shim-by-precedence): the
        # shims plus the _REAL_TOOLS symlinks are the entire command universe.
        "PATH": str(sandbox / "bin"),
        "HOME": str(sandbox / "home"),
        "XDG_CONFIG_HOME": str(sandbox / "xdg"),  # -> SYSTEMD_USER_DIR=<sandbox>/xdg/systemd/user
        "LC_ALL": "C",
        "USER": "tester",  # install.sh's LINGER_USER; keeps `id -un` out of the transcript
        "COLUMNS": "100",
        # The scripts drop colors when stdout is not a TTY (as here, a pipe);
        # force them on so the transcript matches what a terminal user sees.
        "CLICOLOR_FORCE": "1",
        "FAKE_PLUGINS": " ".join(plugins),
        "FAKE_PLUGIN_EXAMPLES": "\n".join(
            f"{p}\t{sandbox}/src/core/scrapers/{p}/config.example.json" for p in plugins
        ),
        "FAKE_PLUGIN_REQUIREMENTS": "\n".join(f"{p}\t{r}" for p, r in (world.requirements or {}).items()),
        "FAKE_SCHEDULES": "\n".join(f"{p}\t{calendar}" for p, calendar in schedules.items()),
        "FAKE_INTERVAL_STATUS": "\n".join(f"{p}\t{s}" for p, s in interval_status.items()),
        "FAKE_SUPPORTED_INTERVALS": world.supported_intervals,
        "FAKE_DISCOVERY_ERROR": world.discovery_error or "",
        "FAKE_NO_ENSUREPIP": "1" if world.ensurepip_missing else "0",
        "FAKE_VENV_CREATE_FAILS": "1" if world.venv_create_fails else "0",
        "FAKE_VENV_TEMPLATE": str(sandbox / "bin" / "venv-python-template"),
        "FAKE_PIP_FAIL": world.pip_fail or "",
        "FAKE_ENABLED_TIMERS": " ".join(world.enabled_timers),
        "FAKE_ACTIVE_TIMERS": " ".join(world.active_timers),
        "FAKE_ACTIVE_SERVICES": " ".join(world.active_services),
        "FAKE_ACTIVATING_SERVICES": " ".join(world.activating_services),
        "FAKE_SYSTEMCTL_FAIL": " ".join(world.systemctl_fail),
        "FAKE_SYSTEMCTL_NOOP": " ".join(world.systemctl_noop),
        "FAKE_SYSTEMD_STATE_DIR": str(sandbox / "systemd-state"),
        "FAKE_LINGER": world.linger,
        "FAKE_LINGER_ENABLE_FAILS": "1" if world.linger_enable_fails else "0",
        "FAKE_GIT_BRANCH": world.git_branch,
        "FAKE_GIT_DIRTY": "1" if world.git_dirty else "0",
        "FAKE_GIT_FAIL": " ".join(world.git_fail),
    }


def _cleanup(sandbox: Path) -> None:
    unit_dir = sandbox / "xdg" / "systemd" / "user"
    if unit_dir.exists():
        unit_dir.chmod(0o755)  # undo unit_dir_readonly so rmtree can empty it
    shutil.rmtree(sandbox, ignore_errors=True)


# ------------------------------------------------------------------------------
# THE DRIVER
# ------------------------------------------------------------------------------

def drive_shell(script: str, *args: str,
                world: ShellWorld = ShellWorld(),
                stdin: str = "",
                border: str | None = None) -> BuildResult:
    """Runs one management script (sandbox-relative, e.g. ``"scripts/enable.sh"`` or
    ``"install.sh"``) in a fresh sandbox and returns its transcript as a BuildResult.

    stdout and stderr are interleaved (exactly what a terminal user sees), the
    sandbox path is normalized to ``<BASE_DIR>``, and the border color defaults to
    the exit-code convention: green for 0, red otherwise.
    """
    sandbox = _build_sandbox(world)
    try:
        proc = subprocess.run(
            ["/bin/sh", str(sandbox / script), *args],
            input=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=_fake_env(sandbox, world),
            cwd=sandbox,
            text=True,
            timeout=30,
        )
    finally:
        _cleanup(sandbox)

    transcript = proc.stdout.replace(str(sandbox), "<BASE_DIR>").replace("\r", "")
    transcript = re.sub(r"\.(tmp|backup)\.\d+", r".\1.<PID>", transcript)
    # sh's own diagnostics (e.g. a failed redirect in the readonly-unit-dir scenarios)
    # carry a script line number that drifts with every script edit - pin it.
    transcript = re.sub(r"\.sh: (?:line )?\d+:", ".sh: <line>:", transcript)
    return BuildResult(
        renderable=Text.from_ansi(transcript),
        border_color=border or ("green" if proc.returncode == 0 else "red"),
        exit_code=proc.returncode,
    )
