#!/bin/sh
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

    UPDATE_PHASE="preflight"
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
        printf '%s\n' "Optional arguments:"
        printf '%s\n' "  -h, --help        show this help message and exit"
        printf '%s\n' "  --debug           show underlying command output"
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

    update_task() {
        _ut_kind="$1"
        shift
        case "$_ut_kind" in
            success) _ut_marker='v'; _ut_color="$GREEN" ;;
            failure) _ut_marker='x'; _ut_color="$RED" ;;
            info) _ut_marker='i'; _ut_color="$CYAN" ;;
            warning) _ut_marker='!'; _ut_color="$YELLOW" ;;
            *) return 2 ;;
        esac
        _ut_prefix="    ${_ut_color}[${_ut_marker}]${NC} "
        _print_indented_wrapped "$_ut_prefix" '        ' "$@"
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

    # shellcheck disable=SC2329  # invoked indirectly through run_update_helper
    update_require_git_worktree() {
        [ "$DEBUG_MODE" -eq 1 ] || {
            require_git_worktree
            return
        }
        require_command git Git || return 1
        if ! run_captured git -C "$BASE_DIR" rev-parse --is-inside-work-tree ||
           [ "$CAPTURED_COMMAND_OUTPUT" != "true" ]; then
            printf '%s\n' "Error: $BASE_DIR is not a Git worktree." >&2
            return 1
        fi
        if ! run_captured git -C "$BASE_DIR" rev-parse --is-bare-repository ||
           [ "$CAPTURED_COMMAND_OUTPUT" != "false" ]; then
            printf '%s\n' \
                "Error: $BASE_DIR is a bare or unusable Git repository." >&2
            return 1
        fi
    }

    # shellcheck disable=SC2329  # invoked indirectly through run_update_helper
    update_require_clean_worktree() {
        [ "$DEBUG_MODE" -eq 1 ] || {
            require_clean_worktree
            return
        }
        if ! run_captured git -C "$BASE_DIR" status \
            --porcelain --untracked-files=normal; then
            printf '%s\n' "Error: Could not inspect the Git working tree." >&2
            return 1
        fi
        if [ -n "$CAPTURED_COMMAND_OUTPUT" ]; then
            printf '%s\n' \
                "Error: The working tree contains tracked changes or nonignored untracked files." >&2
            printf '%s\n' \
                "Commit or stash your work before running $(command_text './scrooge-alert update'); nothing was changed." >&2
            return 1
        fi
    }

    # shellcheck disable=SC2329  # invoked indirectly through run_update_helper
    update_require_main_branch() {
        [ "$DEBUG_MODE" -eq 1 ] || {
            require_main_branch
            return
        }
        if ! run_captured git -C "$BASE_DIR" symbolic-ref --short HEAD; then
            printf '%s\n' \
                "Error: The checkout is in detached-HEAD state; $(command_text './scrooge-alert update') requires branch 'main'." >&2
            return 1
        fi
        if [ "$CAPTURED_COMMAND_OUTPUT" != "main" ]; then
            printf '%s\n' \
                "Error: $(command_text './scrooge-alert update') requires branch 'main' (current branch: '$CAPTURED_COMMAND_OUTPUT')." >&2
            printf '%s\n' \
                "Switch branches yourself after saving any work, then retry." >&2
            return 1
        fi
    }

    # shellcheck disable=SC2329  # invoked indirectly through run_update_helper
    update_require_origin_remote() {
        [ "$DEBUG_MODE" -eq 1 ] || {
            require_origin_remote
            return
        }
        if ! run_captured git -C "$BASE_DIR" remote get-url origin; then
            printf '%s\n' "Error: Git remote 'origin' is missing or unusable." >&2
            return 1
        fi
    }

    # shellcheck disable=SC2329  # invoked indirectly through run_update_helper
    update_require_origin_main() {
        [ "$DEBUG_MODE" -eq 1 ] || {
            require_origin_main
            return
        }
        update_require_origin_remote || return 1
        if ! run_captured git -C "$BASE_DIR" rev-parse --verify \
            'refs/remotes/origin/main^{commit}'; then
            printf '%s\n' \
                "Error: origin/main is missing or does not name a commit." >&2
            return 1
        fi
    }

    # shellcheck disable=SC2329  # invoked indirectly through run_update_helper
    update_require_revision_paths() {
        [ "$DEBUG_MODE" -eq 1 ] || {
            require_revision_paths "$@"
            return
        }
        _urp_revision="$1"
        shift
        for _urp_path in "$@"; do
            if ! run_captured git -C "$BASE_DIR" cat-file -e \
                "$_urp_revision:$_urp_path"; then
                printf '%s\n' \
                    "Error: Fetched revision $_urp_revision is missing required file '$_urp_path'." >&2
                return 1
            fi
        done
    }

    # shellcheck disable=SC2329  # invoked indirectly through run_update_helper
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

    # shellcheck disable=SC2329  # invoked indirectly through run_update_helper
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
        # shellcheck disable=SC2086  # intentional newline-only stream iteration
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

    # shellcheck disable=SC2317,SC2329  # invoked indirectly by HUP/INT/TERM traps
    update_interrupted() {
        _ui_signal="$1"
        _ui_status="$2"
        trap '' HUP INT TERM
        update_section success "Update interruption"
        update_task failure "Update interrupted by $_ui_signal."
        case "$UPDATE_PHASE" in
            capturing)
                [ -z "$UPDATE_RECOVERY_DIR" ] ||
                    run_action rm -rf "$UPDATE_RECOVERY_DIR"
                ;;
            captured|quiescing)
                if restore_update_snapshot; then
                    run_action rm -rf "$UPDATE_RECOVERY_DIR"
                    update_task success "The original timer states were restored."
                else
                    update_task failure "Timer-state restoration was incomplete."
                    update_task warning "Recovery data was retained at $UPDATE_RECOVERY_DIR."
                fi
                ;;
            advancing|post_advance|migrating|provisioning|activating)
                disable_update_targets
                if [ "$UPDATE_DISABLE_FAILED" -eq 0 ]; then
                    update_task warning "Affected timers were left disabled for safety."
                else
                    update_task failure \
                        "Some affected timer states could not be verified as disabled."
                fi
                update_task warning "Recovery data was retained at $UPDATE_RECOVERY_DIR."
                update_task warning \
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
        update_task failure "The --debug flag may be specified only once."
        update_task warning "Run $(command_text './scrooge-alert update --help') for usage."
        update_exit 1
    fi
    if [ "$NONDEBUG_ARGUMENTS" -gt 1 ]; then
        update_section success "Update arguments"
        update_task failure \
            "$(command_text './scrooge-alert update') accepts no arguments other than one -h or --help, plus --debug."
        update_task warning "Run $(command_text './scrooge-alert update --help') for usage."
        update_exit 1
    fi
    if [ "$NONDEBUG_ARGUMENTS" -eq 1 ]; then
        case "$NONDEBUG_ARGUMENT" in
            -h|--help) print_help; exit 0 ;;
            *)
                update_section success "Update arguments"
                update_task failure "Invalid argument: $NONDEBUG_ARGUMENT"
                update_task warning "Run $(command_text './scrooge-alert update --help') for usage."
                update_exit 1
                ;;
        esac
    fi

    trap 'update_interrupted HUP 129' HUP
    trap 'update_interrupted INT 130' INT
    trap 'update_interrupted TERM 143' TERM

    update_section success "Update checks"
    if ! run_update_helper reject_project_venv_symlink; then
        update_task failure "The project venv path is a symlink."
        update_task warning \
            "Remove the venv symlink, then recreate it with ./scripts/dev/setup.sh or $(command_text './scrooge-alert install')."
        update_exit 1
    fi
    if ! run_update_helper require_systemctl; then
        update_task failure "Systemd user services are not available."
        update_task warning "Install or enable systemd user services, then retry."
        update_exit 1
    fi
    if ! run_update_helper update_require_git_worktree; then
        update_task failure "The project directory is not a usable Git worktree."
        update_task warning "Restore the Git checkout, then retry."
        update_exit 1
    fi
    if ! run_update_helper update_require_main_branch; then
        update_task failure "The checkout is not on branch 'main'."
        update_task warning "Switch to main after saving any work, then retry."
        update_exit 1
    fi
    if ! run_update_helper update_require_clean_worktree; then
        update_task failure \
            "The working tree contains tracked changes or nonignored untracked files."
        update_task warning "Commit or stash your work before running $(command_text './scrooge-alert update')."
        update_exit 1
    fi
    if ! run_update_helper update_require_origin_remote; then
        update_task failure "Git remote 'origin' is missing or unusable."
        update_task warning "Restore the origin remote, then retry."
        update_exit 1
    fi
    if run_captured list_installed_targets; then
        INSTALLED_TARGETS="$CAPTURED_COMMAND_OUTPUT"
    else
        update_task failure "Installed target units could not be inspected."
        update_task warning "Inspect with $(command_text './scrooge-alert status'), then retry."
        update_exit 1
    fi
    if [ -z "$INSTALLED_TARGETS" ]; then
        update_task failure "No installed target timer or service units were found."
        update_task warning "Choose targets explicitly with $(command_text './scrooge-alert install --<target>')."
        update_exit 1
    fi
    # Updates only own absent or regular unit destinations. Reject links and
    # special entries before fetching or quiescing any target.
    if ! run_update_helper validate_unit_destinations \
        "$INSTALLED_TARGETS" pair; then
        update_task failure "A managed systemd unit destination is unsafe."
        update_task warning \
            "Remove the unsafe unit with $(command_text './scrooge-alert uninstall --<target>'), then retry."
        update_exit 1
    fi
    update_task success "The checkout and installed target selection are safe to update."

    update_section success "Source update"
    UPDATE_PHASE="fetching"
    if run_with_progress "Fetching origin/main..." fetch_update_source; then
        FETCH_STATUS=0
    else
        FETCH_STATUS=$?
    fi
    if [ "$FETCH_STATUS" -ne 0 ]; then
        update_task failure "Failed to fetch origin/main."
        update_task info "Nothing was stopped or changed."
        update_exit 1
    fi
    update_task success "Fetched origin/main before stopping any target."

    # Close the race between the first inspection and the destructive phase.
    if ! run_update_helper update_require_main_branch ||
        ! run_update_helper update_require_clean_worktree ||
        ! run_update_helper update_require_origin_main ||
        ! run_update_helper require_fast_forward_to_origin ||
        ! run_update_helper update_require_revision_paths origin/main \
            scrooge-alert \
            scripts/install.sh \
            scripts/dev/migrate.sh \
            scripts/lib/common.sh \
            scripts/lib/preflight.sh \
            scripts/lib/systemd.sh \
            scripts/lib/provisioning.sh; then
        update_task failure "The fetched update failed safety validation."
        update_task warning \
            "Reconcile the checkout or fetched origin/main, then rerun $(command_text './scrooge-alert update --debug')."
        update_exit 1
    fi
    update_task success "Verified origin/main can safely fast-forward this checkout."

    update_section success "Target quiescence"
    if run_captured create_private_workspace update; then
        UPDATE_RECOVERY_DIR="$CAPTURED_COMMAND_OUTPUT"
    else
        update_task failure \
            "Could not create private update state in $SYSTEMD_USER_DIR."
        update_task info "No target was stopped."
        update_exit 1
    fi
    if ! run_update_helper initialize_unit_snapshot "$UPDATE_RECOVERY_DIR"; then
        run_action rm -rf "$UPDATE_RECOVERY_DIR"
        update_task failure "Could not initialize private update recovery data."
        update_task info "No target was stopped."
        update_exit 1
    fi

    UPDATE_PHASE="capturing"
    if ! run_update_helper capture_unit_snapshot "$INSTALLED_TARGETS" pair \
        "$UPDATE_RECOVERY_DIR"; then
        run_action rm -rf "$UPDATE_RECOVERY_DIR"
        update_task failure "Could not capture unit files and timer states."
        update_task info "Update aborted before any target was stopped."
        update_exit 1
    fi
    update_task success "Captured the installed unit files and timer states."

    UPDATE_PHASE="captured"
    QUIESCE_FAILED=0
    UPDATE_PHASE="quiescing"
    OLD_IFS="$IFS"
    IFS='
