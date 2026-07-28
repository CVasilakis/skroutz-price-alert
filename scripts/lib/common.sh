#!/bin/sh
# Shared POSIX-shell foundations. Callers define BASE_DIR before sourcing.

# shellcheck disable=SC2034  # colors and paths are consumed by sourcing scripts
if [ -z "${NO_COLOR:-}" ] && { [ -t 1 ] || [ -n "${CLICOLOR_FORCE:-}" ]; }; then
    RED='\033[0;31m'
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    CYAN='\033[0;36m'
    NC='\033[0m'
else
    RED=''
    GREEN=''
    YELLOW=''
    CYAN=''
    NC=''
fi

SYSTEMD_USER_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

# DEBUG_MODE is process-local public state. SCROOGE_INTERNAL_DEBUG is only for
# propagating that state between project scripts; invalid inherited values fail
# closed to normal (quiet) execution.
case "${SCROOGE_INTERNAL_DEBUG:-0}" in
    1) DEBUG_MODE=1 ;;
    *) DEBUG_MODE=0 ;;
esac
export DEBUG_MODE

begin_operational_output() {
    printf '\n'
}

section_heading() {
    case "$1" in
        success) _sh_marker='+'; _sh_color="$GREEN" ;;
        warning) _sh_marker='!'; _sh_color="$YELLOW" ;;
        *) return 2 ;;
    esac
    shift
    printf '%b[%s]%b %s\n' "$_sh_color" "$_sh_marker" "$NC" "$*"
}

task_status() {
    case "$1" in
        success) _ts_marker='v'; _ts_color="$GREEN" ;;
        failure) _ts_marker='x'; _ts_color="$RED" ;;
        info) _ts_marker='i'; _ts_color="$CYAN" ;;
        warning) _ts_marker='!'; _ts_color="$YELLOW" ;;
        *) return 2 ;;
    esac
    shift
    _ts_prefix="    ${_ts_color}[${_ts_marker}]${NC} "
    _print_indented_wrapped "$_ts_prefix" '        ' "$@"
}

_print_indented_wrapped() {
    _piw_first="$1"
    _piw_continuation="$2"
    shift 2
    _piw_width="${COLUMNS:-100}"
    case "$_piw_width" in
        ''|*[!0-9]*) _piw_width=100 ;;
    esac
    [ "$_piw_width" -ge 20 ] || _piw_width=20
    printf '%s\n' "$*" | awk \
        -v first="$_piw_first" \
        -v continuation="$_piw_continuation" \
        -v width="$_piw_width" '
        {
            prefix = first
            line = prefix
            for (i = 1; i <= NF; i++) {
                separator = (line == prefix ? "" : " ")
                if (length(line separator $i) > width && line != prefix) {
                    print line
                    prefix = continuation
                    line = prefix $i
                } else {
                    line = line separator $i
                }
            }
            print line
        }'
}

guidance() {
    _print_indented_wrapped '    ' '        ' "$@"
}

bullet() {
    _print_indented_wrapped '    - ' '        ' "$@"
}

end_operational_output() {
    printf '\n'
}

# run_action <command> [arguments...]
# Quiet by default; in debug mode the command owns the terminal streams.
run_action() {
    if [ "$DEBUG_MODE" -eq 1 ]; then
        "$@"
    else
        "$@" >/dev/null 2>&1
    fi
}

# run_captured <command> [arguments...]
# Exports the command's stdout through CAPTURED_COMMAND_OUTPUT and stderr through
# CAPTURED_COMMAND_STDERR. Both streams remain quiet normally and are mirrored to
# the terminal diagnostics stream after capture in debug mode. Using stderr for
# the mirror keeps stdout safe when a caller itself captures this helper's output.
# The command is always run exactly once, and its precise status is returned.
run_captured() {
    command -v mktemp >/dev/null 2>&1 || {
        printf '%s\n' "Error: mktemp is required for command capture." >&2
        return 1
    }
    _rc_parent="${TMPDIR:-/tmp}"
    if ! _rc_workspace="$(
        umask 077
        mktemp -d "$_rc_parent/scrooge-capture.XXXXXX"
    )"; then
        printf '%s\n' "Error: Could not create a private command-capture workspace." >&2
        return 1
    fi
    _rc_stdout="$_rc_workspace/stdout"
    _rc_stderr="$_rc_workspace/stderr"
    if "$@" >"$_rc_stdout" 2>"$_rc_stderr"; then
        _rc_status=0
    else
        _rc_status=$?
    fi
    CAPTURED_COMMAND_OUTPUT="$(cat "$_rc_stdout")"
    CAPTURED_COMMAND_STDERR="$(cat "$_rc_stderr")"
    if [ "$DEBUG_MODE" -eq 1 ]; then
        cat "$_rc_stdout" >&2
        cat "$_rc_stderr" >&2
    fi
    rm -rf "$_rc_workspace"
    return "$_rc_status"
}

