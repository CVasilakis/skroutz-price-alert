#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname -- "$SCRIPT_DIR")"

# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=scripts/lib/runtime.sh
. "$SCRIPT_DIR/lib/runtime.sh"

print_help() {
    printf '\n'
    if [ "${SCROOGE_PUBLIC_COMMAND:-}" = ping ]; then
        printf '%s\n' "Usage: ./scrooge-alert ping [--help]"
    else
        printf '%s\n' "Usage: ping.sh [-h]"
    fi
    printf '\n'
    printf '%s\n' "Send a test notification through configured endpoints."
    printf '\n'
    help_options_block ping
    printf '\n'
}

for raw_arg in "$@"; do
    case "$raw_arg" in
        -h|--help)
            print_help
            exit 0
            ;;
    esac
done

require_runtime_python

# ping takes no options at all, --debug included: ping.py owns its TUI, runtime
# diagnostics, and logging, so this wrapper has no shell-level debug mode to offer
# and no target flags to validate.
if [ "$#" -gt 0 ]; then
    runtime_argument_failure ping "Invalid argument: $1."
fi

exec_runtime_entrypoint ping.py
