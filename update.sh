#!/bin/sh
set -eu

# Keep the entire update in one function: the shell reads these function bodies
# before git replaces files in the checkout.
main() {
    SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
    BASE_DIR="$SCRIPT_DIR"

    # shellcheck source=scripts/lib/common.sh
    . "$SCRIPT_DIR/scripts/lib/common.sh"
    # shellcheck source=scripts/lib/preflight.sh
    . "$SCRIPT_DIR/scripts/lib/preflight.sh"
    # shellcheck source=scripts/lib/provisioning.sh
    . "$SCRIPT_DIR/scripts/lib/provisioning.sh"

    UPDATE_PHASE="preflight"
    UPDATE_RECOVERY_DIR=''
    INSTALLED_TARGETS=''

    # shellcheck disable=SC2329  # reached from the signal trap handler
    disable_update_targets() {
        UPDATE_DISABLE_FAILED=0
        _dut_old_ifs="$IFS"
        IFS='
'
        # shellcheck disable=SC2086  # intentional newline-only stream iteration
        for _dut_target in $INSTALLED_TARGETS; do
            disable_one "$_dut_target" || UPDATE_DISABLE_FAILED=1
        done
        IFS="$_dut_old_ifs"
    }

    restore_update_snapshot() {
        _rus_failed=0
        _rus_old_ifs="$IFS"
        IFS='
'
        # shellcheck disable=SC2086  # intentional newline-only stream iteration
        for _rus_target in $INSTALLED_TARGETS; do
            for _rus_suffix in service timer; do
                _rus_name="$(unit_name "$_rus_target" "$_rus_suffix")"
                _rus_live="$SYSTEMD_USER_DIR/$_rus_name"
                if [ -f "$UPDATE_RECOVERY_DIR/existed/$_rus_name" ]; then
                    restore_unit_file "$UPDATE_RECOVERY_DIR/backups/$_rus_name" \
                        "$_rus_live" || _rus_failed=1
                else
                    rm -f "$_rus_live" || _rus_failed=1
                fi
            done
        done
        IFS="$_rus_old_ifs"
        systemctl --user daemon-reload >/dev/null || _rus_failed=1
        if [ "$_rus_failed" -eq 0 ]; then
            restore_captured_states "$INSTALLED_TARGETS" \
                "$UPDATE_RECOVERY_DIR/state" || _rus_failed=1
        fi
        [ "$_rus_failed" -eq 0 ]
    }

    # shellcheck disable=SC2317,SC2329  # invoked indirectly by HUP/INT/TERM traps
    update_interrupted() {
        _ui_signal="$1"
        _ui_status="$2"
        trap '' HUP INT TERM
        printf '%s\n' "Update interrupted by $_ui_signal." >&2
        case "$UPDATE_PHASE" in
            capturing)
                [ -z "$UPDATE_RECOVERY_DIR" ] || rm -rf "$UPDATE_RECOVERY_DIR"
                ;;
            captured|quiescing)
                if restore_update_snapshot; then
                    rm -rf "$UPDATE_RECOVERY_DIR"
                    printf '%s\n' "The original timer states were restored." >&2
                else
                    printf '%s\n' "Timer-state restoration was incomplete. Recovery data:" >&2
                    printf '%s\n' "$UPDATE_RECOVERY_DIR" >&2
                fi
                ;;
            resetting|post_reset|provisioning|activating)
                disable_update_targets
                if [ "$UPDATE_DISABLE_FAILED" -eq 0 ]; then
                    printf '%s\n' "Affected timers were left disabled for safety." >&2
                else
                    printf '%s\n' \
                        "Warning: Some affected timer states could not be verified as disabled." >&2
                fi
                printf '%s\n' "Recovery data was retained at:" >&2
                printf '%s\n' "$UPDATE_RECOVERY_DIR" >&2
                printf '%s\n' "Rerun ./update.sh or inspect ./scripts/run.sh --status." >&2
                ;;
        esac
        exit "$_ui_status"
    }

    print_help() {
        printf '\n'
        printf '%s\n' "Usage: update.sh [-h|--help]"
        printf '\n'
        printf '%s\n' "Safely update Scrooge Alert from origin/main and transactionally"
        printf '%s\n' "reprovision exactly the scraper targets that are already installed."
        printf '\n'
    }

    if [ "$#" -gt 1 ]; then
        printf '%s\n' "Error: update.sh accepts no arguments other than one -h or --help." >&2
        exit 1
    fi
    if [ "$#" -eq 1 ]; then
        case "$1" in
            -h|--help) print_help; exit 0 ;;
            *) printf "%bError: Invalid argument: %s%b\n" "$RED" "$1" "$NC"; exit 1 ;;
        esac
    fi
    trap 'update_interrupted HUP 129' HUP
    trap 'update_interrupted INT 130' INT
    trap 'update_interrupted TERM 143' TERM

    reject_project_venv_symlink || exit 1
    require_systemctl
    require_git_worktree || exit 1
    require_main_branch || exit 1
    require_clean_worktree || exit 1
    require_origin_remote || exit 1

    INSTALLED_TARGETS="$(list_installed_targets)"
    if [ -z "$INSTALLED_TARGETS" ]; then
        printf '%s\n' "Error: No installed scraper timer or service units were found." >&2
        printf '%s\n' "Choose targets explicitly with ./install.sh --<target>." >&2
        exit 1
    fi

    printf "%b\n" "\n${CYAN}Updating Scrooge Alert...${NC}"
    UPDATE_PHASE="fetching"
    printf "%b\n" "${CYAN}Fetching origin/main before stopping any scraper...${NC}"
    if ! git -C "$BASE_DIR" fetch --quiet origin main; then
        printf "%b\n" "${RED}Error: Failed to fetch origin/main. Nothing was stopped or changed.${NC}"
        exit 1
    fi

    # Close the race between the first inspection and the destructive phase.
    require_main_branch || exit 1
    require_clean_worktree || exit 1
    require_origin_main || exit 1
    require_fast_forward_to_origin || exit 1
    require_revision_paths origin/main \
        install.sh \
        scripts/lib/common.sh \
        scripts/lib/preflight.sh \
        scripts/lib/provisioning.sh || exit 1

    UPDATE_RECOVERY_DIR="$(create_private_workspace update)" || {
        printf '%s\n' "Error: Could not create private update state in $SYSTEMD_USER_DIR." >&2
        exit 1
    }
    mkdir "$UPDATE_RECOVERY_DIR/state" "$UPDATE_RECOVERY_DIR/backups" \
        "$UPDATE_RECOVERY_DIR/existed" || {
        rm -rf "$UPDATE_RECOVERY_DIR"
        printf '%s\n' "Error: Could not initialize private update recovery data." >&2
        exit 1
    }

    CAPTURE_FAILED=0
    UPDATE_PHASE="capturing"
    OLD_IFS="$IFS"
    IFS='
