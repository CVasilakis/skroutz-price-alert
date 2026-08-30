#!/bin/sh
# Atomic systemd unit replacement and reusable snapshot/restore primitives.
# Source common.sh and systemd.sh first.

unit_file_has_line() {
    while IFS= read -r _ufhl_line; do
        [ "$_ufhl_line" = "$2" ] && return 0
    done < "$1"
    return 1
}

validate_staged_units() {
    _vsu_target="$1"
    _vsu_calendar="$2"
    _vsu_scope="$3"
    _vsu_staged="$4"
    _vsu_timer="$_vsu_staged/$(unit_name "$_vsu_target" timer)"
    [ -f "$_vsu_timer" ] && [ ! -L "$_vsu_timer" ] &&
        unit_file_has_line "$_vsu_timer" "OnCalendar=$_vsu_calendar" &&
        unit_file_has_line "$_vsu_timer" \
            "Unit=$(unit_name "$_vsu_target" service)" &&
        unit_file_has_line "$_vsu_timer" "RandomizedDelaySec=180s" &&
        unit_file_has_line "$_vsu_timer" "Persistent=true" || return 1
    [ "$_vsu_scope" = timer ] && return 0
    _vsu_service="$_vsu_staged/$(unit_name "$_vsu_target" service)"
    [ -f "$_vsu_service" ] && [ ! -L "$_vsu_service" ] &&
        unit_file_has_line "$_vsu_service" "Type=oneshot" &&
        unit_file_has_line "$_vsu_service" "WorkingDirectory=$BASE_DIR" &&
        unit_file_has_line "$_vsu_service" \
            "ExecStart=\"$BASE_DIR/scripts/run.sh\" --quiet --$_vsu_target"
}

# Record one target's timer/service load, enabled, and active states, rejecting
# any value the rollback path could not reassert. A timer that is not installed
# yet is recorded as the 'absent' sentinel, which is deliberately not a systemd
# UnitFileState/ActiveState value: it can never compare equal to a live state,
# so a caller that reasserts captured values cannot mistake "was not there" for
# "was disabled". restore_timer_state treats it as "leave this target alone".
capture_timer_state() {
    _cts_target="$1"
    _cts_path="$2"
    _cts_timer="$(unit_name "$_cts_target" timer)"
    _cts_service="$(unit_name "$_cts_target" service)"
    _cts_timer_load="$(systemd_property "$_cts_timer" LoadState)" || return 1
    _cts_service_load="$(systemd_property "$_cts_service" LoadState)" || return 1
    if [ "$_cts_timer_load" = not-found ]; then
        _cts_enabled=absent
        _cts_active=absent
    elif [ "$_cts_timer_load" = loaded ]; then
        _cts_enabled="$(systemd_property "$_cts_timer" UnitFileState)" || return 1
        _cts_active="$(systemd_property "$_cts_timer" ActiveState)" || return 1
        if ! timer_state_is_enabled "$_cts_enabled" &&
            ! timer_state_is_disabled "$_cts_enabled"; then
            printf '%s\n' \
                "Error: $_cts_timer has unexpected state '$_cts_enabled'." >&2
            return 1
        fi
        case "$_cts_active" in active|inactive|failed) ;; *)
            printf '%s\n' \
                "Error: $_cts_timer has unexpected active state '$_cts_active'." >&2
            return 1 ;;
        esac
    else
        printf '%s\n' \
            "Error: $_cts_timer has unexpected load state '$_cts_timer_load'." >&2
        return 1
    fi
    case "$_cts_service_load" in loaded|not-found) ;; *)
        printf '%s\n' \
            "Error: $_cts_service has unexpected load state '$_cts_service_load'." >&2
        return 1 ;;
    esac
    {
        printf '%s\n' "$_cts_timer_load"
        printf '%s\n' "$_cts_enabled"
        printf '%s\n' "$_cts_active"
        printf '%s\n' "$_cts_service_load"
    } > "$_cts_path"
}

read_captured_state() {
    {
        IFS= read -r CAPTURED_TIMER_LOAD
        IFS= read -r CAPTURED_TIMER_ENABLED
        IFS= read -r CAPTURED_TIMER_ACTIVE
        # shellcheck disable=SC2034  # exported for update.sh repair decisions
        IFS= read -r CAPTURED_SERVICE_LOAD
    } < "$1"
}

