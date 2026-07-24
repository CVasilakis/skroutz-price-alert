#!/bin/sh
set -eu

# ==============================================================================
# GLOBAL VARIABLES
# ==============================================================================

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "$0" )" >/dev/null 2>&1 && pwd )"
BASE_DIR="$( dirname "$SCRIPT_DIR" )"

# Shared helpers (colors, plugin enumeration, systemd helpers)
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

# Note for developers/agents: In user-facing text, a "plugin" is referred to as a "target".
print_help() {
    load_plugin_manifest || true
    _registered="$(list_plugins 2>/dev/null || true)"
    # The supported cadences come from the settings vocabulary (SUPPORTED_INTERVALS),
    # not a literal here, so this help can never drift from the code.
    _intervals="$(list_supported_intervals 2>/dev/null || true)"

    printf '\n'
    printf '%s\n' "Usage: schedule.sh [-h] [--<target> ...]"
    printf '\n'
    printf '%s\n' "Apply each scraper's configured execution interval to its systemd timer. The"
    printf '%s\n' "interval is read from the \"settings.execution_interval\" field of the scraper's"
    printf '%s\n' "config file (config/<target>.json) and translated to the timer's schedule. With"
    printf '%s\n' "no target flag every installed scraper is updated; pass one or more --<target>"
    printf '%s\n' "flags to update only those."
    printf '\n'
    printf '%s\n' "Supported intervals: ${_intervals:-unavailable (run ./install.sh first)}"
    printf '%s\n' "Many spellings are accepted, e.g. \"1 hour\", \"60m\" and \"hourly\" all mean 1h."
    printf '%s\n' "An unset interval keeps the scraper's default; an unsupported value is reported"
    printf '%s\n' "and the timer is left unchanged."
    printf '\n'
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    _installed="$(list_installed_plugins timer)"
    # shellcheck disable=SC2086  # intentional newline-delimited target stream
    for plugin in $_installed; do
        # Skip orphans (installed but no longer a registered scraper) - they have
        # no config to apply. If the catalog is unavailable we can't tell, so list all.
        if [ -n "$_registered" ] && ! stream_contains "$plugin" "$_registered"; then
            continue
        fi
        printf '  --%-15s Apply only the %s scraper interval\n' "$plugin" "$plugin"
    done
    printf '\n'
}

# ------------------------------------------------------------------------------
# TARGET RESOLUTION
# ------------------------------------------------------------------------------
# schedule.sh re-applies the cadence of the timers install.sh provisioned, so it
# acts on the INSTALLED timer units intersected with the catalog: it needs Python
# both to enumerate scrapers and to resolve each one's configured interval. An
# installed unit whose plugin was removed (an orphan) has no config to apply, so it
# is reported and skipped. Because resolving intervals requires the catalog, a
# readable catalog is REQUIRED when units exist.
# -h/--help is honored anywhere in the argument list; a bare '--' is rejected
# (it would otherwise parse as an empty target name and silently select nothing).

SELECTED=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help) print_help; exit 0 ;;
        --) printf "%bError: Invalid argument: %s%b\n" "$RED" "$1" "$NC"; exit 1 ;;
        --*)
            target="${1#--}"
            require_valid_target "$target" || exit 1
            SELECTED="$(stream_add_unique "$SELECTED" "$target")"
            ;;
        *) printf "%bError: Invalid argument: %s%b\n" "$RED" "$1" "$NC"; exit 1 ;;
    esac
    shift
done

require_systemctl

load_plugin_manifest || true
INSTALLED_PLUGINS="$(list_installed_plugins timer)"
REGISTERED="$(list_plugins 2>/dev/null || true)"

# Units exist but the catalog can't be read -> without it we can neither enumerate
# scrapers nor resolve their intervals, so refuse rather than guess.
# catalog_diagnose says WHY (venv missing vs. a plugin whose discovery failed).
if [ -n "$INSTALLED_PLUGINS" ] && [ -z "$REGISTERED" ]; then
    catalog_diagnose || exit 1
fi

