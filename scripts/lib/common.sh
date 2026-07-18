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
# by filtering the immutable plugin manifest (the single source of truth). Requires the venv,
# but plugin discovery only imports each plugin's lightweight descriptor (plugin.py)
# - never its client/storage or transport libraries (tls_client, selenium, ...),
# which load lazily only when a scrape actually runs. Returns non-zero (printing
# nothing) if the venv is unavailable, so callers can fall back to
# list_installed_plugins.
registry_cli() {
    [ -x "$BASE_DIR/venv/bin/python3" ] || return 1
    PYTHONPATH="$BASE_DIR/src" "$BASE_DIR/venv/bin/python3" -m core.scrapers.cli "$@"
}

list_plugins() {
    plugin_manifest | awk -F '\t' '{ print $1 }'
}

plugin_display_name() {
    plugin_manifest | awk -F '\t' -v target="$1" '$1 == target { print $2; exit }'
}

list_plugin_examples() {
    plugin_manifest | awk -F '\t' '{ print $1 "\t" $3 }'
}

# list_plugin_requirements: print "<plugin><TAB><abs_requirements_path>" for every
# registered plugin that ships its own requirements.txt (one pair per line),
# using the catalog-computed colocated path. Plugins with no extra dependencies are
# omitted. The path is absolute, so it installs regardless of cwd. Same venv
# requirement as list_plugins.
list_plugin_requirements() {
    plugin_manifest | awk -F '\t' '$4 != "" { print $1 "\t" $4 }'
}

# list_plugin_schedules: print "<plugin><TAB><OnCalendar value>" for every
# registered plugin. The manifest resolves the user's execution_interval and carries
# its framework-owned systemd translation; plugins cannot inject timer directives.
list_plugin_schedules() {
    plugin_manifest | awk -F '\t' '{ print $1 "\t" $5 }'
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
    plugin_manifest | awk -F '\t' '{ print $1 "\t" $6 }'
}

# plugin_manifest: the only metadata bridge between POSIX scripts and Python.
# Columns are target, display name, example config, optional requirements,
# resolved OnCalendar, and interval status.
plugin_manifest() {
    registry_cli manifest --config-dir "$BASE_DIR/config" 2>/dev/null
}

# list_supported_intervals: print the canonical execution_interval keys as one
# comma-separated line (e.g. "15m, 30m, 1h, ..."), straight from the settings
# vocabulary (SUPPORTED_INTERVALS) so user-facing help text never drifts from the
# code. Same venv requirement as list_plugins.
list_supported_intervals() {
    registry_cli intervals 2>/dev/null
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
    registry_cli diagnose >&2 || :
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

# list_installed_targets: print the de-duplicated union of installed timer and
# service units. Teardown commands use this so a partially-installed pair (for
# example, a service whose timer file is gone) remains manageable.
list_installed_targets() {
    _lit_seen=" "
    for _lit_target in $(list_installed_plugins timer) $(list_installed_plugins service); do
        case "$_lit_seen" in
            *" $_lit_target "*) ;;
            *)
                _lit_seen="$_lit_seen$_lit_target "
                printf '%s\n' "$_lit_target"
                ;;
        esac
    done
}

# plugin_stream_value <plugin> <tab-separated-stream>: print the value paired
# with a plugin. Returns 1 when the stream has no row for that plugin.
plugin_stream_value() {
    _psv_plugin="$1"
    _psv_all="$2"
    _psv_tab="$(printf '\t')"
    _psv_old_ifs="$IFS"
    IFS='
'
    for _psv_line in $_psv_all; do
        if [ "${_psv_line%%"$_psv_tab"*}" = "$_psv_plugin" ]; then
            IFS="$_psv_old_ifs"
            printf '%s' "${_psv_line#*"$_psv_tab"}"
            return 0
        fi
    done
    IFS="$_psv_old_ifs"
    return 1
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

# known_targets_all: registered plugins plus every installed unit half. Commands
# that operate on both timer and service units use this broader validation/help set.
known_targets_all() {
    _kta_seen=" "
    for _kta_target in $(list_plugins) $(list_installed_targets); do
        case "$_kta_seen" in
            *" $_kta_target "*) ;;
            *)
                _kta_seen="$_kta_seen$_kta_target "
                printf '%s\n' "$_kta_target"
                ;;
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

is_known_target_any() {
    plugin_in_list "$1" $(known_targets_all)
}

# ------------------------------------------------------------------------------
# SYSTEMD UNIT STATE QUERIES
# ------------------------------------------------------------------------------

