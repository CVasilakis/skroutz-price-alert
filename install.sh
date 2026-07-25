#!/bin/sh
set -eu

# ==============================================================================
# GLOBAL VARIABLES
# ==============================================================================

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$SCRIPT_DIR"

# Shared helpers (colors, plugin enumeration, systemd helpers)
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/scripts/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$SCRIPT_DIR/scripts/lib/preflight.sh"
# shellcheck source=scripts/lib/systemd.sh
. "$SCRIPT_DIR/scripts/lib/systemd.sh"
# shellcheck source=scripts/lib/provisioning.sh
. "$SCRIPT_DIR/scripts/lib/provisioning.sh"

# Environment and File Configurations
VENV_DIR="venv"
REQUIREMENTS_FILE="requirements.txt"

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

# Note for developers/agents: In user-facing text, a "plugin" is referred to as a "target".
print_help() {
    load_plugin_catalog || true
    printf '\n'
    printf '%s\n' "Usage: install.sh [-h] [--<target> ...]"
    printf '\n'
    printf '%s\n' "Set up the Python virtual environment and install the systemd timer(s) and"
    printf '%s\n' "service(s). With no target flag every registered scraper is installed and"
    printf '%s\n' "enabled; pass one or more --<target> flags to install only those. You can"
    printf '%s\n' "run this command as many times as you like - run it again in the future"
    printf '%s\n' "to install additional scrapers."
    printf '\n'
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    for plugin in $(list_plugins 2>/dev/null || true); do
        display_name="$(plugin_display_name "$plugin")"
        printf '  --%-15s Install and enable only the %s scraper\n' "$plugin" "${display_name:-$plugin}"
    done
    printf '\n'
}

cd "$SCRIPT_DIR"
CATALOG_PYTHON=python3
parse_target_flags "$@" || exit 1
if [ "$TARGET_HELP_REQUESTED" -eq 1 ]; then
    print_help
    exit 0
fi
case "${SCROOGE_INSTALL_CONTEXT:-normal}" in
    normal) IS_UPDATE=0 ;;
    deferred)
        IS_UPDATE=1
        if [ "${SCROOGE_INTERNAL_UPDATE:-}" != 1 ] ||
            [ "$TARGET_FLAGS_EXPLICIT" -ne 1 ] ||
            [ -z "$TARGET_FLAGS" ]; then
            printf '%s\n' "Error: Invalid internal deferred-install context." >&2
            exit 1
        fi
        ;;
    *)
        printf '%s\n' "Error: Invalid install context." >&2
        exit 1
        ;;
esac
reject_project_venv_symlink || exit 1
require_python_310 python3 "./install.sh" || exit 1

# Validate the import-light catalog, selection, source inputs, and every unit
# destination before venv creation or package installation.
load_plugin_catalog || {
    catalog_diagnose || exit 1
}
ALL_PLUGINS="$(list_plugins)"
if [ "$IS_UPDATE" -eq 0 ]; then
    select_targets registered || exit 1
    PLUGINS="$SELECTED_TARGETS"
else
    PLUGINS=''
    OLD_IFS="$IFS"
    IFS='
'
    # shellcheck disable=SC2086
    for sel in $TARGET_FLAGS; do
        if stream_contains "$sel" "$ALL_PLUGINS"; then
            PLUGINS="$(stream_add_unique "$PLUGINS" "$sel")"
        else
            printf '%s\n' \
                "Note: '$sel' is no longer registered; its units remain disabled."
            printf '%s\n' \
                "Remove them with: ./scripts/uninstall.sh --$sel"
        fi
    done
    IFS="$OLD_IFS"
fi

for required_file in requirements.txt scripts/run.sh scripts/lib/common.sh \
    scripts/lib/preflight.sh scripts/lib/systemd.sh scripts/lib/provisioning.sh; do
    require_regular_owned_file "$BASE_DIR/$required_file" || exit 1
done
EARLY_PLUGIN_REQS="$(list_plugin_requirements)" || exit 1
OLD_IFS="$IFS"
IFS='
'
PAIR_TAB="$(printf '\t')"
# shellcheck disable=SC2086
for pair in $EARLY_PLUGIN_REQS; do
    req_name="${pair%%"$PAIR_TAB"*}"
    req_path="${pair#*"$PAIR_TAB"}"
    stream_contains "$req_name" "$PLUGINS" || continue
    require_regular_owned_file "$req_path" || exit 1
done
IFS="$OLD_IFS"
validate_unit_destinations "$PLUGINS" pair || exit 1

if [ -d "$VENV_DIR" ]; then
    require_python_310 "$VENV_DIR/bin/python3" "./scripts/uninstall.sh then ./install.sh" || exit 1
fi

if ! python3 -c "import ensurepip" > /dev/null 2>&1; then
    printf "%b\n" "${RED}Error: The python venv module is not available. Please install it first.${NC}"
    exit 1
fi

require_systemctl || exit 1

# ------------------------------------------------------------------------------
# PYTHON VIRTUAL ENVIRONMENT SETUP
# ------------------------------------------------------------------------------