'
    # shellcheck disable=SC2086  # intentional newline-only stream iteration
    for target in $INSTALLED_TARGETS; do
        for suffix in service timer; do
            name="$(unit_name "$target" "$suffix")"
            live="$SYSTEMD_USER_DIR/$name"
            if ! require_supported_unit_entry "$live"; then
                CAPTURE_FAILED=1
                continue
            fi
            if path_entry_exists "$live"; then
                : > "$UPDATE_RECOVERY_DIR/existed/$name"
                if ! cp -Pp "$live" "$UPDATE_RECOVERY_DIR/backups/$name" ||
                   ! unit_file_matches_backup "$live" \
                        "$UPDATE_RECOVERY_DIR/backups/$name"; then
                    printf "%b\n" \
                        "${RED}Error: Could not capture the unit file for '$target'.${NC}"
                    CAPTURE_FAILED=1
                fi
            fi
        done
        if ! capture_timer_state "$target" "$UPDATE_RECOVERY_DIR/state/$target"; then
            printf "%b\n" "${RED}Error: Could not capture the timer state for '$target'.${NC}"
            CAPTURE_FAILED=1
        fi
    done
    IFS="$OLD_IFS"
    if [ "$CAPTURE_FAILED" -ne 0 ]; then
        rm -rf "$UPDATE_RECOVERY_DIR"
        printf "%b\n" "${RED}Update aborted before any scraper was stopped.${NC}"
        exit 1
    fi

    UPDATE_PHASE="captured"
    QUIESCE_FAILED=0
    UPDATE_PHASE="quiescing"
    IFS='
