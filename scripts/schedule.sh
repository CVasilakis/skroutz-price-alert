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
    for plugin in $(list_installed_plugins timer); do
        # Skip orphans (installed but no longer a registered scraper) - they have
        # no config to apply. If the registry is unreadable we can't tell, so list all.
        if [ -n "$_registered" ] && ! plugin_in_list "$plugin" $_registered; then
            continue
        fi
        printf '  --%-15s Apply only the %s scraper interval\n' "$plugin" "$plugin"
    done
    printf '\n'
}

# status_of <plugin> <status_stream>: print the interval-resolution status for a
# plugin from the "<plugin><TAB><status>" stream captured from list_interval_status.
status_of() {
    _so_plugin="$1"
    _so_all="$2"
    _so_tab="$(printf '\t')"
    _so_old_ifs="$IFS"
    IFS='
'
    for _so_line in $_so_all; do
        if [ "${_so_line%%"$_so_tab"*}" = "$_so_plugin" ]; then
            IFS="$_so_old_ifs"
            printf '%s' "${_so_line#*"$_so_tab"}"
            return 0
        fi
    done
    IFS="$_so_old_ifs"
}

cd "$SCRIPT_DIR"

case "${1:-}" in
    -h|--help) print_help; exit 0 ;;
esac

require_systemctl

# ------------------------------------------------------------------------------
# TARGET RESOLUTION
# ------------------------------------------------------------------------------
# schedule.sh re-applies the cadence of the timers install.sh provisioned, so it
# acts on the INSTALLED timer units intersected with the registry: it needs Python
# both to enumerate scrapers and to resolve each one's configured interval. An
# installed unit whose plugin was removed (an orphan) has no config to apply, so it
# is reported and skipped. Because resolving intervals requires the registry, a
# readable registry is REQUIRED when units exist.

SELECTED=""
while [ "$#" -gt 0 ]; do
    case "$1" in
        --*) SELECTED="$SELECTED ${1#--}" ;;
        *) printf "%bError: Invalid argument: %s%b\n" "$RED" "$1" "$NC"; exit 1 ;;
    esac
    shift
done

INSTALLED_PLUGINS="$(list_installed_plugins timer)"
REGISTERED="$(list_plugins 2>/dev/null || true)"

# Units exist but the registry can't be read -> without it we can neither enumerate
# scrapers nor resolve their intervals, so refuse rather than guess.
# registry_diagnose says WHY (venv missing vs. a plugin whose discovery failed).
if [ -n "$INSTALLED_PLUGINS" ] && [ -z "$REGISTERED" ]; then
    registry_diagnose || exit 1
fi

if [ -n "$SELECTED" ]; then
    PLUGINS=""
    for sel in $SELECTED; do
        if plugin_in_list "$sel" $INSTALLED_PLUGINS; then
            # Installed: configure it - unless the registry omits it, i.e. it is an
            # orphan whose code is gone, in which case point at uninstall instead.
            if ! plugin_in_list "$sel" $REGISTERED; then
                printf "%b\n" "${RED}Error: '$sel' is installed but no longer a registered scraper (orphan).${NC}"
                printf "%b\n" "Remove its leftover units with: ${CYAN}./scripts/uninstall.sh --$sel${NC}"
                exit 1
            fi
            PLUGINS="$PLUGINS $sel"
        elif plugin_in_list "$sel" $REGISTERED; then
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
    for plugin in $INSTALLED_PLUGINS; do
        if plugin_in_list "$plugin" $REGISTERED; then
            PLUGINS="$PLUGINS $plugin"
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
# Each plugin's effective [Timer] directives already fold in its configured interval
# (list_plugin_timer_directives is config-aware). We compare the resolved block
# against the installed timer's current block and rewrite only when it changed, so an
# unchanged cadence is a true no-op and an active timer is restarted only when its
# schedule actually moved. A missing config or an unsupported value leaves the timer
# untouched (keeping the previously-applied schedule, or the default).

ALL_TIMER_DIRECTIVES="$(list_plugin_timer_directives || true)"
INTERVAL_STATUS="$(list_interval_status || true)"
# The registry is readable at this point (guarded above), so this is non-empty.
SUPPORTED_INTERVAL_KEYS="$(list_supported_intervals || true)"

CHANGED=""
for plugin in $PLUGINS; do
    status="$(status_of "$plugin" "$INTERVAL_STATUS")"

    case "$status" in
        nocfg)
            printf "%b\n" "\n${YELLOW}[$plugin] No config file found; leaving its timer unchanged.${NC}"
            printf "%b\n" "Create it by copying ${CYAN}config/$plugin.json.example${NC} to ${CYAN}config/$plugin.json${NC}."
            continue
            ;;
        invalid)
            printf "%b\n" "\n${YELLOW}[$plugin] Unsupported execution_interval in config; leaving its timer unchanged.${NC}"
            printf "%b\n" "Use one of: ${CYAN}$SUPPORTED_INTERVAL_KEYS${NC}."
            continue
            ;;
    esac

    new_block="$(plugin_timer_block "$plugin" "$ALL_TIMER_DIRECTIVES")"
    if [ -z "$new_block" ]; then
        printf "%b\n" "\n${RED}[$plugin] Declares no [Timer] directives; skipping.${NC}"
        continue
    fi

    if [ "$new_block" = "$(read_timer_block "$plugin")" ]; then
        printf "%b\n" "\n${GREEN}[$plugin] Timer already matches the configured interval. Nothing to do.${NC}"
        continue
    fi

    printf "%b\n" "\n${CYAN}[$plugin] Updating the timer schedule to match the configured interval...${NC}"
    if write_plugin_units "$plugin" "$new_block"; then
        CHANGED="$CHANGED $plugin"
        printf "%b\n" "${GREEN}[$plugin] Timer schedule updated.${NC}"
    else
        printf "%b\n" "${RED}[$plugin] Error: Failed to write the systemd timer unit.${NC}"
        exit 1
    fi
done

# ------------------------------------------------------------------------------
# RELOAD AND RESTART CHANGED TIMERS
# ------------------------------------------------------------------------------
# daemon-reload makes systemd read the rewritten unit files; try-restart re-arms an
# *active* timer so it recomputes its next elapse from the new schedule, while
# leaving a disabled timer alone (it picks up the new file when re-enabled).

if [ -n "$CHANGED" ]; then
    systemctl --user daemon-reload
    for plugin in $CHANGED; do
        systemctl --user try-restart "$(unit_name "$plugin" timer)" 2>/dev/null || true
    done
    printf "%b\n" "\n${GREEN}Done. Updated:${NC}${CYAN}$(printf ' %s' $CHANGED)${NC}\n"
else
    printf "%b\n" "\n${GREEN}All timers already match their configured intervals. Nothing changed.${NC}\n"
fi
