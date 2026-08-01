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

VENV_PYTHON="$BASE_DIR/venv/bin/python3"
PLUGINS=""
CATALOG_AVAILABLE=0

# ==============================================================================
# HELPER FUNCTIONS
# ==============================================================================

# Note for developers/agents: In user-facing text, a "plugin" is referred to as a "target".
print_fixed_help() {
    case "${SCROOGE_PUBLIC_COMMAND:-}" in
        run)
            printf '\n'
            printf '%s\n' "Usage: ./scrooge-alert run [--help] [--quiet] [--<target> ...]"
            printf '\n'
            printf '%s\n' "Check prices for every registered target or only selected targets."
            printf '\n'
            printf '%s\n' "Options:"
            printf '%s\n' "  --help            Show this help message and exit"
            printf '%s\n' "  --quiet           Run with no console output"
            return
            ;;
        ping)
            printf '\n%s\n\n' "Usage: ./scrooge-alert ping [--help]"
            printf '%s\n\n' "Send a test notification through configured endpoints."
            printf '%s\n' "Options:"
            printf '%s\n\n' "  --help            Show this help message and exit"
            return
            ;;
        status)
            printf '\n%s\n\n' "Usage: ./scrooge-alert status [--help]"
            printf '%s\n\n' "Inspect configuration, updates, and background services."
            printf '%s\n' "Options:"
            printf '%s\n\n' "  --help            Show this help message and exit"
            return
            ;;
    esac
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
    case "${SCROOGE_PUBLIC_COMMAND:-}" in ping|status) return ;; esac
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
    printf '%s\n' "Run ./scrooge-alert install, then rerun this help command to list registered targets."
    printf '\n'
}

run_failure() {
    _rf_section="$1"
    _rf_message="$2"
    _rf_detail="${3:-}"
    _rf_recovery="${4:-}"

    begin_operational_output
    section_heading success "$_rf_section"
    task_status failure "$_rf_message"
    if [ -n "$_rf_detail" ]; then
        task_status info "$_rf_detail"
    fi
    if [ -n "$_rf_recovery" ]; then
        task_status warning "$_rf_recovery"
    fi
    end_operational_output
    exit 1
}

argument_failure() {
    _af_command="${SCROOGE_PUBLIC_COMMAND:-run}"
    run_failure \
        "Run arguments" \
        "$1" \
        "" \
        "Run $(command_text "./scrooge-alert $_af_command --help") to view supported options."
}

catalog_failure() {
    if _cf_output="$(catalog_cli diagnose 2>&1)"; then
        _cf_recovery="The target catalog is readable now; retry the command."
    else
        _cf_recovery="Fix (or remove) the offending package under src/core/scrapers/plugins/, then retry."
    fi
    _cf_detail="$(
        printf '%s\n' "$_cf_output" |
            awk 'NF { sub(/^[[:space:]]+/, ""); print; exit }'
    )"
    run_failure \
        "Run preflight" \
        "The target catalog could not be loaded." \
        "$_cf_detail" \
        "$_cf_recovery"
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

if [ -L "$BASE_DIR/venv" ]; then
    run_failure \
        "Run preflight" \
        "The project venv path must be a project-owned directory, not a symlink." \
        "" \
        "Remove the venv symlink, then recreate it with ./scripts/dev/setup.sh or $(command_text './scrooge-alert install')."
fi
if [ ! -x "$VENV_PYTHON" ]; then
    run_failure \
        "Run preflight" \
        "The project Python environment is missing or unusable." \
        "" \
        "Run $(command_text './scrooge-alert install'), then retry."
fi
if ! RUN_PYTHON_VERSION="$(
    "$VENV_PYTHON" -c \
        'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null
)"; then
    run_failure \
        "Run preflight" \
        "The project Python environment could not be executed." \
        "" \
        "Run $(command_text './scrooge-alert install'), then retry."
fi
if ! "$VENV_PYTHON" -c \
    'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
    >/dev/null 2>&1; then
    [ -n "$RUN_PYTHON_VERSION" ] || RUN_PYTHON_VERSION="unknown"
    run_failure \
        "Run preflight" \
        "Python $RUN_PYTHON_VERSION is unsupported; Python 3.10 or newer is required." \
        "" \
        "Install a supported Python, run $(command_text './scrooge-alert install'), then retry."
fi

# Registered plugins (one --<plugin> flag is accepted per registered scraper).
if load_plugin_catalog; then
    CATALOG_AVAILABLE=1
fi
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
            argument_failure "Invalid argument: $1."
            ;;
        --*)
            # Any registered plugin (e.g. --skroutz) selects that scraper and is
            # forwarded to main.py, which builds a matching flag per plugin.
            name="${1#--}"
            if ! is_valid_target "$name"; then
                argument_failure \
                    "Invalid target flag: $1 (expected --<snake_case target>)."
            fi
            if stream_contains "$name" "$PLUGINS"; then
                FLAG_PLUGIN=$((FLAG_PLUGIN + 1))
                append_forward_arg "$1"
            else
                if [ "$CATALOG_AVAILABLE" -eq 0 ]; then
                    catalog_failure
                fi
                argument_failure "Unknown target flag: $1."
            fi
            ;;
        *)
            argument_failure "Invalid argument: $1."
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
        argument_failure "The --ping flag must be used alone."
    fi
fi

# Check --status rules
if [ "$FLAG_STATUS" -gt 0 ]; then
    if [ "$TOTAL_FLAGS" -gt 1 ]; then
        argument_failure "The --status flag must be used alone."
    fi
fi

exec_with_forward_args 1