# Initialize or update python virtual environment
VENV_NEWLY_CREATED=false
if [ ! -d "$VENV_DIR" ]; then
    printf "%b\n" "\n${CYAN}Creating python virtual environment...${NC}"
    if ! python3 -m venv "$VENV_DIR"; then
        printf "%b\n" "${RED}Error: Failed to create python virtual environment.${NC}\n"
        exit 1
    fi
    VENV_NEWLY_CREATED=true
else
    printf "%b\n" "\n${CYAN}Updating python packages in existing virtual environment...${NC}"
fi

require_python_310 "$VENV_DIR/bin/python3" "./scripts/uninstall.sh then ./install.sh" || exit 1

# Safely upgrade pip and install matching requirements
if ! "$VENV_DIR/bin/python3" -m pip install -q --upgrade pip; then
    printf "%b\n" "${RED}Error: Failed to upgrade pip in the virtual environment.${NC}\n"
    exit 1
fi

if [ -f "$REQUIREMENTS_FILE" ]; then
    if ! "$VENV_DIR/bin/python3" -m pip install -q --upgrade -r "$REQUIREMENTS_FILE"; then
        printf "%b\n" "${RED}Error: Failed to install packages from $REQUIREMENTS_FILE.${NC}\n"
        exit 1
    fi
else
    printf "%b\n" "${RED}Error: $REQUIREMENTS_FILE not found. The script cannot run without its dependencies.${NC}\n"
    exit 1
fi

if [ "$VENV_NEWLY_CREATED" = true ]; then
    printf "%b\n" "${GREEN}Python virtual environment successfully created.${NC}"
else
    printf "%b\n" "${GREEN}Python virtual environment successfully updated.${NC}"
fi

# Re-read the same import-light metadata through the completed venv before
# installing plugin-private dependencies and resolving schedules.
CATALOG_PYTHON="$BASE_DIR/venv/bin/python3"
reset_catalog_cache
load_plugin_catalog || {
    catalog_diagnose || exit 1
}
FINAL_PLUGINS="$(list_plugins)"
if [ "$FINAL_PLUGINS" != "$ALL_PLUGINS" ]; then
    printf '%s\n' "Error: Plugin catalog changed during installation." >&2
    exit 1
fi

# ------------------------------------------------------------------------------
# PER-PLUGIN DEPENDENCIES
# ------------------------------------------------------------------------------
# The root requirements.txt installed above carries only the core framework. Each
# plugin may ship its own requirements.txt (next to its plugin.py) listing the
# transport/parsing libraries only it needs (e.g. tls-client, selenium). Only the
# requirements of the plugin(s) being provisioned are installed, so an install
# that skips a heavy scraper never pulls that scraper's dependencies.

if ! PLUGIN_REQS="$(list_plugin_requirements)"; then
    printf "%b\n" "${RED}Error: Failed to read per-plugin dependency metadata.${NC}\n"
    exit 1
fi
OLD_IFS="$IFS"
IFS='
'
PAIR_TAB="$(printf '\t')"
# shellcheck disable=SC2086  # intentional newline-only stream iteration
for pair in $PLUGIN_REQS; do
    req_name="${pair%%"$PAIR_TAB"*}"
    req_path="${pair#*"$PAIR_TAB"}"
    stream_contains "$req_name" "$PLUGINS" || continue

    printf "%b\n" "${CYAN}Installing dependencies for the '$req_name' scraper...${NC}"
    if ! "$VENV_DIR/bin/python3" -m pip install -q --upgrade -r "$req_path"; then
        IFS="$OLD_IFS"
        printf "%b\n" "${RED}Error: Failed to install dependencies for the '$req_name' scraper.${NC}\n"
        exit 1
    fi
done
IFS="$OLD_IFS"

if ! "$VENV_DIR/bin/python3" -m pip check; then
    printf "%b\n" "${RED}Error: Installed core and plugin dependencies are incompatible.${NC}\n"
    exit 1
fi

# ------------------------------------------------------------------------------
# SYSTEMD SETUP
# ------------------------------------------------------------------------------

printf "%b\n" "\n${CYAN}Setting up Systemd timer(s)...${NC}"

mkdir -p "$SYSTEMD_USER_DIR"

# Resolve config-dependent schedules separately from the immutable plugin catalog.
# A structurally invalid config excludes only its own target from this transaction.
if ! load_plugin_schedules || \
   ! ALL_SCHEDULES="$(list_plugin_schedules)" || \
   ! INTERVAL_STATUS="$(list_interval_status)" || \
   ! SCHEDULE_ERRORS="$(list_schedule_errors)"; then
    printf "%b\n" "${RED}Error: Failed to resolve scraper scheduling metadata.${NC}\n"
    exit 1
fi