# systemd_property <unit> <property>: print one property from `systemctl show`.
# A failed bus/query or malformed response is an error, never an empty state. Using
# shell parameter expansion rather than a pipeline preserves systemctl's status in
# strictly POSIX sh (there is intentionally no non-POSIX `pipefail`).
systemd_property() {
    _sdp_unit="$1"
    _sdp_property="$2"
    if ! _sdp_output="$(systemctl --user show -p "$_sdp_property" "$_sdp_unit")"; then
        printf '%s\n' "Error: Could not query $_sdp_property for $_sdp_unit." >&2
        return 1
    fi
    case "$_sdp_output" in
        "$_sdp_property="*) printf '%s' "${_sdp_output#*=}" ;;
        *)
            printf '%s\n' "Error: systemctl returned an invalid $_sdp_property response for $_sdp_unit." >&2
            return 1
            ;;
    esac
}

# State predicates used by both actions and their callers.
state_is_stopped() {
    case "$1" in inactive|failed) return 0 ;; *) return 1 ;; esac
}

timer_state_is_disabled() {
    case "$1" in
        disabled|masked|masked-runtime|static|indirect|generated|transient|linked|linked-runtime|alias)
            return 0
            ;;
        *) return 1 ;;
    esac
}

# plugin_is_disabled <plugin>: 0 if the pair already meets disable_one's
# postcondition, 1 if work is required, 2 if the state could not be queried.
plugin_is_disabled() {
    _pid_plugin="$1"
    _pid_timer="$(unit_name "$_pid_plugin" timer)"
    _pid_service="$(unit_name "$_pid_plugin" service)"
    if ! _pid_timer_load="$(systemd_property "$_pid_timer" LoadState)" || \
       ! _pid_service_load="$(systemd_property "$_pid_service" LoadState)"; then
        return 2
    fi

    if [ "$_pid_timer_load" != "not-found" ]; then
        if ! _pid_timer_active="$(systemd_property "$_pid_timer" ActiveState)" || \
           ! _pid_timer_enabled="$(systemd_property "$_pid_timer" UnitFileState)"; then
            return 2
        fi
        state_is_stopped "$_pid_timer_active" && \
            timer_state_is_disabled "$_pid_timer_enabled" || return 1
    fi
    if [ "$_pid_service_load" != "not-found" ]; then
        if ! _pid_service_active="$(systemd_property "$_pid_service" ActiveState)"; then
            return 2
        fi
        state_is_stopped "$_pid_service_active" || return 1
    fi
    return 0
}

# Query the state properties the framework owns. LoadState=not-found is returned
# as a normal value; callers decide whether absence is benign for their operation.
timer_is_enabled() {
    systemd_property "$(unit_name "$1" timer)" UnitFileState
}
timer_is_active() {
    systemd_property "$(unit_name "$1" timer)" ActiveState
}

# service_state <plugin>: echo the ActiveState of that plugin's service
# (e.g. "active", "activating", "inactive"). Query errors stay errors.
service_state() {
    systemd_property "$(unit_name "$1" service)" ActiveState
}

# ------------------------------------------------------------------------------
# SYSTEMD UNIT ACTIONS (per plugin)
# ------------------------------------------------------------------------------

# enable_one <plugin>: enable + start the timer and verify the final state.
enable_one() {
    _eo_plugin="$1"
    _eo_timer="$(unit_name "$_eo_plugin" timer)"
    if ! systemctl --user enable --now "$_eo_timer" >/dev/null; then
        printf '%s\n' "Error: Failed to enable and start $_eo_timer." >&2
        return 1
    fi
    if ! _eo_load="$(systemd_property "$_eo_timer" LoadState)" || \
       ! _eo_enabled="$(systemd_property "$_eo_timer" UnitFileState)" || \
       ! _eo_active="$(systemd_property "$_eo_timer" ActiveState)"; then
        return 1
    fi
    if [ "$_eo_load" != "loaded" ] || [ "$_eo_enabled" != "enabled" ] || \
       [ "$_eo_active" != "active" ]; then
        printf '%s\n' "Error: $_eo_timer did not become loaded, enabled, and active." >&2
        return 1
    fi
}

# stop_one <plugin>: stop the service and verify it is no longer running. A unit
# that is genuinely absent is already stopped; query/transport failures are fatal.
stop_one() {
    _so_plugin="$1"
    _so_service="$(unit_name "$_so_plugin" service)"
    if ! _so_load="$(systemd_property "$_so_service" LoadState)"; then
        return 1
    fi
    [ "$_so_load" = "not-found" ] && return 0
    if ! systemctl --user stop "$_so_service" >/dev/null; then
        printf '%s\n' "Error: Failed to stop $_so_service." >&2
        return 1
    fi
    if ! _so_state="$(systemd_property "$_so_service" ActiveState)"; then
        return 1
    fi
    if ! state_is_stopped "$_so_state"; then
        printf '%s\n' "Error: $_so_service is still $_so_state after the stop request." >&2
        return 1
    fi
}