'
    # shellcheck disable=SC2086  # intentional newline-only stream iteration
    for target in $INSTALLED_TARGETS; do
        if run_with_progress "[$target] Stopping and disabling its timer..." \
            run_update_helper disable_one "$target"; then
            update_task success "[$target] Safely stopped and disabled its timer."
        else
            update_task failure "[$target] Could not be safely quiesced."
            QUIESCE_FAILED=1
        fi
    done
    IFS="$OLD_IFS"
    if [ "$QUIESCE_FAILED" -ne 0 ]; then
        if restore_update_snapshot; then
            run_action rm -rf "$UPDATE_RECOVERY_DIR"
            update_task success "The original timer states were restored."
        else
            update_task warning \
                "Timer-state recovery data was retained at $UPDATE_RECOVERY_DIR."
        fi
        update_task failure "Update aborted before changing project files."
        update_exit 1
    fi

    UPDATE_PHASE="advancing"
    update_section success "Source advancement"
    if run_with_progress "Advancing the checkout with a verified fast-forward..." \
        advance_update_source; then
        ADVANCE_STATUS=0
    else
        ADVANCE_STATUS=$?
    fi
    if [ "$ADVANCE_STATUS" -ne 0 ]; then
        update_task failure "Failed to update project files."
        if restore_update_snapshot; then
            run_action rm -rf "$UPDATE_RECOVERY_DIR"
            update_task success "The prior timer states were restored."
        else
            update_task failure "Timer-state restoration was incomplete."
            update_task warning "Recovery data was retained at $UPDATE_RECOVERY_DIR."
        fi
        update_exit 1
    fi
    UPDATE_PHASE="post_advance"
    update_task success "Fast-forwarded the project files to origin/main."

    if [ ! -f "$SCRIPT_DIR/install.sh" ] || [ -L "$SCRIPT_DIR/install.sh" ]; then
        update_task failure "The fetched update has no safe scripts/install.sh."
        update_task warning "Target timers remain disabled."
        update_task warning "Repair the checkout, then rerun $(command_text './scrooge-alert update')."
        update_exit 1
    fi

    UPDATE_PHASE="migrating"
    update_section success "JSON migration"
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
            update_task failure "JSON migration infrastructure failed."
            update_task warning "Affected timers remain disabled for safety."
            update_task warning "Retry with $(command_text './scrooge-alert update --debug')."
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
    # shellcheck disable=SC2086
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
        update_task failure \
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
        update_task failure "JSON migration infrastructure failed."
        update_task warning "Affected timers remain disabled for safety."
        update_task warning "Retry with $(command_text './scrooge-alert update --debug')."
        update_exit "$MIGRATION_STATUS"
    fi
    if [ "$MIGRATION_FAILURES" -eq 0 ]; then
        update_task success "Managed JSON documents are ready for the updated source."
    fi
    if [ -n "$MIGRATION_RECOVERY_PATH" ]; then
        update_task warning \
            "JSON migration recovery copies were retained at $MIGRATION_RECOVERY_PATH."
    fi

    set --
    IFS='
