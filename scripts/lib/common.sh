#!/bin/sh
# ==============================================================================
# common.sh - shared POSIX sh helpers for the Scrooge Alert management scripts.
#
# This file is meant to be SOURCED (with the POSIX `.` builtin), never executed:
#
#     . "$SCRIPT_DIR/lib/common.sh"          # from scripts/*.sh
#     . "$SCRIPT_DIR/scripts/lib/common.sh"  # from root install.sh / update.sh
#
# Caller contract: define BASE_DIR (the repository root) BEFORE sourcing.
# This file intentionally does NOT set `set -eu` (the sourcing script owns its
# shell options) and uses no `local` (a bashism); helper-internal variables use
# unique names to avoid clobbering the caller's scope.
#
# shellcheck disable=SC2034  # variables here (colors, SYSTEMD_USER_DIR) are
#                            # consumed by the sourcing scripts, not this file.
# ==============================================================================

# ------------------------------------------------------------------------------
# COLORS
# ------------------------------------------------------------------------------
# Colored only when stdout is a terminal. NO_COLOR (https://no-color.org) always
# wins; CLICOLOR_FORCE keeps colors on for a non-TTY (the snapshot harness sets it
# so the captured transcripts stay identical to what a terminal user sees).
if [ -z "${NO_COLOR:-}" ] && { [ -t 1 ] || [ -n "${CLICOLOR_FORCE:-}" ]; }; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    CYAN='\033[0;36m'
    NC='\033[0m' # No Color
else
    RED=''
    GREEN=''
    YELLOW=''
    CYAN=''
    NC=''
fi

SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

# ------------------------------------------------------------------------------
# ENVIRONMENT CHECKS
# ------------------------------------------------------------------------------

# Abort with an error if systemctl (systemd) is not available.
require_systemctl() {
    if ! command -v systemctl > /dev/null 2>&1; then
        printf "%b\n" "${RED}Error: systemctl (systemd) is not installed or not available.${NC}"
        exit 1
    fi
}

# ------------------------------------------------------------------------------
# PLUGIN / UNIT NAMING
# ------------------------------------------------------------------------------

# unit_name <plugin> <suffix>  ->  "<plugin>-scraper.<suffix>"
# This is the same convention status.py uses to locate per-plugin systemd units.
unit_name() {
    printf '%s-scraper.%s' "$1" "$2"
}

# plugin_in_list <needle> <item>...  ->  returns 0 if <needle> is one of the items.
# Call unquoted to split a space/newline list, e.g. plugin_in_list "$x" $PLUGINS
plugin_in_list() {
    _needle="$1"
    shift
    for _item in "$@"; do
        [ "$_item" = "$_needle" ] && return 0
    done
    return 1
}

# ------------------------------------------------------------------------------
# PLUGIN ENUMERATION
# ------------------------------------------------------------------------------

# list_plugins: print the machine name of every registered plugin, one per line,
# by querying the ScraperRegistry (the single source of truth). Requires the venv,
# but plugin discovery only imports each plugin's lightweight descriptor (plugin.py)
# - never its client/storage or transport libraries (tls_client, selenium, ...),
# which load lazily only when a scrape actually runs. Returns non-zero (printing
# nothing) if the venv is unavailable, so callers can fall back to
# list_installed_plugins.
list_plugins() {
    [ -x "$BASE_DIR/venv/bin/python3" ] || return 1
    PYTHONPATH="$BASE_DIR/src" "$BASE_DIR/venv/bin/python3" - 2>/dev/null <<'PY'
from core.scrapers.registry import ScraperRegistry
for target in ScraperRegistry.registered_targets():
    print(target)
PY
}

# list_plugin_configs: print "<plugin> <config_filename>" for every registered
# plugin (one pair per line), reusing plugin.get_config_filename(). Same venv
# requirement as list_plugins.
list_plugin_configs() {
    [ -x "$BASE_DIR/venv/bin/python3" ] || return 1
    PYTHONPATH="$BASE_DIR/src" "$BASE_DIR/venv/bin/python3" - 2>/dev/null <<'PY'
from core.scrapers.registry import ScraperRegistry
for target in ScraperRegistry.registered_targets():
    print(target, ScraperRegistry.get_plugin(target).get_config_filename())
PY
}