if [ -n "$SELECTED" ]; then
    PLUGINS=""
    # shellcheck disable=SC2086  # intentional newline-delimited target stream
    for sel in $SELECTED; do
        if stream_contains "$sel" "$INSTALLED_PLUGINS"; then
            # Installed: configure it - unless the catalog omits it, i.e. it is an
            # orphan whose code is gone, in which case point at uninstall instead.
            if ! stream_contains "$sel" "$REGISTERED"; then
                printf "%b\n" "${RED}Error: '$sel' is installed but no longer a registered scraper (orphan).${NC}"
                printf "%b\n" "Remove its leftover units with: ${CYAN}./scripts/uninstall.sh --$sel${NC}"
                exit 1
            fi
            PLUGINS="$(stream_add_unique "$PLUGINS" "$sel")"
        elif stream_contains "$sel" "$REGISTERED"; then
            # A real scraper, but install.sh never provisioned its timer - there is
            # no unit to reschedule.
            printf "%b\n" "${RED}Error: '$sel' is registered but not installed.${NC}"
            printf "%b\n" "Install it first with: ${CYAN}./install.sh --$sel${NC}"
            exit 1
        else
            printf "%b\n" "${RED}Error: Unknown target '$sel'.${NC}"
            [ -z "$INSTALLED_PLUGINS" ] || \
                printf "%b\n" "Installed scrapers: ${CYAN}$(printf '%s ' $INSTALLED_PLUGINS)${NC}"
            exit 1
        fi
    done
else
    # No flag: every installed timer that is STILL a registered scraper. Orphans
    # (installed but de-registered) are reported here, then skipped - they have no
    # config and no code to schedule.
    PLUGINS=""
    # shellcheck disable=SC2086  # intentional newline-delimited target stream
    for plugin in $INSTALLED_PLUGINS; do
        if stream_contains "$plugin" "$REGISTERED"; then
            PLUGINS="$(stream_add_unique "$PLUGINS" "$plugin")"
        else
            printf "%b\n" "\n${YELLOW}[$plugin] Installed but no longer a registered scraper (orphan); skipping.${NC}"
            printf "%b\n" "Remove its leftover units with: ${CYAN}./scripts/uninstall.sh --$plugin${NC}"
        fi
    done
fi

if [ -z "$PLUGINS" ]; then
    if [ -n "$INSTALLED_PLUGINS" ]; then
        printf "%b\n" "\n${YELLOW}Nothing to schedule: every installed unit is an orphan (no longer a registered scraper).${NC}"
        printf "%b\n" "Remove the leftovers with ${CYAN}./scripts/uninstall.sh${NC} (see ${CYAN}./scripts/uninstall.sh --help${NC})."
    else
        printf "%b\n" "\n${YELLOW}No installed scrapers found.${NC}"
        printf "%b\n" "Run ${CYAN}./install.sh${NC} to provision your scrapers.\n"
    fi
    exit 0
fi

# ------------------------------------------------------------------------------
# APPLYING INTERVALS
# ------------------------------------------------------------------------------
# Each plugin's effective schedule already folds in its configured interval. We
# compare the resolved OnCalendar value against the installed timer and rewrite only when it changed, so an
# unchanged cadence is a true no-op and an active timer is restarted only when its
# schedule actually moved. A missing config or an unsupported value leaves the timer
# untouched (keeping the previously-applied schedule, or the default).

if ! ALL_SCHEDULES="$(list_plugin_schedules)" || \
   ! INTERVAL_STATUS="$(list_interval_status)" || \
   ! SUPPORTED_INTERVAL_KEYS="$(list_supported_intervals)" || \
   ! EXAMPLE_PAIRS="$(list_plugin_examples)"; then
    printf "%b\n" "${RED}Error: Failed to resolve plugin scheduling metadata.${NC}\n"
    exit 1
fi

