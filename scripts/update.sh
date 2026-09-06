#!/bin/sh
# Fast-forward the checkout to origin/main and reprovision what is installed.
#
# Selection policy: none of the shared ones. This is the only lifecycle entry
# point that does not call select_targets -- it takes exactly the set
# list_installed_targets reads off disk, accepts no --<target> flags, and
# refuses when that set is empty, because an update preserves an existing
# installation rather than choosing a new one. Registration is not required to
# be selected: a target whose plugin disappeared upstream is still quiesced and
# still reported, just left disabled instead of reactivated.
#
# The interrupt and recovery model is documented at update_interrupted, and the
# handoff to install.sh at the SCROOGE_INSTALL_CONTEXT=deferred call site.

set -eu

# Keep the entire update in one function: the shell reads these function bodies
# before git replaces files in the checkout.
main() {
    SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
    BASE_DIR="$(dirname -- "$SCRIPT_DIR")"

    # shellcheck source=scripts/lib/common.sh
    . "$SCRIPT_DIR/lib/common.sh"
    # shellcheck source=scripts/lib/preflight.sh
    . "$SCRIPT_DIR/lib/preflight.sh"
    # shellcheck source=scripts/lib/systemd.sh
    . "$SCRIPT_DIR/lib/systemd.sh"
    # shellcheck source=scripts/lib/provisioning.sh
    . "$SCRIPT_DIR/lib/provisioning.sh"

    UPDATE_PHASE="safe"
    UPDATE_RECOVERY_DIR=''
    INSTALLED_TARGETS=''
    UPDATE_OUTPUT_STARTED=0
    UPDATE_SECTION_STARTED=0

    print_help() {
        printf '\n'
        if [ "${SCROOGE_PUBLIC_COMMAND:-}" = update ]; then
            printf '%s\n' "Usage: ./scrooge-alert update [--help] [--debug]"
        else
            printf '%s\n' "Usage: update.sh [-h|--help] [--debug]"
        fi
        printf '\n'
        printf '%s\n' "Safely update Scrooge Alert from origin/main and transactionally"
        printf '%s\n' "reprovision exactly the scraper targets that are already installed."
        printf '\n'
        help_options_block update
        help_debug_flag update
        printf '\n'
    }

    update_begin() {
        if [ "$UPDATE_OUTPUT_STARTED" -eq 0 ]; then
            begin_operational_output
            UPDATE_OUTPUT_STARTED=1
        fi
    }

    update_section() {
        _us_kind="$1"
        shift
        update_begin
        if [ "$UPDATE_SECTION_STARTED" -eq 1 ]; then
            printf '\n'
        fi
        section_heading "$_us_kind" "$@"
        UPDATE_SECTION_STARTED=1
    }

    update_finish() {
        [ "$UPDATE_OUTPUT_STARTED" -eq 0 ] || end_operational_output
    }

    update_exit() {
        _ue_status="$1"
        update_finish
        exit "$_ue_status"
    }

    # Quiet sourced operational helpers normally while preserving the update
    # shell's signal ownership when a nested command is interrupted.
    run_update_helper() {
        if [ "$DEBUG_MODE" -eq 1 ]; then
            "$@"
        else
            (
                trap 'kill -HUP "$$"; exit 129' HUP
                trap 'kill -INT "$$"; exit 130' INT
                trap 'kill -TERM "$$"; exit 143' TERM
                "$@" >/dev/null 2>&1
            )
        fi
    }

    # shellcheck disable=SC2329  # invoked indirectly through run_with_progress
    fetch_update_source() {
        if [ "$DEBUG_MODE" -eq 1 ]; then
            git -C "$BASE_DIR" fetch origin main
        else
            git -C "$BASE_DIR" fetch --quiet origin main >/dev/null 2>&1
        fi
    }

    # shellcheck disable=SC2329  # invoked indirectly through run_with_progress
    advance_update_source() {
        if [ "$DEBUG_MODE" -eq 1 ]; then
            git -C "$BASE_DIR" merge --ff-only origin/main
        else
            git -C "$BASE_DIR" merge --ff-only --quiet origin/main \
                >/dev/null 2>&1
        fi
    }

    # The two loaders below are the only helpers here called directly rather
    # than through run_update_helper. They are built on run_captured, which is
    # already quiet outside debug mode and mirrors both streams inside it, so
    # the wrapper would add nothing but its subshell -- and that subshell
    # discards the PLUGIN_* cache these exist to refresh, leaving the accessors
    # below to re-run the catalog CLI ungated. Their pipelines end in awk and so
    # always exit 0, which would turn a post-update discovery failure into a
    # silently empty target list on a run that still reports success.
    update_load_catalog() {
        if run_captured catalog_cli catalog; then
            PLUGIN_CATALOG_DATA="$CAPTURED_COMMAND_OUTPUT"
            PLUGIN_CATALOG_STATE=1
            return 0
        fi
        PLUGIN_CATALOG_DATA=''
        PLUGIN_CATALOG_STATE=2
        return 1
    }

    update_load_schedules() {
        if run_captured catalog_cli schedules --config-dir "$BASE_DIR/config"; then
            PLUGIN_SCHEDULE_DATA="$CAPTURED_COMMAND_OUTPUT"
            PLUGIN_SCHEDULE_STATE=1
            return 0
        fi
        PLUGIN_SCHEDULE_DATA=''
        PLUGIN_SCHEDULE_STATE=2
        return 1
    }

    # shellcheck disable=SC2329  # reached from the signal trap handler
    disable_update_targets() {
        UPDATE_DISABLE_FAILED=0
        _dut_old_ifs="$IFS"
        IFS='
'
        for _dut_target in $INSTALLED_TARGETS; do
            run_update_helper disable_one "$_dut_target" ||
                UPDATE_DISABLE_FAILED=1
        done
        IFS="$_dut_old_ifs"
    }

    restore_update_snapshot() {
        run_update_helper restore_unit_snapshot \
            "$INSTALLED_TARGETS" pair "$UPDATE_RECOVERY_DIR"
    }

    # UPDATE_PHASE names how much of the update must be undone if a signal
    # arrives. Each value maps to exactly one recovery strategy below; there is
    # no default arm because "no phase has advanced yet" and "nothing to undo"
    # are the same state.
    #
    #   safe       Nothing exists to undo: argument parsing, the read-only
    #              checks, the fetch, and the fast-forward verification. No
    #              workspace exists and no target has been stopped.
    #   workspace  The private recovery workspace exists but no target has been
    #              stopped. Remove it; the system is untouched. Set immediately
    #              after the mktemp path is captured, never after the snapshot
    #              completes, so the directory cannot outlive the run.
    #   quiescing  Targets are being or have been stopped, but the checkout is
    #              unchanged. Restore the captured unit files and timer states,
    #              then remove the workspace; retain it if the restore failed.
    #   mutating   The fast-forward, the migration, the reprovision, or the
    #              activation is in flight. The checkout may already be new, so
    #              the captured states can no longer be trusted: leave every
    #              target disabled, retain the workspace, and point at status.
    #
    # The quiescing -> mutating pivot is set *before* the fast-forward, never
    # after, for the same reason provisioning.sh sets UNIT_MUTATION_STARTED
    # before its first move: treating an unmodified checkout as modified only
    # costs a disabled timer, while the opposite order would restore timers
    # against source that has already changed underneath them.
    #
    # The recovery helpers below still run through run_update_helper, whose
    # subshell re-arms these signals, so a second interrupt during a rollback
    # aborts that rollback. That is deliberate: the operator asked twice, and
    # the retained-workspace warning reports the incomplete result honestly.
    # shellcheck disable=SC2317,SC2329  # invoked indirectly by HUP/INT/TERM traps
    update_interrupted() {
        _ui_signal="$1"
        _ui_status="$2"
        trap '' HUP INT TERM
        update_section success "Update interruption"
        task_status failure "Update interrupted by $_ui_signal."
        case "$UPDATE_PHASE" in
            workspace)
                [ -z "$UPDATE_RECOVERY_DIR" ] ||
                    run_action rm -rf "$UPDATE_RECOVERY_DIR"
                ;;
            quiescing)
                if restore_update_snapshot; then
                    run_action rm -rf "$UPDATE_RECOVERY_DIR"
                    task_status success "The original timer states were restored."
                else
                    task_status failure "Timer-state restoration was incomplete."
                    task_status warning "Recovery data was retained at $UPDATE_RECOVERY_DIR."
                fi
                ;;
            mutating)
                disable_update_targets
                if [ "$UPDATE_DISABLE_FAILED" -eq 0 ]; then
                    task_status warning "Affected timers were left disabled for safety."
                else
                    task_status failure \
                        "Some affected timer states could not be verified as disabled."
                fi
                task_status warning "Recovery data was retained at $UPDATE_RECOVERY_DIR."
                task_status warning \
                    "Rerun $(command_text './scrooge-alert update') or inspect $(command_text './scrooge-alert status')."
                ;;
        esac
        update_exit "$_ui_status"
    }

    # Public --debug is removed before the historical no-argument/help rules are
    # applied. Inherited internal debug remains active across self-replacement.
    DEBUG_ARGUMENTS=0
    NONDEBUG_ARGUMENTS=0
    NONDEBUG_ARGUMENT=''
    for argument in "$@"; do
        case "$argument" in
            --debug)
                DEBUG_ARGUMENTS=$((DEBUG_ARGUMENTS + 1))
                DEBUG_MODE=1
                SCROOGE_INTERNAL_DEBUG=1
                export DEBUG_MODE SCROOGE_INTERNAL_DEBUG
                ;;
            *)
                NONDEBUG_ARGUMENTS=$((NONDEBUG_ARGUMENTS + 1))
                [ "$NONDEBUG_ARGUMENTS" -ne 1 ] ||
                    NONDEBUG_ARGUMENT="$argument"
                ;;
        esac
    done

    if [ "$DEBUG_ARGUMENTS" -gt 1 ]; then
        update_section success "Update arguments"
        task_status failure "The --debug flag may be specified only once."
        task_status warning "Run $(command_text './scrooge-alert update --help') for usage."
        update_exit 1
    fi
    if [ "$NONDEBUG_ARGUMENTS" -gt 1 ]; then
        update_section success "Update arguments"
        task_status failure \
            "$(command_text './scrooge-alert update') accepts no arguments other than one -h or --help, plus --debug."
        task_status warning "Run $(command_text './scrooge-alert update --help') for usage."
        update_exit 1
    fi
    if [ "$NONDEBUG_ARGUMENTS" -eq 1 ]; then
        case "$NONDEBUG_ARGUMENT" in
            -h|--help) print_help; exit 0 ;;
            *)
                update_section success "Update arguments"
                task_status failure "Invalid argument: $NONDEBUG_ARGUMENT"
                task_status warning "Run $(command_text './scrooge-alert update --help') for usage."
                update_exit 1
                ;;
        esac
    fi

    trap 'update_interrupted HUP 129' HUP
    trap 'update_interrupted INT 130' INT
    trap 'update_interrupted TERM 143' TERM

    update_section success "Update checks"
    if ! run_update_helper reject_project_venv_symlink; then
        task_status failure "The project venv path is a symlink."
        task_status warning \
            "Remove the venv symlink, then recreate it with ./scripts/dev/setup.sh or $(command_text './scrooge-alert install')."
        update_exit 1
    fi
    if ! run_update_helper require_systemctl; then
        task_status failure "Systemd user services are not available."
        task_status warning "Install or enable systemd user services, then retry."
        update_exit 1
    fi
    if ! run_update_helper require_git_worktree; then
        task_status failure "The project directory is not a usable Git worktree."
        task_status warning "Restore the Git checkout, then retry."
        update_exit 1
    fi
    if ! run_update_helper require_main_branch; then
        task_status failure "The checkout is not on branch 'main'."
        task_status warning "Switch to main after saving any work, then retry."
        update_exit 1
    fi
    if ! run_update_helper require_clean_worktree; then
        task_status failure \
            "The working tree contains tracked changes or nonignored untracked files."
        task_status warning "Commit or stash your work before running $(command_text './scrooge-alert update')."
        update_exit 1
    fi
    if ! run_update_helper require_origin_remote; then
        task_status failure "Git remote 'origin' is missing or unusable."
        task_status warning "Restore the origin remote, then retry."
        update_exit 1
    fi
    if run_captured list_installed_targets; then
        INSTALLED_TARGETS="$CAPTURED_COMMAND_OUTPUT"
    else
        task_status failure "Installed target units could not be inspected."
        task_status warning "Inspect with $(command_text './scrooge-alert status'), then retry."
        update_exit 1
    fi
    if [ -z "$INSTALLED_TARGETS" ]; then
        task_status failure "No installed target timer or service units were found."
        task_status warning "Choose targets explicitly with $(command_text './scrooge-alert install --<target>')."
        update_exit 1
    fi
    # Updates only own absent or regular unit destinations. Reject links and
    # special entries before fetching or quiescing any target.
    if ! run_update_helper validate_unit_destinations \
        "$INSTALLED_TARGETS" pair; then
        task_status failure "A managed systemd unit destination is unsafe."
        task_status warning \
            "Remove the unsafe unit with $(command_text './scrooge-alert uninstall --<target>'), then retry."
        update_exit 1
    fi
    task_status success "The checkout and installed target selection are safe to update."

    update_section success "Source update"
    if run_with_progress "Fetching origin/main..." fetch_update_source; then
        FETCH_STATUS=0
    else
        FETCH_STATUS=$?
    fi
    if [ "$FETCH_STATUS" -ne 0 ]; then
        task_status failure "Failed to fetch origin/main."
        task_status info "Nothing was stopped or changed."
        update_exit 1
    fi
    task_status success "Fetched origin/main before stopping any target."

    # Close the race between the first inspection and the destructive phase.
    if ! run_update_helper require_main_branch ||
        ! run_update_helper require_clean_worktree ||
        ! run_update_helper require_origin_main ||
        ! run_update_helper require_fast_forward_to_origin ||
        ! run_update_helper require_revision_paths origin/main \
            scrooge-alert \
            scripts/install.sh \
            scripts/dev/migrate.sh \
            scripts/lib/common.sh \
            scripts/lib/preflight.sh \
            scripts/lib/systemd.sh \
            scripts/lib/provisioning.sh; then
        task_status failure "The fetched update failed safety validation."
        task_status warning \
            "Reconcile the checkout or fetched origin/main, then rerun $(command_text './scrooge-alert update --debug')."
        update_exit 1
    fi
    task_status success "Verified origin/main can safely fast-forward this checkout."

    update_section success "Target quiescence"
    if run_captured create_private_workspace update; then
        UPDATE_RECOVERY_DIR="$CAPTURED_COMMAND_OUTPUT"
        UPDATE_PHASE="workspace"
    else
        task_status failure \
            "Could not create private update state in $SYSTEMD_USER_DIR."
        task_status info "No target was stopped."
        update_exit 1
    fi
    if ! run_update_helper initialize_unit_snapshot "$UPDATE_RECOVERY_DIR"; then
        run_action rm -rf "$UPDATE_RECOVERY_DIR"
        task_status failure "Could not initialize private update recovery data."
        task_status info "No target was stopped."
        update_exit 1
    fi

    if ! run_update_helper capture_unit_snapshot "$INSTALLED_TARGETS" pair \
        "$UPDATE_RECOVERY_DIR"; then
        run_action rm -rf "$UPDATE_RECOVERY_DIR"
        task_status failure "Could not capture unit files and timer states."
        task_status info "Update aborted before any target was stopped."
        update_exit 1
    fi
    task_status success "Captured the installed unit files and timer states."

    QUIESCE_FAILED=0
    UPDATE_PHASE="quiescing"
    OLD_IFS="$IFS"
    IFS='
