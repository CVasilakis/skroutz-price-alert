#!/bin/sh
# systemd discovery, state, lifecycle actions, and unit rendering.
# Source common.sh first.

require_systemctl() {
    if ! command -v systemctl >/dev/null 2>&1; then
        printf '%s\n' "Error: systemctl (systemd) is not installed or not available." >&2
        return 1
    fi
}

create_private_workspace() {
    case "$1" in ''|*[!a-z0-9_-]*) return 2 ;; esac
    command -v mktemp >/dev/null 2>&1 || {
        printf '%s\n' "Error: mktemp is required for safe file replacement." >&2
        return 1
    }
    (
        umask 077
        mktemp -d "$SYSTEMD_USER_DIR/.scrooge-$1.XXXXXX"
    )
}

# Discovery accepts regular files and links because teardown must remove links
# without following them. Every other entry type and malformed name is fatal.
list_installed_units() {
    case "$1" in service|timer) _liu_suffix="$1" ;; *) return 2 ;; esac
    _liu_failed=0
    for _liu_path in "$SYSTEMD_USER_DIR"/*-scraper."$_liu_suffix"; do
        path_entry_exists "$_liu_path" || continue
        _liu_name="${_liu_path##*/}"
        _liu_target="${_liu_name%-scraper."$_liu_suffix"}"
        if ! is_valid_target "$_liu_target"; then
            printf '%s\n' "Error: Malformed managed unit name: $_liu_name" >&2
            _liu_failed=1
            continue
        fi
        if [ ! -L "$_liu_path" ] && [ ! -f "$_liu_path" ]; then
            printf '%s\n' \
                "Error: Managed unit path is neither a regular file nor a symlink: $_liu_path" >&2
            _liu_failed=1
            continue
        fi
        printf '%s\n' "$_liu_target"
    done
    [ "$_liu_failed" -eq 0 ]
}

# Compatibility alias for callers outside the management scripts.
list_installed_plugins() {
    list_installed_units "$@"
}

list_installed_targets() {
    _lit_timers="$(list_installed_units timer)" || return 1
    _lit_services="$(list_installed_units service)" || return 1
    stream_union "$_lit_timers" "$_lit_services"
}

require_writable_unit_path() {
    _rwup_path="$1"
    if [ -L "$_rwup_path" ]; then
        printf '%s\n' \
            "Error: Refusing to replace managed unit symlink: $_rwup_path" >&2
        printf '%s\n' \
            "Remove it with ./scripts/uninstall.sh, then retry." >&2
        return 1
    fi
    if [ -e "$_rwup_path" ] && [ ! -f "$_rwup_path" ]; then
        printf '%s\n' \
            "Error: Managed unit path must be absent or a regular file: $_rwup_path" >&2
        return 1
    fi
}

validate_unit_destinations() {
    _vud_targets="$1"
    _vud_scope="$2"
    _vud_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _vud_target in $_vud_targets; do
        require_valid_target "$_vud_target" || {
            IFS="$_vud_old_ifs"
            return 1
        }
        case "$_vud_scope" in
            pair|timer)
                # Timer-only writes still depend on the paired service and must
                # reject a legacy service link before changing the timer.
                require_writable_unit_path \
                    "$SYSTEMD_USER_DIR/$(unit_name "$_vud_target" service)" || {
                    IFS="$_vud_old_ifs"
                    return 1
                }
                ;;
            *) IFS="$_vud_old_ifs"; return 2 ;;
        esac
        require_writable_unit_path \
            "$SYSTEMD_USER_DIR/$(unit_name "$_vud_target" timer)" || {
            IFS="$_vud_old_ifs"
            return 1
        }
    done
    IFS="$_vud_old_ifs"
}

systemd_property() {
    if ! run_captured systemctl --user show -p "$2" "$1"; then
        printf '%s\n' "Error: Could not query $2 for $1." >&2
        return 1
    fi
    _sdp_output="$CAPTURED_COMMAND_OUTPUT"
    case "$_sdp_output" in
        "$2="*) printf '%s' "${_sdp_output#*=}" ;;
        *)
            printf '%s\n' "Error: Invalid $2 response for $1." >&2
            return 1
            ;;
    esac
}

state_is_stopped() {
    case "$1" in inactive|failed) return 0 ;; *) return 1 ;; esac
}

timer_state_is_disabled() {
    case "$1" in
        disabled|masked|masked-runtime|static|indirect|generated|transient|linked|linked-runtime|alias)
            return 0 ;;
        *) return 1 ;;
    esac
}

timer_state_is_enabled() {
    case "$1" in enabled|enabled-runtime) return 0 ;; *) return 1 ;; esac
}

timer_is_enabled() {
    systemd_property "$(unit_name "$1" timer)" UnitFileState
}

timer_is_active() {
    systemd_property "$(unit_name "$1" timer)" ActiveState
}

service_state() {
    systemd_property "$(unit_name "$1" service)" ActiveState
}

reset_failed_if_failed() {
    _rfif_active="$(systemd_property "$1" ActiveState)" || return 1
    [ "$_rfif_active" != failed ] ||
        run_action systemctl --user reset-failed "$1"
}

plugin_is_disabled() {
    _pid_timer="$(unit_name "$1" timer)"
    _pid_service="$(unit_name "$1" service)"
    _pid_timer_load="$(systemd_property "$_pid_timer" LoadState)" || return 2
    _pid_service_load="$(systemd_property "$_pid_service" LoadState)" || return 2
    if [ "$_pid_timer_load" != "not-found" ]; then
        _pid_active="$(systemd_property "$_pid_timer" ActiveState)" || return 2
        _pid_enabled="$(systemd_property "$_pid_timer" UnitFileState)" || return 2
        state_is_stopped "$_pid_active" &&
            timer_state_is_disabled "$_pid_enabled" || return 1
    fi
    if [ "$_pid_service_load" != "not-found" ]; then
        _pid_active="$(systemd_property "$_pid_service" ActiveState)" || return 2
        state_is_stopped "$_pid_active" || return 1
    fi
}