# list_plugin_requirements: print "<plugin> <abs_requirements_path>" for every
# registered plugin that ships its own requirements.txt (one pair per line),
# reusing plugin.get_requirements_path(). Plugins with no extra dependencies are
# omitted. The path is absolute, so it installs regardless of cwd. Same venv
# requirement as list_plugins.
list_plugin_requirements() {
    [ -x "$BASE_DIR/venv/bin/python3" ] || return 1
    PYTHONPATH="$BASE_DIR/src" "$BASE_DIR/venv/bin/python3" - 2>/dev/null <<'PY'
from core.scrapers.registry import ScraperRegistry
for target in ScraperRegistry.registered_targets():
    path = ScraperRegistry.get_plugin(target).get_requirements_path()
    if path:
        print(target, path)
PY
}

# list_plugin_timer_directives: print "<plugin><TAB><Key>=<Value>" for every
# systemd [Timer] directive of every registered plugin (one per line). The value
# is the plugin's *effective* cadence: ScraperRegistry.resolve_timer_directives()
# starts from plugin.get_timer_directives() and overrides OnCalendar with the
# user's config "settings.execution_interval" when it is set and valid (otherwise
# the plugin default). The plugin name is machine-readable (no whitespace), so a
# literal tab cleanly separates it from the directive, whose value may itself
# contain spaces (e.g. an OnCalendar like "*-*-* 00/2:00:00"). Same venv
# requirement as list_plugins.
list_plugin_timer_directives() {
    [ -x "$BASE_DIR/venv/bin/python3" ] || return 1
    PYTHONPATH="$BASE_DIR/src" "$BASE_DIR/venv/bin/python3" - 2>/dev/null <<'PY'
from core.constants import CONFIG_DIR
from core.scrapers.registry import ScraperRegistry
for target in ScraperRegistry.registered_targets():
    for key, value in ScraperRegistry.resolve_timer_directives(target, CONFIG_DIR).items():
        print(f"{target}\t{key}={value}")
PY
}

# list_interval_status: print "<plugin><TAB><status>" for every registered plugin
# (one per line), where status is how its execution_interval resolved:
#   ok      - config present with a valid, supported interval
#   default - no interval set; the plugin default is in effect
#   invalid - config sets an unsupported/unparseable interval
#   nocfg   - the config file is missing entirely
# schedule.sh uses this to decide whether to apply, warn, or skip a plugin's timer.
# Same venv requirement as list_plugins.
list_interval_status() {
    [ -x "$BASE_DIR/venv/bin/python3" ] || return 1
    PYTHONPATH="$BASE_DIR/src" "$BASE_DIR/venv/bin/python3" - 2>/dev/null <<'PY'
from core.constants import CONFIG_DIR
from core.scrapers.registry import ScraperRegistry
from core.scrapers.base.settings import KEY_INTERVAL
for target in ScraperRegistry.registered_targets():
    print(f"{target}\t{ScraperRegistry.resolve_value(target, KEY_INTERVAL, CONFIG_DIR).status}")
PY
}

# list_supported_intervals: print the canonical execution_interval keys as one
# comma-separated line (e.g. "15m, 30m, 1h, ..."), straight from the settings
# vocabulary (SUPPORTED_INTERVALS) so user-facing help text never drifts from the
# code. Same venv requirement as list_plugins.
list_supported_intervals() {
    [ -x "$BASE_DIR/venv/bin/python3" ] || return 1
    PYTHONPATH="$BASE_DIR/src" "$BASE_DIR/venv/bin/python3" - 2>/dev/null <<'PY'
from core.scrapers.base.settings import SUPPORTED_INTERVALS
print(", ".join(SUPPORTED_INTERVALS))
PY
}

# registry_diagnose: explain on stderr WHY plugin enumeration printed nothing,
# then return 1. The list_* helpers suppress stderr so their stdout stays a clean
# machine-readable stream, which would otherwise let a single malformed plugin
# masquerade as a broken venv. Two cases are distinguished:
#   1) the venv python is missing/broken            -> the reinstall hint;
#   2) the venv is fine but plugin discovery failed -> the actual one-line error
#      (e.g. the PluginDiscoveryError naming the offending plugin package).
# Callers use it in their "registry unreadable" error paths: registry_diagnose || exit 1
registry_diagnose() {
    if [ ! -x "$BASE_DIR/venv/bin/python3" ]; then
        printf "%b\n" "${RED}Error: Cannot read the scraper registry - the Python environment looks missing or broken.${NC}" >&2
        printf "%b\n" "Reinstall it with: ${CYAN}./scripts/uninstall.sh${NC} then ${CYAN}./install.sh${NC}" >&2
        return 1
    fi
    printf "%b\n" "${RED}Error: Scraper plugin discovery failed:${NC}" >&2
    PYTHONPATH="$BASE_DIR/src" "$BASE_DIR/venv/bin/python3" - >&2 <<'PY'
try:
    from core.scrapers.registry import ScraperRegistry
    targets = ScraperRegistry.registered_targets()
except Exception as e:
    print(f"  {type(e).__name__}: {e}")
else:
    print(f"  (discovery succeeded on retry: {len(targets)} scraper(s) registered)")
PY
    printf "%b\n" "Fix (or remove) the offending plugin package under ${CYAN}src/core/scrapers/${NC}, then retry." >&2
    return 1
}