path_entry_exists() {
    [ -e "$1" ] || [ -L "$1" ]
}

require_regular_owned_file() {
    if [ -L "$1" ] || [ ! -f "$1" ]; then
        printf '%s\n' "Error: Required project file must be a regular file: $1" >&2
        return 1
    fi
}

reject_project_venv_symlink() {
    if [ -L "$BASE_DIR/venv" ]; then
        printf '%s\n' \
            "Error: $BASE_DIR/venv must be a project-owned directory, not a symlink." >&2
        printf '%s\n' \
            "Remove the venv symlink, then recreate it with ./scripts/dev/setup.sh or ./install.sh." >&2
        return 1
    fi
}

unit_name() {
    printf '%s-scraper.%s' "$1" "$2"
}

is_valid_target() {
    case "$1" in
        ''|[!a-z]*|*[!a-z0-9_]*) return 1 ;;
        *) return 0 ;;
    esac
}

require_valid_target() {
    if ! is_valid_target "$1"; then
        printf '%s\n' \
            "Error: Invalid target '$1' (expected a nonblank snake_case name)." >&2
        return 1
    fi
}

stream_contains() {
    _sc_needle="$1"
    _sc_stream="$2"
    _sc_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086  # deliberate newline-only stream iteration
    for _sc_item in $_sc_stream; do
        if [ "$_sc_item" = "$_sc_needle" ]; then
            IFS="$_sc_old_ifs"
            return 0
        fi
    done
    IFS="$_sc_old_ifs"
    return 1
}

stream_add_unique() {
    _sau_stream="$1"
    _sau_item="$2"
    if [ -n "$_sau_stream" ]; then
        printf '%s\n' "$_sau_stream"
    fi
    stream_contains "$_sau_item" "$_sau_stream" || printf '%s\n' "$_sau_item"
}

stream_union() {
    _su_result=''
    _su_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086  # deliberate newline-only stream iteration
    for _su_item in $1 ${2:-}; do
        if ! stream_contains "$_su_item" "$_su_result"; then
            _su_result="$(stream_add_unique "$_su_result" "$_su_item")"
        fi
    done
    IFS="$_su_old_ifs"
    [ -z "$_su_result" ] || printf '%s\n' "$_su_result"
}

stream_for_display() {
    _sfd_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086  # deliberate newline-only stream iteration
    for _sfd_item in $1; do
        printf '%s ' "$_sfd_item"
    done
    IFS="$_sfd_old_ifs"
}

plugin_stream_value() {
    _psv_target="$1"
    _psv_rows="$2"
    _psv_tab="$(printf '\t')"
    _psv_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086  # deliberate newline-only stream iteration
    for _psv_row in $_psv_rows; do
        if [ "${_psv_row%%"$_psv_tab"*}" = "$_psv_target" ]; then
            IFS="$_psv_old_ifs"
            printf '%s' "${_psv_row#*"$_psv_tab"}"
            return 0
        fi
    done
    IFS="$_psv_old_ifs"
    return 1
}

# parse_target_flags <arguments...>
# Exports TARGET_FLAGS, TARGET_FLAGS_EXPLICIT, TARGET_HELP_REQUESTED, and the
# shared DEBUG_MODE. SCROOGE_INTERNAL_DEBUG propagates debug state only to child
# project scripts and is intentionally not a public command-line interface.
# Help is recognized in any position before other arguments are interpreted.
parse_target_flags() {
    TARGET_FLAGS=''
    TARGET_FLAGS_EXPLICIT=0
    TARGET_HELP_REQUESTED=0
    DEBUG_MODE=0
    for _ptf_arg in "$@"; do
        case "$_ptf_arg" in
            -h|--help) TARGET_HELP_REQUESTED=1 ;;
            --debug) DEBUG_MODE=1 ;;
        esac
    done
    SCROOGE_INTERNAL_DEBUG="$DEBUG_MODE"
    export DEBUG_MODE SCROOGE_INTERNAL_DEBUG
    [ "$TARGET_HELP_REQUESTED" -eq 0 ] || return 0

    for _ptf_arg in "$@"; do
        case "$_ptf_arg" in
            --debug) ;;
            --)
                printf '%s\n' "Error: Invalid argument: $_ptf_arg" >&2
                return 1
                ;;
            --?*)
                _ptf_target="${_ptf_arg#--}"
                require_valid_target "$_ptf_target" || return 1
                TARGET_FLAGS_EXPLICIT=1
                TARGET_FLAGS="$(stream_add_unique "$TARGET_FLAGS" "$_ptf_target")"
                ;;
            *)
                printf '%s\n' "Error: Invalid argument: $_ptf_arg" >&2
                return 1
                ;;
        esac
    done
}