restore_timer_state() {
    _rts_target="$1"
    _rts_load="$2"
    _rts_enabled="$3"
    _rts_active="$4"
    _rts_timer="$(unit_name "$_rts_target" timer)"
    _rts_failed=0
    [ "$_rts_load" != not-found ] || return 0

    reset_failed_if_failed "$_rts_timer" || _rts_failed=1
    run_action systemctl --user stop "$_rts_timer" || _rts_failed=1
    case "$_rts_enabled" in
        enabled)
            run_action systemctl --user enable "$_rts_timer" || _rts_failed=1 ;;
        enabled-runtime)
            run_action systemctl --user --runtime enable "$_rts_timer" ||
                _rts_failed=1 ;;
    esac
    [ "$_rts_active" != active ] ||
        run_action systemctl --user start "$_rts_timer" || _rts_failed=1

    _rts_now_enabled="$(systemd_property "$_rts_timer" UnitFileState)" ||
        return 1
    _rts_now_active="$(systemd_property "$_rts_timer" ActiveState)" || return 1
    [ "$_rts_now_enabled" = "$_rts_enabled" ] || _rts_failed=1
    if [ "$_rts_active" = active ]; then
        [ "$_rts_now_active" = active ] || _rts_failed=1
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
    # shellcheck disable=SC2086
    for _rcst_target in $_rcst_targets; do
        read_captured_state "$_rcst_state_dir/$_rcst_target"
        restore_timer_state "$_rcst_target" "$CAPTURED_TIMER_LOAD" \
            "$CAPTURED_TIMER_ENABLED" "$CAPTURED_TIMER_ACTIVE" ||
            _rcst_failed=1
    done
    IFS="$_rcst_old_ifs"
    [ "$_rcst_failed" -eq 0 ]
}

initialize_unit_snapshot() {
    _ius_workspace="$1"
    (
        umask 077
        mkdir "$_ius_workspace/backups" "$_ius_workspace/existed" \
            "$_ius_workspace/state"
    )
}

# capture_unit_snapshot <targets> <pair|timer> <workspace>
# Destinations must already have passed validate_unit_destinations.
capture_unit_snapshot() {
    _cus_targets="$1"
    _cus_scope="$2"
    _cus_workspace="$3"
    _cus_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _cus_target in $_cus_targets; do
        capture_timer_state "$_cus_target" \
            "$_cus_workspace/state/$_cus_target" || {
            IFS="$_cus_old_ifs"
            return 1
        }
        for _cus_suffix in timer service; do
            [ "$_cus_scope" = pair ] || [ "$_cus_suffix" = timer ] || continue
            _cus_name="$(unit_name "$_cus_target" "$_cus_suffix")"
            _cus_live="$SYSTEMD_USER_DIR/$_cus_name"
            if [ -f "$_cus_live" ] && [ ! -L "$_cus_live" ]; then
                : > "$_cus_workspace/existed/$_cus_name"
                if ! cp -p "$_cus_live" \
                        "$_cus_workspace/backups/$_cus_name" ||
                    ! cmp -s "$_cus_live" \
                        "$_cus_workspace/backups/$_cus_name"; then
                    IFS="$_cus_old_ifs"
                    return 1
                fi
            fi
        done
    done
    IFS="$_cus_old_ifs"
}

restore_regular_file() {
    _rrf_backup="$1"
    _rrf_live="$2"
    _rrf_tmp="$_rrf_live.restore.$$"
    [ ! -e "$_rrf_tmp" ] && [ ! -L "$_rrf_tmp" ] || return 1
    if ! cp -p "$_rrf_backup" "$_rrf_tmp" ||
        ! cmp -s "$_rrf_backup" "$_rrf_tmp" ||
        ! mv "$_rrf_tmp" "$_rrf_live" ||
        [ ! -f "$_rrf_live" ] || [ -L "$_rrf_live" ] ||
        ! cmp -s "$_rrf_backup" "$_rrf_live"; then
        rm -f "$_rrf_tmp"
        return 1
    fi
}