'
    # shellcheck disable=SC2086  # intentional newline-only stream iteration
    for target in $INSTALLED_TARGETS; do
        printf "%b\n" "${CYAN}Quiescing '$target'...${NC}"
        if ! disable_one "$target"; then
            printf "%b\n" "${RED}Error: Could not safely quiesce '$target'.${NC}"
            QUIESCE_FAILED=1
        fi
    done
    IFS="$OLD_IFS"
    if [ "$QUIESCE_FAILED" -ne 0 ]; then
        if restore_update_snapshot; then
            rm -rf "$UPDATE_RECOVERY_DIR"
        else
            printf '%s\n' "Timer-state recovery data was retained at:" >&2
            printf '%s\n' "$UPDATE_RECOVERY_DIR" >&2
        fi
        printf "%b\n" "${RED}Update aborted before changing project files.${NC}"
        exit 1
    fi

    UPDATE_PHASE="resetting"
    if ! git -C "$BASE_DIR" reset --hard --quiet origin/main; then
        printf "%b\n" "${RED}Error: Failed to update project files; restoring timer states.${NC}"
        if restore_update_snapshot; then
            rm -rf "$UPDATE_RECOVERY_DIR"
            printf "%b\n" "${YELLOW}The prior timer states were restored.${NC}"
        else
            printf "%b\n" "${RED}Timer-state restoration was incomplete. Recovery data:${NC}"
            printf '%s\n' "$UPDATE_RECOVERY_DIR"
        fi
        exit 1
    fi
    UPDATE_PHASE="post_reset"

    if [ ! -f "$SCRIPT_DIR/install.sh" ] || [ -L "$SCRIPT_DIR/install.sh" ]; then
        printf "%b\n" "${RED}Error: The fetched update has no install.sh.${NC}"
        printf "%b\n" "${YELLOW}Scraper timers remain disabled. Repair the checkout, then rerun update.sh.${NC}"
        exit 1
    fi
    chmod +x "$SCRIPT_DIR/install.sh"

    set -- --update
    IFS='
'
    # shellcheck disable=SC2086  # intentional newline-only stream iteration
    for target in $INSTALLED_TARGETS; do
        set -- "$@" "--$target"
    done
    IFS="$OLD_IFS"
    UPDATE_PHASE="provisioning"
    if SCROOGE_INTERNAL_UPDATE=1 "$SCRIPT_DIR/install.sh" "$@"; then
        INSTALL_STATUS=0
    else
        INSTALL_STATUS=$?
    fi
    if [ "$INSTALL_STATUS" -ne 0 ] && [ "$INSTALL_STATUS" -ne 15 ]; then
        printf "%b\n" "${RED}Error: Provisioning failed after the source update.${NC}"
        printf "%b\n" "${YELLOW}Affected timers remain disabled for safety.${NC}"
        printf "%b\n" "Rerun ${CYAN}./update.sh${NC}, or inspect with ${CYAN}./scripts/run.sh --status${NC}."
        printf '%s\n' "Original timer states were recorded at:"
        printf '%s\n' "$UPDATE_RECOVERY_DIR"
        exit 1
    fi
    PARTIAL_CONFIG=0
    [ "$INSTALL_STATUS" -ne 15 ] || PARTIAL_CONFIG=1

    # The new install deliberately left all selected timers quiesced. Restore
    # original enabled/active state, except a service-only damaged installation:
    # its newly reconstructed timer is enabled and started as a repair.
    load_plugin_catalog || true
    CURRENT_TARGETS="$(list_plugins 2>/dev/null || true)"
    if ! load_plugin_schedules; then
        printf "%b\n" "${RED}Error: Could not reload scraper scheduling metadata after the update.${NC}"
        printf "%b\n" "${YELLOW}Affected timers remain disabled for safety.${NC}"
        printf '%s\n' "Recorded timer states were retained at:"
        printf '%s\n' "$UPDATE_RECOVERY_DIR"
        printf "%b\n" "Rerun ${CYAN}./update.sh${NC}, or inspect with ${CYAN}./scripts/run.sh --status${NC}."
        exit 1
    fi
    CURRENT_INTERVAL_STATUS="$(list_interval_status)"
    ACTIVATE_FAILED=0
    UPDATE_PHASE="activating"
    IFS='