CHANGED=""
ACTIVE_CHANGED=""
INACTIVE_CHANGED=""
FAILED=0
# shellcheck disable=SC2086  # intentional newline-delimited target stream
for plugin in $PLUGINS; do
    if ! status="$(plugin_stream_value "$plugin" "$INTERVAL_STATUS")"; then
        printf "%b\n" "\n${RED}[$plugin] Could not resolve execution_interval status; skipping.${NC}"
        FAILED=1
        continue
    fi

    case "$status" in
        nocfg)
            printf "%b\n" "\n${YELLOW}[$plugin] No config file found; leaving its timer unchanged.${NC}"
            if example_path="$(plugin_stream_value "$plugin" "$EXAMPLE_PAIRS")"; then
                printf "%b\n" "Create it by copying ${CYAN}$example_path${NC} to ${CYAN}config/$plugin.json${NC}."
            fi
            continue
            ;;
        invalid)
            printf "%b\n" "\n${YELLOW}[$plugin] Unsupported execution_interval in config; leaving its timer unchanged.${NC}"
            printf "%b\n" "Use one of: ${CYAN}$SUPPORTED_INTERVAL_KEYS${NC}."
            continue
            ;;
    esac

    new_calendar="$(plugin_stream_value "$plugin" "$ALL_SCHEDULES")"
    if [ -z "$new_calendar" ]; then
        printf "%b\n" "\n${RED}[$plugin] Has no resolved schedule; skipping.${NC}"
        FAILED=1
        continue
    fi

    if [ "$new_calendar" = "$(read_timer_oncalendar "$plugin")" ]; then
        printf "%b\n" "\n${GREEN}[$plugin] Timer already matches the configured interval. Nothing to do.${NC}"
        continue
    fi

    if ! prior_active="$(timer_is_active "$plugin")"; then
        printf "%b\n" "\n${RED}[$plugin] Could not determine the timer's active state; skipping.${NC}"
        FAILED=1
        continue
    fi
    if [ "$prior_active" = "active" ]; then
        ACTIVE_CHANGED="$(stream_add_unique "$ACTIVE_CHANGED" "$plugin")"
    elif state_is_stopped "$prior_active"; then
        INACTIVE_CHANGED="$(stream_add_unique "$INACTIVE_CHANGED" "$plugin")"
    else
        printf "%b\n" "\n${RED}[$plugin] Timer is in unexpected state '$prior_active'; skipping.${NC}"
        FAILED=1
        continue
    fi

    printf "%b\n" "\n${CYAN}[$plugin] Updating the timer schedule to match the configured interval...${NC}"
    if write_plugin_timer_unit "$plugin" "$new_calendar"; then
        CHANGED="$(stream_add_unique "$CHANGED" "$plugin")"
        printf "%b\n" "${GREEN}[$plugin] Timer unit updated.${NC}"
    else
        printf "%b\n" "${RED}[$plugin] Error: Failed to write the systemd timer unit.${NC}"
        FAILED=1
    fi
done

# ------------------------------------------------------------------------------
# RELOAD AND RESTART CHANGED TIMERS
# ------------------------------------------------------------------------------
# daemon-reload makes systemd read the rewritten unit files; timers that were
# active before the change are restarted and verified so they recompute their next
# elapse, while inactive timers remain stopped until explicitly enabled.

if [ -n "$CHANGED" ]; then
    if systemctl --user daemon-reload; then
        # shellcheck disable=SC2086  # intentional newline-delimited target stream
        for plugin in $ACTIVE_CHANGED; do
            if ! stream_contains "$plugin" "$CHANGED"; then
                continue
            fi
            if ! restart_timer_one "$plugin"; then
                printf "%b\n" "${RED}[$plugin] Error: The active timer could not be re-armed.${NC}"
                FAILED=1
            fi
        done
        # shellcheck disable=SC2086  # intentional newline-delimited target stream
        for plugin in $INACTIVE_CHANGED; do
            if ! stream_contains "$plugin" "$CHANGED"; then
                continue
            fi
            if ! inactive_state="$(timer_is_active "$plugin")"; then
                printf "%b\n" "${RED}[$plugin] Error: Could not verify the inactive timer after reload.${NC}"
                FAILED=1
            elif ! state_is_stopped "$inactive_state"; then
                printf "%b\n" "${RED}[$plugin] Error: Timer unexpectedly became '$inactive_state'.${NC}"
                FAILED=1
            fi
        done
    else
        printf "%b\n" "${RED}Error: Failed to reload the systemd user manager; updated files remain on disk.${NC}"
        FAILED=1
    fi
fi

if [ "$FAILED" -ne 0 ]; then
    [ -z "$CHANGED" ] || \
        printf "%b\n" "\n${YELLOW}Timer files written but not fully activated:${NC} ${CYAN}$(stream_for_display "$CHANGED")${NC}"
    printf "%b\n" "${RED}One or more timer schedules could not be applied.${NC}\n"
    exit 1
fi

if [ -n "$CHANGED" ]; then
    printf "%b\n" "\n${GREEN}Done. Updated:${NC} ${CYAN}$(stream_for_display "$CHANGED")${NC}\n"
else
    printf "%b\n" "\n${GREEN}All timers already match their configured intervals. Nothing changed.${NC}\n"
fi