# disable_one <plugin>: make the timer/service pair unable to run, then verify the
# postcondition. All independent cleanup actions are attempted so one failure does
# not prevent another unit from being made safe; any failure makes the result fail.
disable_one() {
    _do_plugin="$1"
    _do_timer="$(unit_name "$_do_plugin" timer)"
    _do_service="$(unit_name "$_do_plugin" service)"
    _do_failed=0

    if ! _do_timer_load="$(systemd_property "$_do_timer" LoadState)"; then
        _do_failed=1
        _do_timer_load="query-failed"
    fi
    if ! _do_service_load="$(systemd_property "$_do_service" LoadState)"; then
        _do_failed=1
        _do_service_load="query-failed"
    fi

    if [ "$_do_timer_load" != "not-found" ] && [ "$_do_timer_load" != "query-failed" ]; then
        if ! systemctl --user stop "$_do_timer" >/dev/null; then
            printf '%s\n' "Error: Failed to stop $_do_timer." >&2
            _do_failed=1
        fi
        if ! systemctl --user disable "$_do_timer" >/dev/null; then
            printf '%s\n' "Error: Failed to disable $_do_timer." >&2
            _do_failed=1
        fi
        if ! systemctl --user reset-failed "$_do_timer" >/dev/null; then
            printf '%s\n' "Error: Failed to clear the failed state of $_do_timer." >&2
            _do_failed=1
        fi
    fi
    if [ "$_do_service_load" != "not-found" ] && [ "$_do_service_load" != "query-failed" ]; then
        if ! systemctl --user stop "$_do_service" >/dev/null; then
            printf '%s\n' "Error: Failed to stop $_do_service." >&2
            _do_failed=1
        fi
        if ! systemctl --user reset-failed "$_do_service" >/dev/null; then
            printf '%s\n' "Error: Failed to clear the failed state of $_do_service." >&2
            _do_failed=1
        fi
    fi

    if [ "$_do_timer_load" != "not-found" ] && [ "$_do_timer_load" != "query-failed" ]; then
        if ! _do_timer_active="$(systemd_property "$_do_timer" ActiveState)" || \
           ! _do_timer_enabled="$(systemd_property "$_do_timer" UnitFileState)"; then
            _do_failed=1
        elif ! state_is_stopped "$_do_timer_active" || \
             ! timer_state_is_disabled "$_do_timer_enabled"; then
            printf '%s\n' "Error: $_do_timer is not fully stopped and disabled." >&2
            _do_failed=1
        fi
    fi
    if [ "$_do_service_load" != "not-found" ] && [ "$_do_service_load" != "query-failed" ]; then
        if ! _do_service_active="$(systemd_property "$_do_service" ActiveState)"; then
            _do_failed=1
        elif ! state_is_stopped "$_do_service_active"; then
            printf '%s\n' "Error: $_do_service is still $_do_service_active." >&2
            _do_failed=1
        fi
    fi
    [ "$_do_failed" -eq 0 ]
}

# restart_timer_one <plugin>: re-arm a timer known to have been active before a
# unit-file update, then verify it returned to the active state.
restart_timer_one() {
    _rto_timer="$(unit_name "$1" timer)"
    if ! systemctl --user restart "$_rto_timer" >/dev/null; then
        printf '%s\n' "Error: Failed to restart $_rto_timer." >&2
        return 1
    fi
    if ! _rto_state="$(systemd_property "$_rto_timer" ActiveState)"; then
        return 1
    fi
    if [ "$_rto_state" != "active" ]; then
        printf '%s\n' "Error: $_rto_timer is $_rto_state after restart." >&2
        return 1
    fi
}

# ------------------------------------------------------------------------------
# SYSTEMD UNIT FILE GENERATION
# ------------------------------------------------------------------------------
# Both install.sh (provisioning) and schedule.sh (re-applying a changed cadence)
# render the same per-plugin unit pair, so the unit format lives here in exactly
# one place.

# render_plugin_service <plugin> <path> / render_plugin_timer <plugin> <OnCalendar> <path>:
# render complete unit files and explicitly preserve redirection failures even when
# the caller invokes these functions from an `if` condition.
render_plugin_service() {
    _rps_plugin="$1"
    _rps_path="$2"
    if ! cat > "$_rps_path" << EOF
[Unit]
Description=Scrooge Alert notification task for $_rps_plugin

[Service]
Type=oneshot
WorkingDirectory=$BASE_DIR
ExecStart="$BASE_DIR/scripts/run.sh" --quiet --$_rps_plugin
EOF
    then
        return 1
    fi
}

render_plugin_timer() {
    _rpt_plugin="$1"
    _rpt_calendar="$2"
    _rpt_path="$3"
    if ! cat > "$_rpt_path" << EOF
[Unit]
Description=Run $_rpt_plugin scraper

[Timer]
OnCalendar=$_rpt_calendar
Unit=$_rpt_plugin-scraper.service
RandomizedDelaySec=180s
Persistent=true

[Install]
WantedBy=timers.target
EOF
    then
        return 1
    fi
}