# Catalog access defaults to the project venv. install.sh overrides
# CATALOG_PYTHON for its import-light, pre-venv validation pass.
catalog_cli() {
    _cc_python="${CATALOG_PYTHON:-$BASE_DIR/venv/bin/python3}"
    if [ "$_cc_python" = "$BASE_DIR/venv/bin/python3" ]; then
        reject_project_venv_symlink || return 1
    fi
    case "$_cc_python" in
        */*) [ -x "$_cc_python" ] || return 1 ;;
        *) command -v "$_cc_python" >/dev/null 2>&1 || return 1 ;;
    esac
    PYTHONPATH="$BASE_DIR/src" "$_cc_python" -m core.scrapers.tooling.cli "$@"
}

PLUGIN_CATALOG_STATE=0
PLUGIN_CATALOG_DATA=''
PLUGIN_SCHEDULE_STATE=0
PLUGIN_SCHEDULE_DATA=''

reset_catalog_cache() {
    PLUGIN_CATALOG_STATE=0
    PLUGIN_CATALOG_DATA=''
    PLUGIN_SCHEDULE_STATE=0
    PLUGIN_SCHEDULE_DATA=''
}

load_plugin_catalog() {
    case "$PLUGIN_CATALOG_STATE" in
        1) return 0 ;;
        2) return 1 ;;
    esac
    if PLUGIN_CATALOG_DATA="$(catalog_cli catalog 2>/dev/null)"; then
        PLUGIN_CATALOG_STATE=1
        return 0
    fi
    PLUGIN_CATALOG_STATE=2
    PLUGIN_CATALOG_DATA=''
    return 1
}

plugin_catalog() {
    load_plugin_catalog || return 1
    [ -z "$PLUGIN_CATALOG_DATA" ] || printf '%s\n' "$PLUGIN_CATALOG_DATA"
}

list_plugins() {
    plugin_catalog | awk -F '\t' '{ print $1 }'
}

plugin_display_name() {
    plugin_catalog | awk -F '\t' -v target="$1" '$1 == target { print $2; exit }'
}

list_plugin_examples() {
    plugin_catalog | awk -F '\t' '{ print $1 "\t" $3 }'
}

list_plugin_requirements() {
    plugin_catalog | awk -F '\t' '$4 != "" { print $1 "\t" $4 }'
}

load_plugin_schedules() {
    case "$PLUGIN_SCHEDULE_STATE" in
        1) return 0 ;;
        2) return 1 ;;
    esac
    if PLUGIN_SCHEDULE_DATA="$(
        catalog_cli schedules --config-dir "$BASE_DIR/config"
    )"; then
        PLUGIN_SCHEDULE_STATE=1
        return 0
    fi
    PLUGIN_SCHEDULE_STATE=2
    PLUGIN_SCHEDULE_DATA=''
    return 1
}

plugin_schedules() {
    load_plugin_schedules || return 1
    [ -z "$PLUGIN_SCHEDULE_DATA" ] || printf '%s\n' "$PLUGIN_SCHEDULE_DATA"
}

list_plugin_schedules() {
    plugin_schedules | awk -F '\t' '$3 != "error" { print $1 "\t" $2 }'
}

list_interval_status() {
    plugin_schedules | awk -F '\t' '{ print $1 "\t" $3 }'
}

list_schedule_errors() {
    plugin_schedules | awk -F '\t' '$3 == "error" { print $1 "\t" $4 }'
}

list_supported_intervals() {
    catalog_cli intervals 2>/dev/null
}

catalog_diagnose() {
    _cd_python="${CATALOG_PYTHON:-$BASE_DIR/venv/bin/python3}"
    if [ "$_cd_python" = "$BASE_DIR/venv/bin/python3" ]; then
        if [ -L "$BASE_DIR/venv" ]; then
            reject_project_venv_symlink
            return 1
        fi
        if [ ! -x "$_cd_python" ]; then
            printf '%s\n' \
                "Error: Cannot read the plugin catalog - the Python environment looks missing or broken." >&2
            printf '%s\n' \
                "Reinstall it with: ./scripts/uninstall.sh then ./install.sh" >&2
            return 1
        fi
    fi
    printf '%s\n' "Error: The scraper plugin catalog could not be loaded." >&2
    if catalog_cli diagnose >&2; then
        printf '%s\n' "The catalog is readable now; retry the command." >&2
    else
        printf '%s\n' \
            "Fix (or remove) the offending package under src/core/scrapers/plugins/, then retry." >&2
    fi
    return 1
}

# select_targets <registered|installed_registered_timers|installed_services|installed_union>
# Requires systemd.sh for installed policies. Exports SELECTED_TARGETS.
select_targets() {
    _st_policy="$1"
    _st_registered="$(list_plugins 2>/dev/null || true)"
    case "$_st_policy" in
        registered)
            _st_installed=''
            _st_available="$_st_registered"
            ;;
        installed_registered_timers)
            _st_installed="$(list_installed_units timer)" || return 1
            _st_available="$_st_installed"
            if [ -n "$_st_installed" ] && [ -z "$_st_registered" ]; then
                catalog_diagnose || return 1
            fi
            ;;
        installed_services)
            _st_installed="$(list_installed_units service)" || return 1
            _st_available="$_st_installed"
            ;;
        installed_union)
            _st_installed="$(list_installed_targets)" || return 1
            _st_available="$_st_installed"
            ;;
        *) return 2 ;;
    esac

    SELECTED_TARGETS=''
    if [ "$TARGET_FLAGS_EXPLICIT" -eq 0 ]; then
        case "$_st_policy" in
            registered) SELECTED_TARGETS="$_st_registered" ;;
            installed_registered_timers)
                _st_old_ifs="$IFS"
                IFS='
'
                # shellcheck disable=SC2086
                for _st_target in $_st_installed; do
                    if stream_contains "$_st_target" "$_st_registered"; then
                        SELECTED_TARGETS="$(
                            stream_add_unique "$SELECTED_TARGETS" "$_st_target"
                        )"
                    fi
                done
                IFS="$_st_old_ifs"
                ;;
            *) SELECTED_TARGETS="$_st_installed" ;;
        esac
        return 0
    fi

    _st_known="$(stream_union "$_st_registered" "$_st_installed")"
    _st_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _st_target in $TARGET_FLAGS; do
        case "$_st_policy" in
            registered)
                if ! stream_contains "$_st_target" "$_st_registered"; then
                    printf '%s\n' "Error: Unknown target '$_st_target'." >&2
                    printf '%s\n' \
                        "Available targets: $(stream_for_display "$_st_registered")" >&2
                    IFS="$_st_old_ifs"
                    return 1
                fi
                ;;
            installed_registered_timers)
                if ! stream_contains "$_st_target" "$_st_installed"; then
                    if stream_contains "$_st_target" "$_st_registered"; then
                        printf '%s\n' \
                            "Error: '$_st_target' is registered but not installed." >&2
                    else
                        printf '%s\n' "Error: Unknown target '$_st_target'." >&2
                    fi
                    IFS="$_st_old_ifs"
                    return 1
                fi
                if ! stream_contains "$_st_target" "$_st_registered"; then
                    printf '%s\n' \
                        "Error: '$_st_target' is installed but no longer registered (orphan)." >&2
                    printf '%s\n' \
                        "Remove it with: ./scripts/uninstall.sh --$_st_target" >&2
                    IFS="$_st_old_ifs"
                    return 1
                fi
                ;;
            installed_services|installed_union)
                if ! stream_contains "$_st_target" "$_st_known"; then
                    printf '%s\n' "Error: Unknown target '$_st_target'." >&2
                    printf '%s\n' \
                        "Available targets: $(stream_for_display "$_st_known")" >&2
                    IFS="$_st_old_ifs"
                    return 1
                fi
                if ! stream_contains "$_st_target" "$_st_installed"; then
                    printf '%s\n' \
                        "[$_st_target] is registered but not installed - nothing to do."
                    continue
                fi
                ;;
        esac
        SELECTED_TARGETS="$(stream_add_unique "$SELECTED_TARGETS" "$_st_target")"
    done
    IFS="$_st_old_ifs"
}
