#!/bin/sh
# Transactional systemd provisioning. Source common.sh before this library.

unit_file_has_line() {
    _ufhl_file="$1"
    _ufhl_expected="$2"
    while IFS= read -r _ufhl_line; do
        [ "$_ufhl_line" = "$_ufhl_expected" ] && return 0
    done < "$_ufhl_file"
    return 1
}

validate_staged_pair() {
    _vsp_target="$1"
    _vsp_calendar="$2"
    _vsp_service="$3"
    _vsp_timer="$4"
    [ -s "$_vsp_service" ] && [ -s "$_vsp_timer" ] &&
        unit_file_has_line "$_vsp_service" "Type=oneshot" &&
        unit_file_has_line "$_vsp_service" "WorkingDirectory=$BASE_DIR" &&
        unit_file_has_line "$_vsp_service" \
            "ExecStart=\"$BASE_DIR/scripts/run.sh\" --quiet --$_vsp_target" &&
        unit_file_has_line "$_vsp_timer" "OnCalendar=$_vsp_calendar" &&
        unit_file_has_line "$_vsp_timer" "Unit=$(unit_name "$_vsp_target" service)" &&
        unit_file_has_line "$_vsp_timer" "RandomizedDelaySec=180s" &&
        unit_file_has_line "$_vsp_timer" "Persistent=true"
}

capture_timer_state() {
    _cts_target="$1"
    _cts_path="$2"
    _cts_timer="$(unit_name "$_cts_target" timer)"
    _cts_service="$(unit_name "$_cts_target" service)"
    _cts_timer_load="$(systemd_property "$_cts_timer" LoadState)" || return 1
    _cts_service_load="$(systemd_property "$_cts_service" LoadState)" || return 1

    if [ "$_cts_timer_load" = "not-found" ]; then
        _cts_enabled="absent"
        _cts_active="absent"
    elif [ "$_cts_timer_load" = "loaded" ]; then
        _cts_enabled="$(systemd_property "$_cts_timer" UnitFileState)" || return 1
        _cts_active="$(systemd_property "$_cts_timer" ActiveState)" || return 1
        if [ "$_cts_enabled" != "enabled" ] &&
           ! timer_state_is_disabled "$_cts_enabled"; then
            printf '%s\n' "Error: $_cts_timer has unexpected enabled state '$_cts_enabled'." >&2
            return 1
        fi
        case "$_cts_active" in active|inactive|failed) ;; *)
            printf '%s\n' "Error: $_cts_timer has unexpected active state '$_cts_active'." >&2
            return 1
        esac
    else
        printf '%s\n' "Error: $_cts_timer has unexpected load state '$_cts_timer_load'." >&2
        return 1
    fi
    case "$_cts_service_load" in loaded|not-found) ;; *)
        printf '%s\n' "Error: $_cts_service has unexpected load state '$_cts_service_load'." >&2
        return 1
    esac
    {
        printf '%s\n' "$_cts_timer_load"
        printf '%s\n' "$_cts_enabled"
        printf '%s\n' "$_cts_active"
        printf '%s\n' "$_cts_service_load"
    } > "$_cts_path"
}

read_captured_state() {
    _rcs_path="$1"
    {
        IFS= read -r CAPTURED_TIMER_LOAD
        IFS= read -r CAPTURED_TIMER_ENABLED
        IFS= read -r CAPTURED_TIMER_ACTIVE
        # shellcheck disable=SC2034  # exported to callers as captured state
        IFS= read -r CAPTURED_SERVICE_LOAD
    } < "$_rcs_path"
}

