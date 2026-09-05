#!/bin/sh
set -eu

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "$0")" >/dev/null 2>&1 && pwd)"
BASE_DIR="$(dirname -- "$SCRIPT_DIR")"

# shellcheck source=scripts/lib/common.sh
. "$SCRIPT_DIR/lib/common.sh"
# shellcheck source=scripts/lib/systemd.sh
. "$SCRIPT_DIR/lib/systemd.sh"
# shellcheck source=scripts/lib/runtime.sh
. "$SCRIPT_DIR/lib/runtime.sh"

PLUGINS=""
KNOWN=""
CATALOG_AVAILABLE=0

# Note for developers/agents: In user-facing text, a "plugin" is referred to as a "target".
print_fixed_help() {
    if [ "${SCROOGE_PUBLIC_COMMAND:-}" = status ]; then
        printf '\n'
        printf '%s\n' "Usage: ./scrooge-alert status [--help] [--<target> ...]"
        printf '\n'
        printf '%s\n' "Inspect configuration, updates, and background services."
        printf '\n'
        printf '%s\n' "Options:"
        printf '%s\n' "  --help            Show this help message and exit"
        return
    fi
    printf '\n'
    printf '%s\n' "Usage: status.sh [-h] [--<target> ...]"
    printf '\n'
    printf '%s\n' "Inspect configuration, updates, and background services."
    printf '%s\n' "With no target flag, report every known target."
    printf '\n'
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
}

# Known targets are the registered plugins plus any installed-but-unregistered
# ones, so an orphaned unit stays selectable instead of being reportable only
# through the unfiltered run.
print_help() {
    print_fixed_help
    _ph_old_ifs="$IFS"
    IFS='
'
    for target in $KNOWN; do
        if stream_contains "$target" "$PLUGINS"; then
            display_name="$(plugin_display_name "$target")"
            printf '  --%-15s Show status for the %s scraper\n' \
                "$target" "${display_name:-$target}"
        else
            printf '  --%-15s Show the orphaned %s units\n' "$target" "$target"
        fi
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
# A malformed unit name is reported by the status report itself; it must not
# stop this read-only command from parsing its arguments.
INSTALLED="$(list_installed_targets 2>/dev/null || true)"
KNOWN="$(stream_union "$PLUGINS" "$INSTALLED")"
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
        --)
            runtime_argument_failure status "Invalid argument: $1."
            ;;
        --*)
            runtime_target_flag status "$1" "$KNOWN" "$CATALOG_AVAILABLE"
            ;;
        *)
            runtime_argument_failure status "Invalid argument: $1."
            ;;
    esac
    shift
done

runtime_exec_forwarded status.py