'
    # shellcheck disable=SC2086  # intentional newline-only stream iteration
    for target in $INSTALLED_TARGETS; do
        if ! stream_contains "$target" "$CURRENT_TARGETS"; then
            printf "%b\n" "${YELLOW}Leaving removed target '$target' disabled.${NC}"
            continue
        fi
        read_captured_state "$UPDATE_RECOVERY_DIR/state/$target"
        target_schedule_status="$(
            plugin_stream_value "$target" "$CURRENT_INTERVAL_STATUS" || true
        )"
        if [ "$CAPTURED_TIMER_LOAD" = "not-found" ] &&
           [ "$CAPTURED_SERVICE_LOAD" = "loaded" ] &&
           [ "$target_schedule_status" != "error" ]; then
            printf "%b\n" "${CYAN}Enabling reconstructed timer for '$target'...${NC}"
            if ! enable_one "$target"; then
                ACTIVATE_FAILED=1
            fi
        elif ! restore_timer_state "$target" "$CAPTURED_TIMER_LOAD" \
            "$CAPTURED_TIMER_ENABLED" "$CAPTURED_TIMER_ACTIVE"; then
            printf "%b\n" "${RED}Error: Could not restore the timer state for '$target'.${NC}"
            disable_one "$target" || true
            ACTIVATE_FAILED=1
        fi
    done
    IFS="$OLD_IFS"
    if [ "$ACTIVATE_FAILED" -ne 0 ]; then
        printf "%b\n" "${RED}The update installed successfully, but timer activation was incomplete.${NC}"
        disable_update_targets
        if [ "$UPDATE_DISABLE_FAILED" -eq 0 ]; then
            printf "%b\n" "${YELLOW}All selected targets were left disabled for safety.${NC}"
        else
            printf "%b\n" \
                "${RED}Warning: Some selected targets could not be verified as disabled.${NC}"
        fi
        printf '%s\n' "Recorded timer states were retained at:"
        printf '%s\n' "$UPDATE_RECOVERY_DIR"
        printf "%b\n" "Inspect with ${CYAN}./scripts/run.sh --status${NC} before enabling timers."
        exit 1
    fi
    # No rollback is needed after successful activation. Ignore termination only
    # across the tiny recovery-cleanup window so a trap cannot reference data
    # that has just been removed.
    trap '' HUP INT TERM
    UPDATE_PHASE="complete"
    rm -rf "$UPDATE_RECOVERY_DIR"
    UPDATE_RECOVERY_DIR=''
    trap - HUP INT TERM

    NEW_TARGETS=''
    INSTALLED_NOW="$(list_installed_targets)"
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
        printf "%b\n" "\n${YELLOW}Scrapers available but not installed:${NC} ${CYAN}$(stream_for_display "$NEW_TARGETS")${NC}"
        printf "%b\n" "Install any of them with: ${CYAN}./install.sh --<target>${NC}"
    fi

    if [ "$PARTIAL_CONFIG" -ne 0 ]; then
        printf "%b\n" "\n${YELLOW}Update complete, but one or more targets retained their existing units because their configuration is invalid.${NC}\n"
        exit 15
    fi

    printf "%b\n" "\n${GREEN}Update complete! You are now running origin/main.${NC}\n"
    exit 0
}

main "$@"