'
    for target in $INSTALLED_TARGETS; do
        if run_with_progress "[$target] Stopping and disabling its timer..." \
            run_update_helper disable_one "$target"; then
            task_status success "[$target] Safely stopped and disabled its timer."
        else
            task_status failure "[$target] Could not be safely quiesced."
            QUIESCE_FAILED=1
        fi
    done
    IFS="$OLD_IFS"
    if [ "$QUIESCE_FAILED" -ne 0 ]; then
        if restore_update_snapshot; then
            run_action rm -rf "$UPDATE_RECOVERY_DIR"
            task_status success "The original timer states were restored."
        else
            task_status warning \
                "Timer-state recovery data was retained at $UPDATE_RECOVERY_DIR."
        fi
        task_status failure "Update aborted before changing project files."
        update_exit 1
    fi

    UPDATE_PHASE="mutating"
    update_section success "Source advancement"
    if run_with_progress "Advancing the checkout with a verified fast-forward..." \
        advance_update_source; then
        ADVANCE_STATUS=0
    else
        ADVANCE_STATUS=$?
    fi
    if [ "$ADVANCE_STATUS" -ne 0 ]; then
        task_status failure "Failed to update project files."
        if restore_update_snapshot; then
            run_action rm -rf "$UPDATE_RECOVERY_DIR"
            task_status success "The prior timer states were restored."
        else
            task_status failure "Timer-state restoration was incomplete."
            task_status warning "Recovery data was retained at $UPDATE_RECOVERY_DIR."
        fi
        update_exit 1
    fi
    task_status success "Fast-forwarded the project files to origin/main."

    if [ ! -f "$SCRIPT_DIR/install.sh" ] || [ -L "$SCRIPT_DIR/install.sh" ]; then
        task_status failure "The fetched update has no safe scripts/install.sh."
        task_status warning "Target timers remain disabled."
        task_status warning "Repair the checkout, then rerun $(command_text './scrooge-alert update')."
        update_exit 1
    fi

    update_section success "JSON migration"
    # --machine is migrate.sh's internal mode: its stdout is exactly the report
    # parsed below and its exit status is the engine's own, so this update owns
    # the presentation and the recovery decisions rather than nesting another
    # script's panels inside its own.
    if run_with_progress "Migrating managed JSON documents..." \
        run_captured "$SCRIPT_DIR/dev/migrate.sh" --machine; then
        MIGRATION_STATUS=0
    else
        MIGRATION_STATUS=$?
    fi
    MIGRATION_REPORT="$CAPTURED_COMMAND_OUTPUT"
    case "$MIGRATION_STATUS" in
        0|"$EXIT_STATUS_TARGET_CONFIG_ERROR"|"$EXIT_STATUS_NOTIFICATION_CONFIG_ERROR"|"$EXIT_STATUS_STORAGE_ERROR") ;;
        *)
            task_status failure "JSON migration infrastructure failed."
            task_status warning "Affected timers remain disabled for safety."
            task_status warning "Retry with $(command_text './scrooge-alert update --debug')."
            update_exit "$MIGRATION_STATUS"
            ;;
    esac

    MIGRATION_FAILED_TARGETS=''
    MIGRATION_CONFIG_FAILED=0
    MIGRATION_GENERAL_FAILED=0
    MIGRATION_STATE_FAILED=0
    MIGRATION_RECOVERY_PATH=''
    MIGRATION_FAILURES=0
    MIGRATION_TAB="$(printf '\t')"
    OLD_IFS="$IFS"
    IFS='