enable_one() {
    _eo_timer="$(unit_name "$1" timer)"
    run_action systemctl --user enable --now "$_eo_timer" || {
        printf '%s\n' "Error: Failed to enable and start $_eo_timer." >&2
        return 1
    }
    _eo_load="$(systemd_property "$_eo_timer" LoadState)" || return 1
    _eo_enabled="$(systemd_property "$_eo_timer" UnitFileState)" || return 1
    _eo_active="$(systemd_property "$_eo_timer" ActiveState)" || return 1
    if [ "$_eo_load" != loaded ] || [ "$_eo_enabled" != enabled ] ||
        [ "$_eo_active" != active ]; then
        printf '%s\n' \
            "Error: $_eo_timer did not become loaded, enabled, and active." >&2
        return 1
    fi
}

stop_one() {
    _so_service="$(unit_name "$1" service)"
    _so_load="$(systemd_property "$_so_service" LoadState)" || return 1
    [ "$_so_load" != not-found ] || return 0
    run_action systemctl --user stop "$_so_service" || {
        printf '%s\n' "Error: Failed to stop $_so_service." >&2
        return 1
    }
    _so_active="$(systemd_property "$_so_service" ActiveState)" || return 1
    state_is_stopped "$_so_active" || {
        printf '%s\n' "Error: $_so_service is still $_so_active." >&2
        return 1
    }
}

disable_one() {
    _do_timer="$(unit_name "$1" timer)"
    _do_service="$(unit_name "$1" service)"
    _do_failed=0
    _do_timer_load="$(systemd_property "$_do_timer" LoadState)" || return 1
    _do_service_load="$(systemd_property "$_do_service" LoadState)" || return 1

    if [ "$_do_timer_load" != not-found ]; then
        _do_enabled="$(systemd_property "$_do_timer" UnitFileState)" ||
            _do_failed=1
        if [ "$_do_failed" -eq 0 ]; then
            if ! timer_state_is_enabled "$_do_enabled" &&
                ! timer_state_is_disabled "$_do_enabled"; then
                printf '%s\n' \
                    "Error: $_do_timer has unsupported state '$_do_enabled'." >&2
                _do_failed=1
            else
                reset_failed_if_failed "$_do_timer" || _do_failed=1
                run_action systemctl --user stop "$_do_timer" || _do_failed=1
                case "$_do_enabled" in
                    enabled)
                        run_action systemctl --user disable "$_do_timer" ||
                            _do_failed=1 ;;
                    enabled-runtime)
                        run_action systemctl --user --runtime disable "$_do_timer" ||
                            _do_failed=1 ;;
                esac
            fi
        fi
    fi
    if [ "$_do_service_load" != not-found ]; then
        reset_failed_if_failed "$_do_service" || _do_failed=1
        run_action systemctl --user stop "$_do_service" || _do_failed=1
    fi

    _do_timer_load="$(systemd_property "$_do_timer" LoadState)" || _do_failed=1
    if [ "$_do_failed" -eq 0 ] && [ "$_do_timer_load" != not-found ]; then
        _do_active="$(systemd_property "$_do_timer" ActiveState)" || _do_failed=1
        _do_enabled="$(systemd_property "$_do_timer" UnitFileState)" || _do_failed=1
        if [ "$_do_failed" -eq 0 ]; then
            state_is_stopped "$_do_active" &&
                timer_state_is_disabled "$_do_enabled" || _do_failed=1
        fi
    fi
    _do_service_load="$(systemd_property "$_do_service" LoadState)" || _do_failed=1
    if [ "$_do_failed" -eq 0 ] && [ "$_do_service_load" != not-found ]; then
        _do_active="$(systemd_property "$_do_service" ActiveState)" || _do_failed=1
        [ "$_do_failed" -ne 0 ] || state_is_stopped "$_do_active" ||
            _do_failed=1
    fi
    [ "$_do_failed" -eq 0 ]
}

restart_timer_one() {
    _rto_timer="$(unit_name "$1" timer)"
    run_action systemctl --user restart "$_rto_timer" || return 1
    [ "$(systemd_property "$_rto_timer" ActiveState)" = active ]
}

render_plugin_service() {
    if ! cat > "$2" << EOF
[Unit]
Description=Scrooge Alert notification task for $1

[Service]
Type=oneshot
WorkingDirectory=$BASE_DIR
ExecStart="$BASE_DIR/scripts/run.sh" --quiet --$1
EOF
    then
        return 1
    fi
}

render_plugin_timer() {
    _rpt_service="$(unit_name "$1" service)"
    if ! cat > "$3" << EOF
[Unit]
Description=Run $1 scraper

[Timer]
OnCalendar=$2
Unit=$_rpt_service
RandomizedDelaySec=180s
Persistent=true

[Install]
WantedBy=timers.target
EOF
    then
        return 1
    fi
}

read_timer_oncalendar() {
    _rto_path="$SYSTEMD_USER_DIR/$(unit_name "$1" timer)"
    [ -f "$_rto_path" ] && [ ! -L "$_rto_path" ] || return 0
    while IFS= read -r _rto_line; do
        case "$_rto_line" in
            OnCalendar=*)
                printf '%s' "${_rto_line#OnCalendar=}"
                return 0 ;;
        esac
    done < "$_rto_path"
}