CONFIG_FAILED=0
PROVISION_PLUGINS=""
OLD_IFS="$IFS"
IFS='
'
# shellcheck disable=SC2086  # intentional newline-only stream iteration
for plugin in $PLUGINS; do
    status="$(plugin_stream_value "$plugin" "$INTERVAL_STATUS" || true)"
    if [ -z "$status" ]; then
        IFS="$OLD_IFS"
        printf "%b\n" "${RED}Error: No scheduling result was returned for target '$plugin'.${NC}\n"
        exit 1
    fi
    if [ "$status" = "error" ]; then
        schedule_error="$(plugin_stream_value "$plugin" "$SCHEDULE_ERRORS" || true)"
        printf "%b\n" "\n${RED}[$plugin] Error: ${schedule_error:-Could not resolve its timer schedule.}${NC}"
        printf "%b\n" "${YELLOW}[$plugin] Existing systemd units were left unchanged.${NC}"
        CONFIG_FAILED=1
        continue
    fi
    PROVISION_PLUGINS="$(stream_add_unique "$PROVISION_PLUGINS" "$plugin")"
done
IFS="$OLD_IFS"

if [ -n "$PROVISION_PLUGINS" ]; then
    if [ "$IS_UPDATE" -eq 1 ]; then
        PROVISION_MODE="deferred"
    else
        PROVISION_MODE="normal"
    fi
    if ! provision_units_transaction "$PROVISION_PLUGINS" "$ALL_SCHEDULES" "$PROVISION_MODE"; then
        if [ "$IS_UPDATE" -eq 1 ]; then
            printf "%b\n" "${RED}Error: Transactional systemd provisioning failed during update.${NC}\n"
        else
            printf "%b\n" "${RED}Error: Transactional systemd provisioning failed.${NC}\n"
        fi
        [ -z "${PROVISION_RECOVERY_DIR:-}" ] || \
            printf "%b\n" "${YELLOW}Recovery files: $PROVISION_RECOVERY_DIR${NC}"
        exit 1
    fi
fi

if command -v loginctl >/dev/null 2>&1; then
    # $USER is conventionally exported but not guaranteed (clean env, some
    # containers/cron); fall back to `id -un` so `set -u` never aborts here.
    LINGER_USER="${USER:-$(id -un)}"
    if [ "$(loginctl show-user "$LINGER_USER" --property=Linger 2>/dev/null)" != "Linger=yes" ]; then
        printf "%b\n" "${CYAN}Enabling user lingering to allow timer to run when logged out...${NC}"
        # Non-fatal: lingering only lets timers run while logged out; without it the
        # install is still valid (timers run while logged in), so a failure here
        # (e.g. a system that requires root to enable linger) must not abort.
        loginctl enable-linger "$LINGER_USER" || printf "%b\n" "${YELLOW}Warning: Could not enable user lingering; timers will run only while you are logged in.${NC}"
    fi
fi

if [ -n "$PROVISION_PLUGINS" ]; then
    printf "%b\n" "${GREEN}Systemd timer(s) configured successfully.${NC}"
fi

# ------------------------------------------------------------------------------
# LAST CHECKS
# ------------------------------------------------------------------------------
# Report any plugin whose products config file is still missing (non-fatal), and
# whether the shared general configuration is missing.

MISSING_CONFIGS=""
if ! EXAMPLE_PAIRS="$(list_plugin_examples)"; then
    printf "%b\n" "${RED}Error: Failed to read plugin configuration metadata.${NC}\n"
    exit 1
fi
OLD_IFS="$IFS"
IFS='
'
# shellcheck disable=SC2086  # intentional newline-only stream iteration
for plugin in $PLUGINS; do
    [ -f "config/$plugin.json" ] || MISSING_CONFIGS="$MISSING_CONFIGS $plugin"
done
IFS="$OLD_IFS"

GENERAL_CONFIG_MISSING=0
[ -f "config/general.json" ] || GENERAL_CONFIG_MISSING=1

if [ -n "$MISSING_CONFIGS" ] || [ "$GENERAL_CONFIG_MISSING" -eq 1 ]; then
    printf "%b\n" "\n${YELLOW}Note: Configuration required!${NC}"

    for plugin in $MISSING_CONFIGS; do
        example="$(plugin_stream_value "$plugin" "$EXAMPLE_PAIRS")"
        printf "%b\n" "- Copy $example to config/$plugin.json"
        printf "%b\n" "  and fill it with your desired items."
    done

    if [ "$GENERAL_CONFIG_MISSING" -eq 1 ]; then
        printf "%b\n" "- Copy src/core/general/config.example.json to config/general.json"
        printf "%b\n" "  and configure your Apprise notification URLs and preferences."
    fi

    printf "%b\n" "- Read the README.md file for more information."
fi

if [ "$IS_UPDATE" -eq 0 ]; then
    if [ "$CONFIG_FAILED" -eq 0 ]; then
        printf "%b\n" "\n${GREEN}Installation complete!${NC}\n"
    fi
fi

if [ "$CONFIG_FAILED" -ne 0 ]; then
    printf "%b\n" "\n${RED}One or more targets were skipped because their configuration is invalid.${NC}\n"
    exit 15
fi
