#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname -- "$SCRIPT_DIR")"
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=scripts/lib/systemd.sh
. "$SCRIPT_DIR/lib/systemd.sh"

# shellcheck disable=SC2034  # cache values are consumed by shared catalog helpers
load_plugin_catalog() {
    case "$PLUGIN_CATALOG_STATE" in
        1) return 0 ;;
        2) return 1 ;;
    esac
    if [ "$DEBUG_MODE" -eq 1 ]; then
        if run_captured catalog_cli catalog; then
            PLUGIN_CATALOG_DATA="$CAPTURED_COMMAND_OUTPUT"
            PLUGIN_CATALOG_STATE=1
            return 0
        fi
    elif PLUGIN_CATALOG_DATA="$(catalog_cli catalog 2>/dev/null)"; then
        PLUGIN_CATALOG_STATE=1
        return 0
    fi
    PLUGIN_CATALOG_STATE=2
    PLUGIN_CATALOG_DATA=''
    return 1
}

print_help() {
    load_plugin_catalog || true
    _ph_registered="$(list_plugins 2>/dev/null || true)"
    _ph_installed="$(list_installed_units timer 2>/dev/null || true)"
    if [ "${SCROOGE_PUBLIC_COMMAND:-}" = enable ]; then
        printf '\n%s\n\n' "Usage: ./scrooge-alert enable [--help] [--debug] [--<target> ...]"
    else
        printf '\n%s\n\n' "Usage: enable.sh [-h] [--debug] [--<target> ...]"
    fi
    printf '%s\n' "Enable and start installed, registered scraper timers."
    printf '%s\n\n' "With no target flag, every eligible installed timer is enabled."
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n' "  --debug           show underlying command output"
    _ph_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _ph_target in $_ph_installed; do
        stream_contains "$_ph_target" "$_ph_registered" || continue
        printf '  --%-15s Enable only the %s scraper\n' \
            "$_ph_target" "$_ph_target"
    done
    IFS="$_ph_old_ifs"
    printf '\n'
}

enable_task() {
    _et_kind="$1"
    shift
    case "$_et_kind" in
        success) _et_marker='v'; _et_color="$GREEN" ;;
        failure) _et_marker='x'; _et_color="$RED" ;;
        info) _et_marker='i'; _et_color="$CYAN" ;;
        warning) _et_marker='!'; _et_color="$YELLOW" ;;
        *) return 2 ;;
    esac
    _et_prefix="    ${_et_color}[${_et_marker}]${NC} "
    _print_indented_wrapped "$_et_prefix" '        ' "$@"
}

enable_finish() {
    end_operational_output
    exit "$1"
}

show_selection_failure() {
    if [ -n "${_st_installed:-}" ] && [ -z "${_st_registered:-}" ]; then
        enable_task failure "The target catalog could not be loaded."
        if [ ! -x "$BASE_DIR/venv/bin/python3" ] || [ -L "$BASE_DIR/venv" ]; then
            enable_task warning \
                "Reinstall it with: $(command_text './scrooge-alert uninstall') then $(command_text './scrooge-alert install')"
        else
            enable_task warning \
                "Fix (or remove) the offending package under src/core/scrapers/plugins/, then retry."
        fi
        return
    fi

    _ssf_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _ssf_target in $TARGET_FLAGS; do
        if stream_contains "$_ssf_target" "${_st_installed:-}"; then
            if ! stream_contains "$_ssf_target" "${_st_registered:-}"; then
                enable_task failure \
                    "'$_ssf_target' is installed but no longer registered (orphan)."
                enable_task warning \
                    "Remove it with: $(command_text "./scrooge-alert uninstall --$_ssf_target")"
                IFS="$_ssf_old_ifs"
                return
            fi
        elif stream_contains "$_ssf_target" "${_st_registered:-}"; then
            enable_task failure \
                "'$_ssf_target' is registered but not installed."
            enable_task warning "Install it with: $(command_text "./scrooge-alert install --$_ssf_target")"
            IFS="$_ssf_old_ifs"
            return
        else
            enable_task failure "Unknown target '$_ssf_target'."
            enable_task info "Run $(command_text './scrooge-alert enable --help') for available targets."
            IFS="$_ssf_old_ifs"
            return
        fi
    done
    IFS="$_ssf_old_ifs"
    enable_task failure "The installed target timers could not be selected safely."
    enable_task info "Run $(command_text './scrooge-alert enable --debug') for underlying diagnostics."
}