restore_timer_state() {
    _rts_target="$1"
    _rts_load="$2"
    _rts_enabled="$3"
    _rts_active="$4"
    _rts_timer="$(unit_name "$_rts_target" timer)"
    _rts_failed=0

    if [ "$_rts_load" = "not-found" ]; then
        return 0
    fi
    systemctl --user stop "$_rts_timer" >/dev/null || _rts_failed=1
    systemctl --user disable "$_rts_timer" >/dev/null || _rts_failed=1
    systemctl --user reset-failed "$_rts_timer" >/dev/null || _rts_failed=1
    if [ "$_rts_enabled" = "enabled" ]; then
        systemctl --user enable "$_rts_timer" >/dev/null || _rts_failed=1
    fi
    if [ "$_rts_active" = "active" ]; then
        systemctl --user start "$_rts_timer" >/dev/null || _rts_failed=1
    fi

    _rts_now_enabled="$(systemd_property "$_rts_timer" UnitFileState)" || return 1
    _rts_now_active="$(systemd_property "$_rts_timer" ActiveState)" || return 1
    if [ "$_rts_enabled" = "enabled" ]; then
        if [ "$_rts_now_enabled" != "enabled" ]; then
            printf '%s\n' "Error: $_rts_timer enabled state was not restored." >&2
            _rts_failed=1
        fi
    elif ! timer_state_is_disabled "$_rts_now_enabled"; then
        printf '%s\n' "Error: $_rts_timer did not return to a disabled-like state." >&2
        _rts_failed=1
    fi
    if [ "$_rts_active" = "active" ]; then
        [ "$_rts_now_active" = "active" ] || _rts_failed=1
    else
        state_is_stopped "$_rts_now_active" || _rts_failed=1
    fi
    [ "$_rts_failed" -eq 0 ]
}

restore_captured_states() {
    _rcst_targets="$1"
    _rcst_state_dir="$2"
    _rcst_failed=0
    _rcst_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086  # intentional newline-only stream iteration
    for _rcst_target in $_rcst_targets; do
        read_captured_state "$_rcst_state_dir/$_rcst_target"
        if ! restore_timer_state "$_rcst_target" "$CAPTURED_TIMER_LOAD" \
            "$CAPTURED_TIMER_ENABLED" "$CAPTURED_TIMER_ACTIVE"; then
            _rcst_failed=1
        fi
    done
    IFS="$_rcst_old_ifs"
    [ "$_rcst_failed" -eq 0 ]
}

rollback_provisioning() {
    _rbp_targets="$1"
    _rbp_transaction="$2"
    _rbp_failed=0
    _rbp_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086  # intentional newline-only stream iteration
    for _rbp_target in $_rbp_targets; do
        disable_one "$_rbp_target" || _rbp_failed=1
        for _rbp_suffix in service timer; do
            _rbp_live="$SYSTEMD_USER_DIR/$(unit_name "$_rbp_target" "$_rbp_suffix")"
            _rbp_backup="$_rbp_transaction/backups/$(unit_name "$_rbp_target" "$_rbp_suffix")"
            _rbp_existed="$_rbp_transaction/existed/$(unit_name "$_rbp_target" "$_rbp_suffix")"
            if [ -f "$_rbp_existed" ]; then
                if [ -f "$_rbp_backup" ]; then
                    cp -p "$_rbp_backup" "$_rbp_live" || _rbp_failed=1
                else
                    printf '%s\n' \
                        "Error: Missing backup for previously-existing $(unit_name "$_rbp_target" "$_rbp_suffix")." >&2
                    _rbp_failed=1
                fi
            else
                rm -f "$_rbp_live" || _rbp_failed=1
            fi
        done
    done
    IFS="$_rbp_old_ifs"
    systemctl --user daemon-reload >/dev/null || _rbp_failed=1
    if [ "$_rbp_failed" -eq 0 ]; then
        restore_captured_states "$_rbp_targets" "$_rbp_transaction/state" || _rbp_failed=1
    fi
    [ "$_rbp_failed" -eq 0 ]
}

clear_provisioning_traps() {
    trap - HUP INT TERM
    PROVISION_TRANSACTION_ACTIVE=0
}