# restore_unit_snapshot <targets> <pair|timer> <workspace>
restore_unit_snapshot() {
    _rus_targets="$1"
    _rus_scope="$2"
    _rus_workspace="$3"
    _rus_failed=0
    _rus_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _rus_target in $_rus_targets; do
        disable_one "$_rus_target" || _rus_failed=1
        for _rus_suffix in timer service; do
            [ "$_rus_scope" = pair ] || [ "$_rus_suffix" = timer ] || continue
            _rus_name="$(unit_name "$_rus_target" "$_rus_suffix")"
            _rus_live="$SYSTEMD_USER_DIR/$_rus_name"
            if [ -f "$_rus_workspace/existed/$_rus_name" ]; then
                restore_regular_file \
                    "$_rus_workspace/backups/$_rus_name" "$_rus_live" ||
                    _rus_failed=1
            else
                rm -f "$_rus_live" || _rus_failed=1
                path_entry_exists "$_rus_live" && _rus_failed=1
            fi
        done
    done
    IFS="$_rus_old_ifs"
    run_action systemctl --user daemon-reload || _rus_failed=1
    if [ "$_rus_failed" -eq 0 ]; then
        restore_captured_states "$_rus_targets" "$_rus_workspace/state" ||
            _rus_failed=1
    fi
    [ "$_rus_failed" -eq 0 ]
}

clear_unit_transaction_traps() {
    trap - HUP INT TERM
    UNIT_TRANSACTION_ACTIVE=0
}

discard_unit_recovery() {
    trap '' HUP INT TERM
    UNIT_TRANSACTION_ACTIVE=0
    [ -z "${UNIT_RECOVERY_DIR:-}" ] || rm -rf "$UNIT_RECOVERY_DIR"
    UNIT_RECOVERY_DIR=''
    clear_unit_transaction_traps
}

# shellcheck disable=SC2317,SC2329  # invoked by traps
unit_transaction_interrupted() {
    _uti_signal="$1"
    _uti_status="$2"
    trap '' HUP INT TERM
    printf '%s\n' "Unit replacement interrupted by $_uti_signal." >&2
    if [ "${UNIT_TRANSACTION_ACTIVE:-0}" -eq 1 ]; then
        if [ "${UNIT_MUTATION_STARTED:-0}" -eq 1 ]; then
            printf '%s\n' "Restoring the previous unit files and timer states." >&2
            if restore_unit_snapshot "$UNIT_TRANSACTION_TARGETS" \
                "$UNIT_TRANSACTION_SCOPE" "$UNIT_RECOVERY_DIR"; then
                rm -rf "$UNIT_RECOVERY_DIR"
            else
                printf '%s\n' \
                    "Error: Interrupted rollback was incomplete. Recovery files:" >&2
                printf '%s\n' "$UNIT_RECOVERY_DIR" >&2
            fi
        else
            rm -rf "$UNIT_RECOVERY_DIR"
        fi
    fi
    exit "$_uti_status"
}

# Unit replacement is one transaction with six ordered phases. The order is not
# stylistic: each phase exists to keep the previous ones recoverable.
#
#   validate   Argument shape and every live destination path, before anything
#              exists to clean up. An illegal scope/activation pair returns 2 to
#              mark a caller error, distinct from the 1 every runtime failure
#              below returns.
#   workspace  One private mktemp directory, created inside SYSTEMD_USER_DIR so
#              the later staged -> live move is a rename within one filesystem
#              rather than a copy that can half-succeed. The signal traps are
#              installed only after every variable the handler reads is set.
#   stage      Render the new unit files under staged/ and re-read them to
#              confirm the load-bearing directives. A bad render costs nothing:
#              no live file has been touched and no snapshot exists yet.
#   capture    The last read-only phase. Back up the live unit files
#              byte-for-byte and record each timer's load/enabled/active state.
#              It sits immediately before the mutation so the states a rollback
#              reasserts are the freshest ones observed, and so an unexpected
#              systemd state is still fatal while aborting is still free.
#   mutate     Move the staged files over the live ones, then daemon-reload.
#   activate   Apply the activation mode described below.
#
# UNIT_MUTATION_STARTED is the rollback pivot, and it is set to 1 *before* the
# first move, never after. A signal arriving between the flag and the first move
# makes the handler restore a system that was not modified, which is harmless
# because restoring is idempotent; the opposite order would leave a moved unit
# file in place while the handler still believed nothing had changed. The same
# flag selects the failure path: before it, discard the workspace and report
# that no live file changed; after it, restore the files, daemon-reload, and
# only then reassert the captured timer states, in that order, because a state
# cannot be reasserted against a unit file that is not back yet.
#
# Two variables outlive the return as the caller-facing outcome contract, and
# install.sh and schedule.sh both branch on them. UNIT_RECOVERY_DIR is cleared
# whenever the workspace was removed -- success, pre-mutation abort, or a
# completed rollback -- and is left set, with the directory still on disk, only
# when a rollback was incomplete and a human has to inspect it. Alongside it,
# UNIT_MUTATION_STARTED is never cleared, so a caller can still tell "previous
# files and states were restored" from "nothing live was touched". Both are
# reset by the next call, but only after its validate and workspace phases,
# which is why this helper is invoked at most once per process.

