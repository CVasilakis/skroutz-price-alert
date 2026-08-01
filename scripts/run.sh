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

# Note for developers/agents: In user-facing text, a "plugin" is referred to as a "target".
print_fixed_help() {
    if [ "${SCROOGE_PUBLIC_COMMAND:-}" = run ]; then
        printf '\n'
        printf '%s\n' "Usage: ./scrooge-alert run [--help] [--quiet] [--<target> ...]"
        printf '\n'
        printf '%s\n' "Check prices for every registered target or only selected targets."
        printf '\n'
        printf '%s\n' "Options:"
        printf '%s\n' "  --help            Show this help message and exit"
        printf '%s\n' "  --quiet           Run with no console output"
        return
    fi
    printf '\n'
    printf '%s\n' "Usage: run.sh [-h] [--quiet] [--<target> ...]"
    printf '\n'
    printf '%s\n' "Check prices for every registered target or only selected targets."
    printf '%s\n' "With no target flag, price-check every registered target."
    printf '\n'
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n' "  --quiet           Run script with no console output"
}

print_help() {
    print_fixed_help
    for plugin in $PLUGINS; do
        display_name="$(plugin_display_name "$plugin")"
        printf '  --%-15s Run exclusively the %s scraper\n' \
            "$plugin" "${display_name:-$plugin}"
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
    runtime_failure \
        "Run preflight" \
        "The target catalog could not be loaded." \
        "$_cf_detail" \
        "$_cf_recovery"
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
        exec_runtime_entrypoint run.py "$@"
    fi
    _ewfa_value=''
    eval "_ewfa_value=\${FORWARD_$_ewfa_index}"
    exec_with_forward_args "$((_ewfa_index + 1))" "$@" "$_ewfa_value"
}

while [ "$#" -gt 0 ]; do
    case "$1" in
        -h|--help)
            print_help
            exit 0
            ;;
        --quiet)
            append_forward_arg --quiet
            ;;
        --)
            runtime_argument_failure run "Invalid argument: $1."
            ;;
        --*)
            name="${1#--}"
            if ! is_valid_target "$name"; then
                runtime_argument_failure run \
                    "Invalid target flag: $1 (expected --<snake_case target>)."
            fi
            if stream_contains "$name" "$PLUGINS"; then
                append_forward_arg "$1"
            else
                if [ "$CATALOG_AVAILABLE" -eq 0 ]; then
                    catalog_failure
                fi
                runtime_argument_failure run "Unknown target flag: $1."
            fi
            ;;
        *)
            runtime_argument_failure run "Invalid argument: $1."
            ;;
    esac
    shift
done

exec_with_forward_args 1