discard_provisioning_recovery() {
    # Once rollback is unnecessary or complete, prevent a signal handler from
    # trying to consume recovery data while it is being removed.
    trap '' HUP INT TERM
    PROVISION_TRANSACTION_ACTIVE=0
    [ -z "${PROVISION_RECOVERY_DIR:-}" ] || rm -rf "$PROVISION_RECOVERY_DIR"
    PROVISION_RECOVERY_DIR=''
    clear_provisioning_traps
}

provisioning_interrupted() {
    _pi_signal="$1"
    _pi_status="$2"
    trap '' HUP INT TERM
    printf '%s\n' "Provisioning interrupted by $_pi_signal." >&2
    if [ "${PROVISION_TRANSACTION_ACTIVE:-0}" -eq 1 ]; then
        if [ "${PROVISION_MUTATION_STARTED:-0}" -eq 1 ]; then
            printf '%s\n' "Restoring the previous unit files and timer states." >&2
            if rollback_provisioning "$PROVISION_TRANSACTION_TARGETS" \
                "$PROVISION_RECOVERY_DIR"; then
                rm -rf "$PROVISION_RECOVERY_DIR"
            else
                printf '%s\n' "Error: Interrupted rollback was incomplete. Recovery files:" >&2
                printf '%s\n' "$PROVISION_RECOVERY_DIR" >&2
            fi
        else
            rm -rf "$PROVISION_RECOVERY_DIR"
        fi
    fi
    exit "$_pi_status"
}

# provision_units_transaction <targets-stream> <schedule-stream> <normal|deferred>
#
# "normal" enables every selected timer. "deferred" commits files but leaves
# activation to update.sh, which restores the pre-update timer selection.
provision_units_transaction() {
    _put_targets="$1"
    _put_schedules="$2"
    _put_mode="$3"
    PROVISION_RECOVERY_DIR="$SYSTEMD_USER_DIR/.scrooge-provision.$$"
    PROVISION_TRANSACTION_TARGETS="$_put_targets"
    case "$_put_mode" in normal|deferred) ;; *) return 2 ;; esac
    [ -n "$_put_targets" ] || {
        printf '%s\n' "Error: No targets selected for provisioning." >&2
        return 1
    }
    [ ! -e "$PROVISION_RECOVERY_DIR" ] || {
        printf '%s\n' "Error: Provisioning workspace already exists: $PROVISION_RECOVERY_DIR" >&2
        return 1
    }
    PROVISION_TRANSACTION_ACTIVE=1
    PROVISION_MUTATION_STARTED=0
    trap 'provisioning_interrupted HUP 129' HUP
    trap 'provisioning_interrupted INT 130' INT
    trap 'provisioning_interrupted TERM 143' TERM
    (
        umask 077
        mkdir "$PROVISION_RECOVERY_DIR" &&
            mkdir "$PROVISION_RECOVERY_DIR/staged" \
                "$PROVISION_RECOVERY_DIR/backups" "$PROVISION_RECOVERY_DIR/state" \
                "$PROVISION_RECOVERY_DIR/existed"
    ) || {
        discard_provisioning_recovery
        return 1
    }

    _put_failed=0
    _put_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086  # intentional newline-only stream iteration
    for _put_target in $_put_targets; do
        require_valid_target "$_put_target" || { _put_failed=1; break; }
        _put_calendar="$(plugin_stream_value "$_put_target" "$_put_schedules")" || {
            printf '%s\n' "Error: Target '$_put_target' has no resolved schedule." >&2
            _put_failed=1
            break
        }
        [ -n "$_put_calendar" ] || {
            printf '%s\n' "Error: Target '$_put_target' has no resolved schedule." >&2
            _put_failed=1
            break
        }
        for _put_suffix in service timer; do
            _put_name="$(unit_name "$_put_target" "$_put_suffix")"
            _put_live="$SYSTEMD_USER_DIR/$_put_name"
            if [ -e "$_put_live" ] || [ -L "$_put_live" ]; then
                : > "$PROVISION_RECOVERY_DIR/existed/$_put_name"
            fi
        done
        _put_service="$PROVISION_RECOVERY_DIR/staged/$(unit_name "$_put_target" service)"
        _put_timer="$PROVISION_RECOVERY_DIR/staged/$(unit_name "$_put_target" timer)"
        if ! capture_timer_state "$_put_target" \
                "$PROVISION_RECOVERY_DIR/state/$_put_target" ||
           ! render_plugin_service "$_put_target" "$_put_service" ||
           ! render_plugin_timer "$_put_target" "$_put_calendar" "$_put_timer" ||
           ! validate_staged_pair "$_put_target" "$_put_calendar" \
                "$_put_service" "$_put_timer"; then
            printf '%s\n' "Error: Failed to stage valid systemd units for '$_put_target'." >&2
            _put_failed=1
            break
        fi
    done
    IFS="$_put_old_ifs"
    if [ "$_put_failed" -ne 0 ]; then
        discard_provisioning_recovery
        return 1
    fi

    _put_backup_failed=0
    IFS='