'
    # Migration report columns, as the parsing below addresses them. The producer
    # (core.tooling.migration_cli) owns the contract:
    #
    #   $1 family  $2 target  $3 result  $4 path  $5 detail
    #
    # Only failed rows and the recovery row matter here, and $1 decides the blast
    # radius of a failure: target_config and scraper_state leave just their own target ($2)
    # disabled, general_config leaves every timer disabled because no target can
    # be trusted without it, and reminder_state only downgrades the final status.
    # The trailing recovery row carries the retained directory in $4. Any other
    # result needs no action, so the rows are filtered rather than enumerated.
    for migration_row in $MIGRATION_REPORT; do
        migration_family="${migration_row%%"$MIGRATION_TAB"*}"
        migration_rest="${migration_row#*"$MIGRATION_TAB"}"
        migration_target="${migration_rest%%"$MIGRATION_TAB"*}"
        migration_rest="${migration_rest#*"$MIGRATION_TAB"}"
        migration_result="${migration_rest%%"$MIGRATION_TAB"*}"
        migration_rest="${migration_rest#*"$MIGRATION_TAB"}"
        migration_path="${migration_rest%%"$MIGRATION_TAB"*}"
        migration_detail="${migration_rest#*"$MIGRATION_TAB"}"
        if [ "$migration_family" = recovery ] &&
           [ "$migration_result" = retained ]; then
            MIGRATION_RECOVERY_PATH="$migration_path"
            continue
        fi
        [ "$migration_result" = failed ] || continue
        MIGRATION_FAILURES=$((MIGRATION_FAILURES + 1))
        task_status failure \
            "[$migration_path] Migration failed: $migration_detail"
        case "$migration_family" in
            target_config)
                MIGRATION_CONFIG_FAILED=1
                MIGRATION_FAILED_TARGETS="$(
                    stream_add_unique "$MIGRATION_FAILED_TARGETS" "$migration_target"
                )"
                ;;
            scraper_state)
                MIGRATION_STATE_FAILED=1
                MIGRATION_FAILED_TARGETS="$(
                    stream_add_unique "$MIGRATION_FAILED_TARGETS" "$migration_target"
                )"
                ;;
            general_config) MIGRATION_GENERAL_FAILED=1 ;;
            reminder_state) MIGRATION_STATE_FAILED=1 ;;
        esac
    done
    IFS="$OLD_IFS"
    if [ "$MIGRATION_STATUS" -ne 0 ] && [ "$MIGRATION_FAILURES" -eq 0 ]; then
        task_status failure "JSON migration infrastructure failed."
        task_status warning "Affected timers remain disabled for safety."
        task_status warning "Retry with $(command_text './scrooge-alert update --debug')."
        update_exit "$MIGRATION_STATUS"
    fi
    if [ "$MIGRATION_FAILURES" -eq 0 ]; then
        task_status success "Managed JSON documents are ready for the updated source."
    fi
    if [ -n "$MIGRATION_RECOVERY_PATH" ]; then
        task_status warning \
            "JSON migration recovery copies were retained at $MIGRATION_RECOVERY_PATH."
    fi

    # Reuse main's own argv as the install.sh argument vector. Nothing is lost:
    # update accepts only one --debug and one -h/--help, both fully consumed into
    # scalars by the argument scan above, and this script never re-execs itself.
    # Argv is the right carrier because these flags are handed to a command
    # verbatim; see the target-stream contract in common.sh for why a stream is
    # not. The loop cannot be lifted into a function to keep the clobber local,
    # since it also reports per-target warnings in section order.
    set --
    IFS='