# list_installed_plugins <suffix>: print the plugin name behind every installed
# "<plugin>-scraper.<suffix>" unit file in SYSTEMD_USER_DIR (<suffix> is "service"
# or "timer"). Glob based, so it needs no venv and still finds units whose plugin
# was deleted from the source tree - essential for robust teardown.
list_installed_plugins() {
    _suffix="$1"
    for _f in "$SYSTEMD_USER_DIR"/*-scraper."$_suffix"; do
        [ -e "$_f" ] || continue   # POSIX sh has no nullglob: skip the literal pattern
        _base="${_f##*/}"                       # strip directory
        printf '%s\n' "${_base%-scraper."$_suffix"}"  # strip "-scraper.<suffix>"
    done
}

# known_targets <suffix>: print every plugin a teardown command may act on - the
# union of registered plugins and installed "<plugin>-scraper.<suffix>" units -
# one per line, de-duplicated, preserving first-seen order. It is the validation
# set for an explicit --<plugin> (the name only has to resolve to something a
# teardown can act on), so a plugin removed from the source tree but still
# installed continues to appear. The no-flag set is narrower: the teardown scripts
# act on the installed units alone (list_installed_plugins), since a registered
# plugin that was never installed has nothing to stop/disable/remove.
known_targets() {
    _seen=" "
    for _t in $(list_plugins) $(list_installed_plugins "$1"); do
        case "$_seen" in
            *" $_t "*) ;;                          # already emitted
            *) _seen="$_seen$_t "; printf '%s\n' "$_t" ;;
        esac
    done
}

# is_known_target <plugin> <suffix>: succeed if <plugin> is a registered plugin
# OR has an installed "<plugin>-scraper.<suffix>" unit. The membership test for
# the teardown commands (disable/stop/uninstall): they only need a unit to act
# on, so they accept this union, whereas install/enable validate against the
# registry alone (they need code to run). A name in neither set is a real typo.
is_known_target() {
    plugin_in_list "$1" $(known_targets "$2")
}

# ------------------------------------------------------------------------------
# SYSTEMD UNIT STATE QUERIES
# ------------------------------------------------------------------------------

# timer_is_enabled <plugin> / timer_is_active <plugin>: echo the systemctl verdict
# ("enabled"/"active"/"" etc.) for that plugin's timer.
timer_is_enabled() {
    systemctl --user is-enabled "$(unit_name "$1" timer)" 2>/dev/null || true
}
timer_is_active() {
    systemctl --user is-active "$(unit_name "$1" timer)" 2>/dev/null || true
}

# service_state <plugin>: echo the ActiveState of that plugin's service
# (e.g. "active", "activating", "inactive", "").
service_state() {
    systemctl --user show -p ActiveState "$(unit_name "$1" service)" 2>/dev/null | cut -d= -f2
}

# ------------------------------------------------------------------------------
# SYSTEMD UNIT ACTIONS (per plugin)
# ------------------------------------------------------------------------------

# enable_one <plugin>: enable + start the plugin's timer. Returns systemctl's code.
enable_one() {
    systemctl --user enable --now "$(unit_name "$1" timer)" >/dev/null 2>&1
}

# stop_one <plugin>: stop the plugin's (oneshot) service, aborting a running scrape.
stop_one() {
    systemctl --user stop "$(unit_name "$1" service)" 2>/dev/null || true
}

# disable_one <plugin>: stop + disable the plugin's timer and service and clear any
# failed state. Mirrors the original single-plugin disable sequence.
disable_one() {
    _tmr="$(unit_name "$1" timer)"
    _svc="$(unit_name "$1" service)"
    systemctl --user stop    "$_tmr" 2>/dev/null || true
    systemctl --user disable "$_tmr" 2>/dev/null || true
    systemctl --user stop    "$_svc" 2>/dev/null || true
    systemctl --user disable "$_svc" 2>/dev/null || true
    systemctl --user reset-failed "$_svc" "$_tmr" 2>/dev/null || true
}

