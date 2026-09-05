#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname -- "$SCRIPT_DIR")"

# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=scripts/lib/runtime.sh
. "$SCRIPT_DIR/lib/runtime.sh"

PLUGINS=""
CATALOG_AVAILABLE=0
# Unlike every other script's --debug, run.sh's is forwarded rather than interpreted:
# it selects run.py's file-log frontend, so this wrapper still sets no DEBUG_MODE and
# still has no shell-level debug mode of its own. These two record which of the pair
# was seen, because they conflict and the case ladder sees only one argument at a time.
QUIET_REQUESTED=0
DEBUG_REQUESTED=0

# Note for developers/agents: In user-facing text, a "plugin" is referred to as a "target".
print_fixed_help() {
    if [ "${SCROOGE_PUBLIC_COMMAND:-}" = run ]; then
        printf '\n'
        printf '%s\n' "Usage: ./scrooge-alert run [--help] [--quiet] [--debug] [--<target> ...]"
        printf '\n'
        printf '%s\n' "Check prices for every registered target or only selected targets."
        printf '\n'
        printf '%s\n' "Options:"
        printf '%s\n' "  --help            Show this help message and exit"
        printf '%s\n' "  --quiet           Run with no console output"
        printf '%s\n' "  --debug           Print the background log lines instead of the live panel"
        return
    fi
    printf '\n'
    printf '%s\n' "Usage: run.sh [-h] [--quiet] [--debug] [--<target> ...]"
    printf '\n'
    printf '%s\n' "Check prices for every registered target or only selected targets."
    printf '%s\n' "With no target flag, price-check every registered target."
    printf '\n'
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n' "  --quiet           Run script with no console output"
    printf '%s\n' "  --debug           Print the background log lines instead of the live panel"
}

print_help() {
    print_fixed_help
    _ph_old_ifs="$IFS"
    IFS='
'
    for plugin in $PLUGINS; do
        display_name="$(plugin_display_name "$plugin")"
        printf '  --%-15s Run exclusively the %s scraper\n' \
            "$plugin" "${display_name:-$plugin}"
    done
    IFS="$_ph_old_ifs"
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

HELP_REQUESTED=0
for raw_arg in "$@"; do
    case "$raw_arg" in -h|--help) HELP_REQUESTED=1 ;; esac
done
if [ "$HELP_REQUESTED" -eq 1 ] && [ ! -x "$VENV_PYTHON" ]; then
    print_missing_venv_help
    exit 0
fi

require_runtime_python

if load_plugin_catalog; then
    CATALOG_AVAILABLE=1
fi
PLUGINS="$(list_plugins || true)"
if [ "$HELP_REQUESTED" -eq 1 ]; then
    print_help
    exit 0
fi

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help)
            print_help
            exit 0
            ;;
        --quiet)
            QUIET_REQUESTED=1
            runtime_forward_arg --quiet
            ;;
        --debug)
            DEBUG_REQUESTED=1
            runtime_forward_arg --debug
            ;;
        --)
            runtime_argument_failure run "Invalid argument: $1."
            ;;
        --*)
            runtime_target_flag run "$1" "$PLUGINS" "$CATALOG_AVAILABLE"
            ;;
        *)
            runtime_argument_failure run "Invalid argument: $1."
            ;;
    esac
    shift
done

# Both flags reach argparse's mutually exclusive group, but run.py's diagnosis for it
# is a bare usage dump on exit 2; reject the pair here so the wrapper's own arguments
# fail in the project's wording and exit status, as its other rejections do.
if [ "$QUIET_REQUESTED" -eq 1 ] && [ "$DEBUG_REQUESTED" -eq 1 ]; then
    runtime_argument_failure run "Conflicting arguments: --quiet and --debug select different output modes."
fi

runtime_exec_forwarded run.py