'
    for target in $INSTALLED_TARGETS; do
        if stream_contains "$target" "$MIGRATION_FAILED_TARGETS"; then
            task_status warning \
                "[$target] Leaving it disabled after its migration failure."
            continue
        fi
        set -- "$@" "--$target"
    done
    IFS="$OLD_IFS"
    # "$#" counts the target flags built directly above, not this script's
    # arguments: zero means every installed target failed its migration.
    if [ "$#" -eq 0 ]; then
        INSTALL_STATUS=0
        task_status info "No migrated target can be reprovisioned."
    else
        UPDATE_SECTION_STARTED=0
        # The handoff to the fetched install.sh. Both variables are required:
        # the context selects deferred provisioning, which renders and replaces
        # the units but activates nothing, because this script quiesced every
        # timer before the fast-forward and restores their prior states itself
        # below; the internal marker plus the explicit per-target flags built
        # above are what that context validates before it will run at all, so
        # the mode cannot be entered from the command line. install.sh
        # re-filters these flags against the new catalog, which is how a target
        # deregistered upstream is reported and skipped instead of failing the
        # whole update.
        if SCROOGE_INTERNAL_UPDATE=1 SCROOGE_INSTALL_CONTEXT=deferred \
            "$SCRIPT_DIR/install.sh" "$@"; then
            INSTALL_STATUS=0
        else
            INSTALL_STATUS=$?
        fi
        UPDATE_SECTION_STARTED=1
    fi
    if [ "$INSTALL_STATUS" -ne 0 ] && \
        [ "$INSTALL_STATUS" -ne "$EXIT_STATUS_TARGET_CONFIG_ERROR" ]; then
        update_section success "Update recovery"
        task_status failure "Provisioning failed after the source update."
        task_status warning "Affected timers remain disabled for safety."
        task_status warning \
            "Rerun $(command_text './scrooge-alert update'), or inspect with $(command_text './scrooge-alert status')."
        task_status warning \
            "Original timer states were recorded at $UPDATE_RECOVERY_DIR."
        update_exit 1
    fi
    PARTIAL_CONFIG=0
    [ "$INSTALL_STATUS" -ne "$EXIT_STATUS_TARGET_CONFIG_ERROR" ] || PARTIAL_CONFIG=1

    # The new install deliberately left all selected timers quiesced. Restore
    # original enabled/active state, except a service-only damaged installation:
    # its newly reconstructed timer is enabled and started as a repair.
    update_section success "Timer activation"
    task_status info "Restoring eligible target timer states."
    # A catalog failure here is not advisory. CURRENT_TARGETS gates every
    # target's activation below, so an empty list is indistinguishable from
    # "every plugin was removed upstream" -- the legitimate case that branch
    # exists for -- and would disable every timer, tell the user to uninstall
    # healthy targets, and still exit 0. Refuse on an unknown catalog the same
    # way the schedule reload below refuses on unknown cadences.
    if ! update_load_catalog; then
        task_status failure \
            "Could not reload the target catalog after the update."
        task_status warning "Affected timers remain disabled for safety."
        task_status warning \
            "Recorded timer states were retained at $UPDATE_RECOVERY_DIR."
        task_status warning \
            "Rerun $(command_text './scrooge-alert update'), or inspect with $(command_text './scrooge-alert status')."
        update_exit 1
    fi
    if run_captured list_plugins; then
        CURRENT_TARGETS="$CAPTURED_COMMAND_OUTPUT"
    else
        CURRENT_TARGETS=''
    fi
    if ! update_load_schedules; then
        task_status failure \
            "Could not reload target scheduling metadata after the update."
        task_status warning "Affected timers remain disabled for safety."
        task_status warning \
            "Recorded timer states were retained at $UPDATE_RECOVERY_DIR."
        task_status warning \
            "Rerun $(command_text './scrooge-alert update'), or inspect with $(command_text './scrooge-alert status')."
        update_exit 1
    fi
    if run_captured list_interval_status; then
        CURRENT_INTERVAL_STATUS="$CAPTURED_COMMAND_OUTPUT"
    else
        task_status failure "Could not read target schedule statuses."
        task_status warning "Affected timers remain disabled for safety."
        task_status warning \
            "Recorded timer states were retained at $UPDATE_RECOVERY_DIR."
        update_exit 1
    fi
    ACTIVATE_FAILED=0
    IFS='