# replace_units_transaction <targets> <schedules> <pair|timer>
#                           <normal|deferred|preserve>
#
# scope selects which files are staged, snapshotted, and rolled back:
#   pair    the service and the timer, i.e. installing or reinstalling a target
#   timer   the timer alone, leaving the installed service bytes untouched,
#           i.e. changing a target's cadence
#
# activation selects what happens to the timer once the files are in place:
#   normal    enable --now. The only mode that turns a target on, used by a
#             direct ./scrooge-alert install.
#   deferred  do nothing; the caller owns activation. Used only by the
#             install.sh run that ./scrooge-alert update invokes with
#             SCROOGE_INSTALL_CONTEXT=deferred. update.sh stopped and disabled
#             every installed target before the fast-forward, and holds its own
#             snapshot of the states from *before* that quiescence, so update.sh
#             restores them in its own activation phase. This transaction's
#             capture necessarily runs after that quiescence and so records the
#             quiesced state: correct for rolling back this transaction alone,
#             and precisely why activation cannot be left to it here.
#   preserve  reassert what capture recorded: restart a timer that was active,
#             otherwise confirm it stayed stopped, and confirm its enabled state
#             is unchanged either way. Used by ./scrooge-alert schedule.
#
# Only pair:normal, pair:deferred, and timer:preserve are legal. The other three
# are rejected because they are unsound, not merely unused:
#   pair:preserve   a pair write is how a target gets installed, so its timer
#                   may not exist yet; capture then records the 'absent'
#                   sentinel, which no live UnitFileState can equal, and
#                   preserve's verification would fail every fresh install.
#   timer:normal    a cadence change must not turn on a target the user turned
#                   off; enable --now would silently enable and start every
#                   rescheduled timer.
#   timer:deferred  nothing runs after a cadence change to activate the timer,
#                   so deferring would leave the user's scrapers stopped with no
#                   later phase to restore them.
replace_units_transaction() {
    _rut_targets="$1"
    _rut_schedules="$2"
    _rut_scope="$3"
    _rut_activation="$4"
    case "$_rut_scope:$_rut_activation" in
        pair:normal|pair:deferred|timer:preserve) ;;
        *) return 2 ;;
    esac
    [ -n "$_rut_targets" ] || return 0

    # All target names and destinations are checked before workspace creation,
    # state capture, or any live mutation.
    validate_unit_destinations "$_rut_targets" "$_rut_scope" || return 1
    UNIT_RECOVERY_DIR="$(create_private_workspace units)" || return 1
    UNIT_TRANSACTION_TARGETS="$_rut_targets"
    UNIT_TRANSACTION_SCOPE="$_rut_scope"
    UNIT_TRANSACTION_ACTIVE=1
    UNIT_MUTATION_STARTED=0
    trap 'unit_transaction_interrupted HUP 129' HUP
    trap 'unit_transaction_interrupted INT 130' INT
    trap 'unit_transaction_interrupted TERM 143' TERM
    if ! initialize_unit_snapshot "$UNIT_RECOVERY_DIR" ||
        ! mkdir "$UNIT_RECOVERY_DIR/staged"; then
        discard_unit_recovery
        return 1
    fi

    _rut_failed=0
    _rut_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for _rut_target in $_rut_targets; do
        _rut_calendar="$(
            plugin_stream_value "$_rut_target" "$_rut_schedules"
        )" || {
            printf '%s\n' \
                "Error: Target '$_rut_target' has no resolved schedule." >&2
            _rut_failed=1
            break
        }
        if [ "$_rut_scope" = pair ]; then
            render_plugin_service "$_rut_target" \
                "$UNIT_RECOVERY_DIR/staged/$(unit_name "$_rut_target" service)" ||
                _rut_failed=1
        fi
        render_plugin_timer "$_rut_target" "$_rut_calendar" \
            "$UNIT_RECOVERY_DIR/staged/$(unit_name "$_rut_target" timer)" ||
            _rut_failed=1
        validate_staged_units "$_rut_target" "$_rut_calendar" "$_rut_scope" \
            "$UNIT_RECOVERY_DIR/staged" || _rut_failed=1
        [ "$_rut_failed" -eq 0 ] || break
    done
    IFS="$_rut_old_ifs"
    if [ "$_rut_failed" -eq 0 ]; then
        capture_unit_snapshot "$_rut_targets" "$_rut_scope" \
            "$UNIT_RECOVERY_DIR" || _rut_failed=1
    fi
    if [ "$_rut_failed" -ne 0 ]; then
        printf '%s\n' \
            "Unit replacement stopped before any live file was changed." >&2
        discard_unit_recovery
        return 1
    fi

    UNIT_MUTATION_STARTED=1
    IFS='
