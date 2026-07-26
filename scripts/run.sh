#!/bin/sh
set -eu

# ==============================================================================
# GLOBAL VARIABLES
# ==============================================================================

# Get the directory where the script is located
SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname -- "$SCRIPT_DIR")"

# Shared helpers (colors, plugin enumeration)
# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$SCRIPT_DIR/lib/preflight.sh"

VENV_PYTHON="$BASE_DIR/venv/bin/python3"
PLUGINS=""

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

# Note for developers/agents: In user-facing text, a "plugin" is referred to as a "target".
print_fixed_help() {
    printf '\n'
    printf '%s\n' "Usage: run.sh [-h] [--quiet] [--status] [--ping] [--<target> ...]"
    printf '\n'
    printf '%s\n' "Run price checks, send a test notification, or inspect installation health."
    printf '%s\n' "With no target flag, price-check every registered target."
    printf '\n'
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n' "  --quiet           Run script with no console output"
    printf '%s\n' "  --status          Perform a health check of the background service"
    printf '%s\n' "  --ping            Send a test notification via Apprise"
}

print_help() {
    print_fixed_help
    for plugin in $PLUGINS; do
        display_name="$(plugin_display_name "$plugin")"
        printf '  --%-15s Run exclusively the %s scraper\n' "$plugin" "${display_name:-$plugin}"
    done
    printf '\n'
}

print_missing_venv_help() {
    print_fixed_help
    printf '\n'
    printf '%s\n' "Target-specific options are unavailable because the project"
    printf '%s\n' "virtual environment is not installed."
    printf '%s\n' "Run ./install.sh, then rerun this help command to list registered targets."
    printf '\n'
}

# Help remains useful before installation and is recognized in any position.
HELP_REQUESTED=0
for raw_arg in "$@"; do
    case "$raw_arg" in -h|--help) HELP_REQUESTED=1 ;; esac
done
if [ "$HELP_REQUESTED" -eq 1 ] && [ ! -x "$VENV_PYTHON" ]; then
    print_missing_venv_help
    exit 0
fi

reject_project_venv_symlink || exit 1
require_python_310 "$VENV_PYTHON" "./install.sh" || exit 1

# Registered plugins (one --<plugin> flag is accepted per registered scraper).
load_plugin_catalog || true
PLUGINS="$(list_plugins || true)"
if [ "$HELP_REQUESTED" -eq 1 ]; then
    print_help
    exit 0
fi

# ==============================================================================
# EXECUTION
# ==============================================================================

TARGET="main.py"
FORWARD_COUNT=0

append_forward_arg() {
    FORWARD_COUNT=$((FORWARD_COUNT + 1))
    # Values reaching this helper are fixed built-ins or validated target flags.
    eval "FORWARD_$FORWARD_COUNT=\$1"
}

exec_with_forward_args() {
    _ewfa_index="$1"
    shift
    if [ "$_ewfa_index" -gt "$FORWARD_COUNT" ]; then
        exec "$VENV_PYTHON" "$BASE_DIR/src/core/$TARGET" "$@"
    fi
    _ewfa_value=''
    eval "_ewfa_value=\${FORWARD_$_ewfa_index}"
    exec_with_forward_args "$((_ewfa_index + 1))" "$@" "$_ewfa_value"
}

# Flags tracking for validation
FLAG_PING=0
FLAG_STATUS=0
FLAG_QUIET=0
FLAG_PLUGIN=0

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help)
            print_help
            exit 0
            ;;
        --ping)
            # Counted (not just set) so a repeated flag still trips the
            # "must be used alone" validation below.
            FLAG_PING=$((FLAG_PING + 1))
            TARGET="ping.py"
            ;;
        --status)
            FLAG_STATUS=$((FLAG_STATUS + 1))
            TARGET="status.py"
            ;;
        --quiet)
            FLAG_QUIET=$((FLAG_QUIET + 1))
            append_forward_arg --quiet
            ;;
        --)
            # A bare '--' would otherwise parse as an empty target name.
            printf "%bError: Invalid flag provided: %s%b\n" "$RED" "$1" "$NC"
            print_help
            exit 1
            ;;
        --*)
            # Any registered plugin (e.g. --skroutz) selects that scraper and is
            # forwarded to main.py, which builds a matching flag per plugin.
            name="${1#--}"
            require_valid_target "$name" || exit 1
            if stream_contains "$name" "$PLUGINS"; then
                FLAG_PLUGIN=$((FLAG_PLUGIN + 1))
                append_forward_arg "$1"
            else
                # An empty plugin list means the flag was rejected because the
                # catalog itself is unavailable, not because of a typo - say so.
                if [ -z "$PLUGINS" ]; then
                    catalog_diagnose || exit 1
                fi
                printf "%bError: Invalid flag provided: %s%b\n" "$RED" "$1" "$NC"
                print_help
                exit 1
            fi
            ;;
        *)
            printf "%bError: Invalid flag provided: %s%b\n" "$RED" "$1" "$NC"
            print_help
            exit 1
            ;;
    esac
    shift
done

# ==============================================================================
# VALIDATION
# ==============================================================================

TOTAL_FLAGS=$((FLAG_PING + FLAG_STATUS + FLAG_QUIET + FLAG_PLUGIN))

# Check --ping rules (-gt 0, not -eq 1: a repeated flag counts up and must
# still land in this validation)
if [ "$FLAG_PING" -gt 0 ]; then
    if [ "$TOTAL_FLAGS" -gt 1 ]; then
        printf "%b\n" "${RED}\nError: The --ping flag must be used alone.${NC}"
        print_help
        exit 1
    fi
fi

# Check --status rules
if [ "$FLAG_STATUS" -gt 0 ]; then
    if [ "$TOTAL_FLAGS" -gt 1 ]; then
        printf "%b\n" "${RED}\nError: The --status flag must be used alone.${NC}"
        print_help
        exit 1
    fi
fi

exec_with_forward_args 1