# ------------------------------------------------------------------------------
# SYSTEMD UNIT FILE GENERATION
# ------------------------------------------------------------------------------
# Both install.sh (provisioning) and schedule.sh (re-applying a changed cadence)
# render the same per-plugin unit pair, so the unit format lives here in exactly
# one place.

# plugin_timer_block <plugin> <all_directives>: print <plugin>'s [Timer] *trigger*
# directives, one "Key=Value" per line (no trailing newline, so the value is safe
# to capture with $(...)). <all_directives> is the tab-separated
# "<plugin>\t<Key>=<Value>" stream from list_plugin_timer_directives, captured once
# by the caller. RandomizedDelaySec and Persistent are framework-managed (appended
# by write_plugin_units), so any a plugin tries to set are dropped here. Prints
# nothing when the plugin declares no trigger directives.
plugin_timer_block() {
    _ptb_plugin="$1"
    _ptb_all="$2"
    _ptb_tab="$(printf '\t')"
    _ptb_block=""
    _ptb_old_ifs="$IFS"
    IFS='
'
    for _ptb_line in $_ptb_all; do
        [ "${_ptb_line%%"$_ptb_tab"*}" = "$_ptb_plugin" ] || continue
        _ptb_directive="${_ptb_line#*"$_ptb_tab"}"
        case "$_ptb_directive" in
            RandomizedDelaySec=*|Persistent=*) continue ;;
        esac
        if [ -z "$_ptb_block" ]; then
            _ptb_block="$_ptb_directive"
        else
            _ptb_block="$_ptb_block
$_ptb_directive"
        fi
    done
    IFS="$_ptb_old_ifs"
    printf '%s' "$_ptb_block"
}

# write_plugin_units <plugin> <timer_block>: (over)write the plugin's
# <plugin>-scraper.{service,timer} unit files in SYSTEMD_USER_DIR. <timer_block> is
# the plugin's [Timer] trigger directives (from plugin_timer_block); the
# framework-managed RandomizedDelaySec/Persistent are appended identically for every
# plugin, and the [Service] dispatches identically through run.sh. Requires BASE_DIR
# (the repository root). Returns non-zero if either file was not written.
write_plugin_units() {
    _wpu_plugin="$1"
    _wpu_block="$2"
    _wpu_service_file="$SYSTEMD_USER_DIR/$(unit_name "$_wpu_plugin" service)"
    _wpu_timer_file="$SYSTEMD_USER_DIR/$(unit_name "$_wpu_plugin" timer)"

    cat > "$_wpu_service_file" << EOF
[Unit]
Description=Scrooge Alert notification task for $_wpu_plugin

[Service]
Type=oneshot
WorkingDirectory=$BASE_DIR
ExecStart="$BASE_DIR/scripts/run.sh" --quiet --$_wpu_plugin
EOF

    cat > "$_wpu_timer_file" << EOF
[Unit]
Description=Run $_wpu_plugin scraper

[Timer]
$_wpu_block
RandomizedDelaySec=180s
Persistent=true

[Install]
WantedBy=timers.target
EOF

    [ -f "$_wpu_service_file" ] && [ -f "$_wpu_timer_file" ]
}

# read_timer_block <plugin>: print the installed <plugin>-scraper.timer's [Timer]
# *trigger* directives in the same normalized form as plugin_timer_block (one
# "Key=Value" per line, no trailing newline, with the framework-managed
# RandomizedDelaySec/Persistent and the section headers removed), or nothing if the
# unit file is absent. Lets schedule.sh tell whether a re-resolved cadence actually
# differs from what is already on disk, so an unchanged interval is a true no-op.
read_timer_block() {
    _rtb_file="$SYSTEMD_USER_DIR/$(unit_name "$1" timer)"
    [ -f "$_rtb_file" ] || return 0
    _rtb_in_timer=0
    _rtb_block=""
    while IFS= read -r _rtb_line; do
        case "$_rtb_line" in
            "[Timer]") _rtb_in_timer=1; continue ;;
            "["*"]") _rtb_in_timer=0; continue ;;
        esac
        [ "$_rtb_in_timer" -eq 1 ] || continue
        [ -n "$_rtb_line" ] || continue
        case "$_rtb_line" in
            RandomizedDelaySec=*|Persistent=*) continue ;;
        esac
        if [ -z "$_rtb_block" ]; then
            _rtb_block="$_rtb_line"
        else
            _rtb_block="$_rtb_block
$_rtb_line"
        fi
    done < "$_rtb_file"
    printf '%s' "$_rtb_block"
}
