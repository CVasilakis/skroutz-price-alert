#!/bin/sh
set -eu

# ==============================================================================
# GLOBAL VARIABLES
# ==============================================================================

# Get the directory where the script is located
SCRIPT_DIR="$( cd "$( dirname "$0" )" >/dev/null 2>&1 && pwd )"
BASE_DIR="$( dirname "$SCRIPT_DIR" )"

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

# Help remains useful before installation, but a healthy installation supplies
# the dynamic target rows from the catalog.
case "${1:-}" in
    -h|--help)
        if [ ! -x "$VENV_PYTHON" ]; then
            print_missing_venv_help
            exit 0
        fi
        ;;
esac

require_python_310 "$VENV_PYTHON" "./install.sh" || exit 1

# Registered plugins (one --<plugin> flag is accepted per registered scraper).
load_plugin_manifest || true
PLUGINS="$(list_plugins || true)"

# ==============================================================================
# EXECUTION
# ==============================================================================

TARGET="main.py"
ARGS=""

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
            shift
            ;;
        --status)
            FLAG_STATUS=$((FLAG_STATUS + 1))
            TARGET="status.py"
            shift
            ;;
        --quiet)
            FLAG_QUIET=$((FLAG_QUIET + 1))
            ARGS="$(stream_add_unique "$ARGS" "--quiet")"
            shift
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
                ARGS="$(stream_add_unique "$ARGS" "$1")"
                shift
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

# A missing venv would otherwise surface as the shell's raw "not found" from exec;
# fail with the same repair hint catalog_diagnose gives. (The plugin-flag path
# already routes through catalog_diagnose above.)
set --
OLD_IFS="$IFS"
IFS='
'
# shellcheck disable=SC2086  # intentional newline-only stream iteration
for arg in $ARGS; do
    set -- "$@" "$arg"
done
IFS="$OLD_IFS"
exec "$VENV_PYTHON" "$BASE_DIR/src/core/$TARGET" "$@"