'
    # shellcheck disable=SC2086  # intentional newline-only stream iteration
    for target in $INSTALLED_TARGETS; do
        if stream_contains "$target" "$MIGRATION_FAILED_TARGETS"; then
            update_task warning \
                "[$target] Leaving it disabled after its migration failure."
            continue
        fi
        set -- "$@" "--$target"
    done
    IFS="$OLD_IFS"
    UPDATE_PHASE="provisioning"
    if [ "$#" -eq 0 ]; then
        INSTALL_STATUS=0
        update_task info "No migrated target can be reprovisioned."
    else
        UPDATE_SECTION_STARTED=0
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
        update_task failure "Provisioning failed after the source update."
        update_task warning "Affected timers remain disabled for safety."
        update_task warning \
            "Rerun $(command_text './scrooge-alert update'), or inspect with $(command_text './scrooge-alert status')."
        update_task warning \
            "Original timer states were recorded at $UPDATE_RECOVERY_DIR."
        update_exit 1
    fi
    PARTIAL_CONFIG=0
    [ "$INSTALL_STATUS" -ne "$EXIT_STATUS_TARGET_CONFIG_ERROR" ] || PARTIAL_CONFIG=1

    # The new install deliberately left all selected timers quiesced. Restore
    # original enabled/active state, except a service-only damaged installation:
    # its newly reconstructed timer is enabled and started as a repair.
    update_section success "Timer activation"
    update_task info "Restoring eligible target timer states."
    run_update_helper update_load_catalog || true
    if run_captured list_plugins; then
        CURRENT_TARGETS="$CAPTURED_COMMAND_OUTPUT"
    else
        CURRENT_TARGETS=''
    fi
    if ! run_update_helper update_load_schedules; then
        update_task failure \
            "Could not reload target scheduling metadata after the update."
        update_task warning "Affected timers remain disabled for safety."
        update_task warning \
            "Recorded timer states were retained at $UPDATE_RECOVERY_DIR."
        update_task warning \
            "Rerun $(command_text './scrooge-alert update'), or inspect with $(command_text './scrooge-alert status')."
        update_exit 1
    fi
    if run_captured list_interval_status; then
        CURRENT_INTERVAL_STATUS="$CAPTURED_COMMAND_OUTPUT"
    else
        update_task failure "Could not read target schedule statuses."
        update_task warning "Affected timers remain disabled for safety."
        update_task warning \
            "Recorded timer states were retained at $UPDATE_RECOVERY_DIR."
        update_exit 1
    fi
    ACTIVATE_FAILED=0
    UPDATE_PHASE="activating"
    IFS='