'
    for target in $INSTALLED_TARGETS; do
        if stream_contains "$target" "$MIGRATION_FAILED_TARGETS"; then
            continue
        fi
        if [ "$MIGRATION_GENERAL_FAILED" -ne 0 ]; then
            task_status warning \
                "[$target] Left disabled after general configuration migration failed."
            continue
        fi
        if ! stream_contains "$target" "$CURRENT_TARGETS"; then
            task_status warning "[$target] Left disabled because it is no longer registered."
            task_status warning "Remove it with $(command_text "./scrooge-alert uninstall --$target")."
            continue
        fi
        read_captured_state "$UPDATE_RECOVERY_DIR/state/$target"
        target_schedule_status="$(
            plugin_stream_value "$target" "$CURRENT_INTERVAL_STATUS" || true
        )"
        if [ "$CAPTURED_TIMER_LOAD" = "not-found" ] &&
           [ "$CAPTURED_SERVICE_LOAD" = "loaded" ] &&
           [ "$target_schedule_status" != "error" ]; then
            if run_with_progress "[$target] Enabling its reconstructed timer..." \
                run_update_helper enable_one "$target"; then
                task_status success "[$target] Enabled its reconstructed timer."
            else
                task_status failure \
                    "[$target] Could not enable its reconstructed timer."
                ACTIVATE_FAILED=1
            fi
        elif run_with_progress "[$target] Restoring its prior timer state..." \
            run_update_helper restore_timer_state \
            "$target" "$CAPTURED_TIMER_LOAD" \
            "$CAPTURED_TIMER_ENABLED" "$CAPTURED_TIMER_ACTIVE"; then
            task_status success "[$target] Restored its prior timer state."
        else
            task_status failure "[$target] Could not restore its prior timer state."
            run_update_helper disable_one "$target" || true
            ACTIVATE_FAILED=1
        fi
    done
    IFS="$OLD_IFS"
    if [ "$ACTIVATE_FAILED" -ne 0 ]; then
        task_status failure \
            "The update installed successfully, but timer activation was incomplete."
        disable_update_targets
        if [ "$UPDATE_DISABLE_FAILED" -eq 0 ]; then
            task_status warning "All selected targets were left disabled for safety."
        else
            task_status failure \
                "Some selected targets could not be verified as disabled."
        fi
        task_status warning \
            "Recorded timer states were retained at $UPDATE_RECOVERY_DIR."
        task_status warning \
            "Inspect with $(command_text './scrooge-alert status') before enabling timers."
        update_exit 1
    fi

    # No rollback is needed after successful activation. Ignore termination only
    # across the tiny recovery-cleanup window so a trap cannot reference data
    # that has just been removed, then restore the default disposition: with the
    # workspace gone there is no phase left for a handler to act on.
    trap '' HUP INT TERM
    run_action rm -rf "$UPDATE_RECOVERY_DIR"
    UPDATE_RECOVERY_DIR=''
    trap - HUP INT TERM

    # The panel below is purely advisory: the update has already succeeded and
    # nothing depends on its content. Skip it when the unit inventory cannot be
    # read rather than reading an empty one as "nothing is installed", which
    # would invite the user to install the targets just restored above.
    NEW_TARGETS=''
    if run_captured list_installed_targets; then
        INSTALLED_NOW="$CAPTURED_COMMAND_OUTPUT"
        IFS='