# write_plugin_timer_unit <plugin> <OnCalendar>: atomically replace only the timer
# unit. schedule.sh uses this so changing cadence never rewrites the service half.
write_plugin_timer_unit() {
    _wpt_plugin="$1"
    _wpt_calendar="$2"
    _wpt_file="$SYSTEMD_USER_DIR/$(unit_name "$_wpt_plugin" timer)"
    _wpt_tmp="$_wpt_file.tmp.$$"
    [ ! -e "$_wpt_tmp" ] || return 1
    if ! render_plugin_timer "$_wpt_plugin" "$_wpt_calendar" "$_wpt_tmp"; then
        rm -f "$_wpt_tmp"
        return 1
    fi
    if ! mv "$_wpt_tmp" "$_wpt_file"; then
        rm -f "$_wpt_tmp"
        return 1
    fi
    [ -f "$_wpt_file" ] && [ "$(read_timer_oncalendar "$_wpt_plugin")" = "$_wpt_calendar" ]
}

# write_plugin_units <plugin> <OnCalendar>: transactionally replace the plugin's
# <plugin>-scraper.{service,timer} unit files in SYSTEMD_USER_DIR. The framework
# owns every other timer key and the service dispatch. Requires BASE_DIR
# (the repository root). Returns non-zero if either file was not written.
write_plugin_units() {
    _wpu_plugin="$1"
    _wpu_calendar="$2"
    _wpu_service_file="$SYSTEMD_USER_DIR/$(unit_name "$_wpu_plugin" service)"
    _wpu_timer_file="$SYSTEMD_USER_DIR/$(unit_name "$_wpu_plugin" timer)"

    _wpu_service_tmp="$_wpu_service_file.tmp.$$"
    _wpu_timer_tmp="$_wpu_timer_file.tmp.$$"
    _wpu_service_backup="$_wpu_service_file.backup.$$"
    _wpu_timer_backup="$_wpu_timer_file.backup.$$"
    _wpu_service_existed=0
    _wpu_timer_existed=0

    [ ! -e "$_wpu_service_tmp" ] && [ ! -e "$_wpu_timer_tmp" ] && \
        [ ! -e "$_wpu_service_backup" ] && [ ! -e "$_wpu_timer_backup" ] || return 1
    if ! render_plugin_service "$_wpu_plugin" "$_wpu_service_tmp" || \
       ! render_plugin_timer "$_wpu_plugin" "$_wpu_calendar" "$_wpu_timer_tmp"; then
        rm -f "$_wpu_service_tmp" "$_wpu_timer_tmp"
        return 1
    fi
    if [ -e "$_wpu_service_file" ]; then
        _wpu_service_existed=1
        if ! cp -p "$_wpu_service_file" "$_wpu_service_backup"; then
            rm -f "$_wpu_service_tmp" "$_wpu_timer_tmp"
            return 1
        fi
    fi
    if [ -e "$_wpu_timer_file" ]; then
        _wpu_timer_existed=1
        if ! cp -p "$_wpu_timer_file" "$_wpu_timer_backup"; then
            rm -f "$_wpu_service_tmp" "$_wpu_timer_tmp" "$_wpu_service_backup"
            return 1
        fi
    fi
    if ! mv "$_wpu_service_tmp" "$_wpu_service_file"; then
        rm -f "$_wpu_service_tmp" "$_wpu_timer_tmp" "$_wpu_service_backup" "$_wpu_timer_backup"
        return 1
    fi
    if ! mv "$_wpu_timer_tmp" "$_wpu_timer_file"; then
        if [ "$_wpu_service_existed" -eq 1 ]; then
            mv "$_wpu_service_backup" "$_wpu_service_file" || return 1
        else
            rm -f "$_wpu_service_file"
        fi
        rm -f "$_wpu_timer_tmp" "$_wpu_service_backup" "$_wpu_timer_backup"
        return 1
    fi
    rm -f "$_wpu_service_backup" "$_wpu_timer_backup"
    [ -f "$_wpu_service_file" ] && [ -f "$_wpu_timer_file" ] && \
        [ "$(read_timer_oncalendar "$_wpu_plugin")" = "$_wpu_calendar" ]
}

# read_timer_oncalendar <plugin>: print the installed timer's framework-owned
# OnCalendar value, or nothing when the unit/value is absent.
read_timer_oncalendar() {
    _rto_file="$SYSTEMD_USER_DIR/$(unit_name "$1" timer)"
    [ -f "$_rto_file" ] || return 0
    while IFS= read -r _rto_line; do
        case "$_rto_line" in
            OnCalendar=*) printf '%s' "${_rto_line#OnCalendar=}"; return 0 ;;
        esac
    done < "$_rto_file"
}