'
    # shellcheck disable=SC2086  # intentional newline-only stream iteration
    for _put_target in $_put_targets; do
        for _put_suffix in service timer; do
            _put_name="$(unit_name "$_put_target" "$_put_suffix")"
            _put_live="$SYSTEMD_USER_DIR/$_put_name"
            if [ -f "$PROVISION_RECOVERY_DIR/existed/$_put_name" ]; then
                if ! cp -p "$_put_live" "$PROVISION_RECOVERY_DIR/backups/$_put_name"; then
                    printf '%s\n' "Error: Failed to back up existing $_put_name." >&2
                    _put_backup_failed=1
                    break
                fi
            fi
        done
        [ "$_put_backup_failed" -eq 0 ] || break
    done
    IFS="$_put_old_ifs"

    if [ "$_put_backup_failed" -ne 0 ]; then
        printf '%s\n' "Provisioning stopped before any live unit file was changed." >&2
        discard_provisioning_recovery
        return 1
    fi

    if [ "$_put_failed" -eq 0 ]; then
        PROVISION_MUTATION_STARTED=1
        IFS='
'
        # shellcheck disable=SC2086  # intentional newline-only stream iteration
        for _put_target in $_put_targets; do
            for _put_suffix in service timer; do
                _put_name="$(unit_name "$_put_target" "$_put_suffix")"
                if ! mv "$PROVISION_RECOVERY_DIR/staged/$_put_name" \
                    "$SYSTEMD_USER_DIR/$_put_name"; then
                    _put_failed=1
                    break
                fi
            done
            [ "$_put_failed" -eq 0 ] || break
        done
        IFS="$_put_old_ifs"
    fi
    if [ "$_put_failed" -eq 0 ] && ! systemctl --user daemon-reload; then
        printf '%s\n' "Error: Failed to reload the systemd user manager." >&2
        _put_failed=1
    fi

    if [ "$_put_failed" -eq 0 ] && [ "$_put_mode" = "normal" ]; then
        IFS='
'
        # shellcheck disable=SC2086  # intentional newline-only stream iteration
        for _put_target in $_put_targets; do
            if ! enable_one "$_put_target"; then
                printf '%s\n' "Error: Failed to enable the timer for '$_put_target'." >&2
                _put_failed=1
                break
            fi
        done
        IFS="$_put_old_ifs"
    fi

    if [ "$_put_failed" -ne 0 ]; then
        printf '%s\n' "Provisioning failed; restoring the previous unit files and timer states." >&2
        if rollback_provisioning "$_put_targets" "$PROVISION_RECOVERY_DIR"; then
            discard_provisioning_recovery
        else
            printf '%s\n' \
                "Error: Rollback was incomplete. Recovery files were retained at:" >&2
            printf '%s\n' "$PROVISION_RECOVERY_DIR" >&2
            clear_provisioning_traps
        fi
        return 1
    fi

    discard_provisioning_recovery
    return 0
}