enable_timer_property() {
    _etp_unit="$(unit_name "$1" timer)"
    _etp_property="$2"
    run_captured systemctl --user show -p "$_etp_property" "$_etp_unit" ||
        return 1
    case "$CAPTURED_COMMAND_OUTPUT" in
        "$_etp_property="*)
            printf '%s' "${CAPTURED_COMMAND_OUTPUT#*=}"
            ;;
        *) return 1 ;;
    esac
}

HELP_REQUESTED=0
for argument in "$@"; do
    case "$argument" in
        -h|--help) HELP_REQUESTED=1 ;;
    esac
done
if [ "$HELP_REQUESTED" -eq 1 ]; then
    DEBUG_MODE=0
    SCROOGE_INTERNAL_DEBUG=0
    export DEBUG_MODE SCROOGE_INTERNAL_DEBUG
    print_help
    exit 0
fi

begin_operational_output
if ! run_action parse_target_flags "$@"; then
    section_heading success "Enable preflight"
    enable_task failure "The command-line arguments are invalid."
    enable_task info "Run $(command_text './scrooge-alert enable --help') for usage."
    enable_finish 1
fi
if ! run_action require_systemctl; then
    section_heading success "Enable preflight"
    enable_task failure "systemctl (systemd) is not installed or not available."
    enable_task warning "Install systemd, then retry this command."
    enable_finish 1
fi
if [ "$DEBUG_MODE" -eq 1 ]; then
    load_plugin_catalog || true
fi
if ! run_action select_targets installed_registered_timers; then
    section_heading success "Enable preflight"
    show_selection_failure
    enable_finish 1
fi
PLUGINS="$SELECTED_TARGETS"

section_heading success "Background schedules"
if [ -z "$PLUGINS" ]; then
    enable_task info "No installed, registered target timers found."
    enable_task warning "Run $(command_text './scrooge-alert install') to provision targets."
    enable_finish 0
fi

FAILED=0
OLD_IFS="$IFS"
IFS='
'
# shellcheck disable=SC2086
for plugin in $PLUGINS; do
    state_failed=0
    if timer_enabled="$(enable_timer_property "$plugin" UnitFileState)"; then
        :
    else
        timer_enabled=''
        state_failed=1
    fi
    if timer_active="$(enable_timer_property "$plugin" ActiveState)"; then
        :
    else
        timer_active=''
        state_failed=1
    fi
    if [ "$state_failed" -eq 1 ]; then
        enable_task failure "[$plugin] Could not determine the timer state."
        enable_task info \
            "[$plugin] Run $(command_text "./scrooge-alert enable --debug --$plugin") for underlying diagnostics."
        FAILED=1
        continue
    fi
    if [ "$timer_enabled" = enabled ] && [ "$timer_active" = active ]; then
        enable_task info "[$plugin] Timer is already enabled and active."
        continue
    fi
    if run_with_progress "[$plugin] Enabling and starting the background schedule..." \
        run_action enable_one "$plugin"; then
        enable_task success "[$plugin] Background schedule enabled and started."
    else
        enable_task failure "[$plugin] Failed to enable and start the timer."
        enable_task info \
            "[$plugin] Run $(command_text "./scrooge-alert enable --debug --$plugin") for underlying diagnostics."
        FAILED=1
    fi
done
IFS="$OLD_IFS"

[ "$FAILED" -eq 0 ] || enable_finish 1
printf '\n'
section_heading success "Optional controls"
enable_task info \
    "If needed, disable background execution with: $(command_text './scrooge-alert disable')"
enable_finish 0