'
    # shellcheck disable=SC2086  # intentional newline-only stream iteration
    for target in $INSTALLED_TARGETS; do
        if stream_contains "$target" "$MIGRATION_FAILED_TARGETS"; then
            continue
        fi
        if [ "$MIGRATION_GENERAL_FAILED" -ne 0 ]; then
            update_task warning \
                "[$target] Left disabled after general configuration migration failed."
            continue
        fi
        if ! stream_contains "$target" "$CURRENT_TARGETS"; then
            update_task warning "[$target] Left disabled because it is no longer registered."
            update_task warning "Remove it with $(command_text "./scrooge-alert uninstall --$target")."
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
                update_task success "[$target] Enabled its reconstructed timer."
            else
                update_task failure \
                    "[$target] Could not enable its reconstructed timer."
                ACTIVATE_FAILED=1
            fi
        elif run_with_progress "[$target] Restoring its prior timer state..." \
            run_update_helper restore_timer_state \
            "$target" "$CAPTURED_TIMER_LOAD" \
            "$CAPTURED_TIMER_ENABLED" "$CAPTURED_TIMER_ACTIVE"; then
            update_task success "[$target] Restored its prior timer state."
        else
            update_task failure "[$target] Could not restore its prior timer state."
            run_update_helper disable_one "$target" || true
            ACTIVATE_FAILED=1
        fi
    done
    IFS="$OLD_IFS"
    if [ "$ACTIVATE_FAILED" -ne 0 ]; then
        update_task failure \
            "The update installed successfully, but timer activation was incomplete."
        disable_update_targets
        if [ "$UPDATE_DISABLE_FAILED" -eq 0 ]; then
            update_task warning "All selected targets were left disabled for safety."
        else
            update_task failure \
                "Some selected targets could not be verified as disabled."
        fi
        update_task warning \
            "Recorded timer states were retained at $UPDATE_RECOVERY_DIR."
        update_task warning \
            "Inspect with $(command_text './scrooge-alert status') before enabling timers."
        update_exit 1
    fi

    # No rollback is needed after successful activation. Ignore termination only
    # across the tiny recovery-cleanup window so a trap cannot reference data
    # that has just been removed.
    trap '' HUP INT TERM
    UPDATE_PHASE="complete"
    run_action rm -rf "$UPDATE_RECOVERY_DIR"
    UPDATE_RECOVERY_DIR=''
    trap - HUP INT TERM

    NEW_TARGETS=''
    if run_captured list_installed_targets; then
        INSTALLED_NOW="$CAPTURED_COMMAND_OUTPUT"
    else
        INSTALLED_NOW=''
    fi
    IFS='