'
        for target in $CURRENT_TARGETS; do
            if ! stream_contains "$target" "$INSTALLED_NOW"; then
                NEW_TARGETS="$(stream_add_unique "$NEW_TARGETS" "$target")"
            fi
        done
        IFS="$OLD_IFS"
    fi
    if [ -n "$NEW_TARGETS" ]; then
        update_section warning "Additional targets"
        task_status info \
            "Available but not installed: $(stream_for_display "$NEW_TARGETS")"
        task_status info "Install any of them with $(command_text './scrooge-alert install --<target>')."
    fi

    if [ "$MIGRATION_CONFIG_FAILED" -ne 0 ] || [ "$PARTIAL_CONFIG" -ne 0 ]; then
        update_section warning "Update result"
        task_status warning \
            "Update complete, but one or more targets retained their existing units because their configuration is invalid."
        task_status warning \
            "Fix each reported target configuration, then rerun $(command_text './scrooge-alert update')."
        update_exit "$EXIT_STATUS_TARGET_CONFIG_ERROR"
    fi
    if [ "$MIGRATION_GENERAL_FAILED" -ne 0 ]; then
        update_section warning "Update result"
        task_status warning \
            "Update complete, but timers remain disabled because general configuration migration failed."
        task_status warning "Fix config/general.json, then rerun $(command_text './scrooge-alert update')."
        update_exit "$EXIT_STATUS_NOTIFICATION_CONFIG_ERROR"
    fi
    if [ "$MIGRATION_STATE_FAILED" -ne 0 ]; then
        update_section warning "Update result"
        task_status warning \
            "Update complete, but one or more state migrations failed."
        task_status warning \
            "Inspect the reported state and recovery data, then rerun $(command_text './scrooge-alert update')."
        update_exit "$EXIT_STATUS_STORAGE_ERROR"
    fi

    update_section success "Update result"
    task_status success "Update complete. You are now running origin/main."
    update_exit 0
}

main "$@"
