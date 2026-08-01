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
        printf '%s\n\n' "Usage: ./scrooge-alert ping [--help]"
    else
        printf '%s\n\n' "Usage: ping.sh [-h]"
    fi
    printf '%s\n\n' "Send a test notification through configured endpoints."
    printf '%s\n' "Options:"
    if [ "${SCROOGE_PUBLIC_COMMAND:-}" = ping ]; then
        printf '%s\n\n' "  --help            Show this help message and exit"
    else
        printf '%s\n\n' "  -h, --help        show this help message and exit"
    fi
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

if [ "$#" -gt 0 ]; then
    runtime_argument_failure ping "Invalid argument: $1."
fi

exec_runtime_entrypoint ping.py