'
    # shellcheck disable=SC2086  # intentional newline-only stream iteration
    for target in $CURRENT_TARGETS; do
        if ! stream_contains "$target" "$INSTALLED_NOW"; then
            NEW_TARGETS="$(stream_add_unique "$NEW_TARGETS" "$target")"
        fi
    done
    IFS="$OLD_IFS"
    if [ -n "$NEW_TARGETS" ]; then
        update_section warning "Additional targets"
        update_task info \
            "Available but not installed: $(stream_for_display "$NEW_TARGETS")"
        update_task info "Install any of them with $(command_text './scrooge-alert install --<target>')."
    fi

    if [ "$MIGRATION_CONFIG_FAILED" -ne 0 ] || [ "$PARTIAL_CONFIG" -ne 0 ]; then
        update_section warning "Update result"
        update_task warning \
            "Update complete, but one or more targets retained their existing units because their configuration is invalid."
        update_task warning \
            "Fix each reported target configuration, then rerun $(command_text './scrooge-alert update')."
        update_exit "$EXIT_STATUS_TARGET_CONFIG_ERROR"
    fi
    if [ "$MIGRATION_GENERAL_FAILED" -ne 0 ]; then
        update_section warning "Update result"
        update_task warning \
            "Update complete, but timers remain disabled because general configuration migration failed."
        update_task warning "Fix config/general.json, then rerun $(command_text './scrooge-alert update')."
        update_exit "$EXIT_STATUS_NOTIFICATION_CONFIG_ERROR"
    fi
    if [ "$MIGRATION_STATE_FAILED" -ne 0 ]; then
        update_section warning "Update result"
        update_task warning \
            "Update complete, but one or more state migrations failed."
        update_task warning \
            "Inspect the reported state and recovery data, then rerun $(command_text './scrooge-alert update')."
        update_exit "$EXIT_STATUS_STORAGE_ERROR"
    fi

    update_section success "Update result"
    update_task success "Update complete. You are now running origin/main."
    update_exit 0
}

main "$@"