'
    # shellcheck disable=SC2086
    for _rut_target in $_rut_targets; do
        for _rut_suffix in timer service; do
            [ "$_rut_scope" = pair ] || [ "$_rut_suffix" = timer ] || continue
            _rut_name="$(unit_name "$_rut_target" "$_rut_suffix")"
            mv "$UNIT_RECOVERY_DIR/staged/$_rut_name" \
                "$SYSTEMD_USER_DIR/$_rut_name" || {
                _rut_failed=1
                break
            }
        done
        [ "$_rut_failed" -eq 0 ] || break
    done
    IFS="$_rut_old_ifs"
    if [ "$_rut_failed" -eq 0 ]; then
        run_action systemctl --user daemon-reload || _rut_failed=1
    fi

    if [ "$_rut_failed" -eq 0 ]; then
        IFS='
'
        # shellcheck disable=SC2086
        for _rut_target in $_rut_targets; do
            case "$_rut_activation" in
                normal) enable_one "$_rut_target" || _rut_failed=1 ;;
                deferred) ;;
                preserve)
                    read_captured_state \
                        "$UNIT_RECOVERY_DIR/state/$_rut_target"
                    if [ "$CAPTURED_TIMER_ACTIVE" = active ]; then
                        restart_timer_one "$_rut_target" || _rut_failed=1
                    else
                        _rut_active="$(timer_is_active "$_rut_target")" ||
                            _rut_failed=1
                        [ "$_rut_failed" -ne 0 ] ||
                            state_is_stopped "$_rut_active" ||
                            _rut_failed=1
                    fi
                    _rut_enabled="$(timer_is_enabled "$_rut_target")" ||
                        _rut_failed=1
                    [ "$_rut_failed" -ne 0 ] ||
                        [ "$_rut_enabled" = "$CAPTURED_TIMER_ENABLED" ] ||
                        _rut_failed=1
                    ;;
            esac
            [ "$_rut_failed" -eq 0 ] || break
        done
        IFS="$_rut_old_ifs"
    fi

    if [ "$_rut_failed" -ne 0 ]; then
        printf '%s\n' \
            "Error: Unit replacement failed; restoring previous files and states." >&2
        if restore_unit_snapshot "$_rut_targets" "$_rut_scope" \
            "$UNIT_RECOVERY_DIR"; then
            discard_unit_recovery
        else
            printf '%s\n' \
                "Error: Rollback was incomplete. Recovery files were retained at:" >&2
            printf '%s\n' "$UNIT_RECOVERY_DIR" >&2
            clear_unit_transaction_traps
        fi
        return 1
    fi
    discard_unit_recovery
}

# Install or reinstall a target: both unit files, with the caller choosing
# between normal and deferred activation.
provision_units_transaction() {
    replace_units_transaction "$1" "$2" pair "$3"
}

# Change an installed target's cadence: the timer file only, with its previous
# enabled and active state reasserted afterwards.
schedule_units_transaction() {
    replace_units_transaction "$1" "$2" timer preserve
}
