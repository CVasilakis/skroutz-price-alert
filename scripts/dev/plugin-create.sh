#!/bin/sh
set -eu

PROJECT_ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
BASE_DIR="$PROJECT_ROOT"
# shellcheck source=scripts/lib/common.sh
. "$PROJECT_ROOT/scripts/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$PROJECT_ROOT/scripts/lib/preflight.sh"

print_help() {
    printf '\n%s\n' \
        "Usage: ./scripts/dev/plugin-create.sh [-h] [--debug] <target> --display-name <name>"
    printf '%s\n\n' "       --domain <domain> --url-prefix <prefix>"
    printf '%s\n\n' "Create an additive in-repository scraper target scaffold."
    printf '%s\n' "Required arguments:"
    printf '%s\n' "  <target>                  non-reserved snake_case target name"
    printf '%s\n' "  --display-name <name>     user-facing store name"
    printf '%s\n' "  --domain <domain>         supported hostname or IP address"
    printf '%s\n\n' "  --url-prefix <prefix>     URL path prefix beginning with /"
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help                show this help message and exit"
    printf '%s\n\n' "  --debug                   show underlying command output"
}

HELP_REQUESTED=0
for argument in "$@"; do
    case "$argument" in
        -h|--help) HELP_REQUESTED=1 ;;
    esac
done
if [ "$HELP_REQUESTED" -eq 1 ]; then
    print_help
    exit 0
fi

remaining=$#
while [ "$remaining" -gt 0 ]; do
    argument=$1
    shift
    if [ "$argument" = "--debug" ]; then
        DEBUG_MODE=1
        SCROOGE_INTERNAL_DEBUG=1
        export DEBUG_MODE SCROOGE_INTERNAL_DEBUG
    else
        set -- "$@" "$argument"
    fi
    remaining=$((remaining - 1))
done

scaffold_finish() {
    end_operational_output
    exit "$1"
}

scaffold_detail() {
    _sd_details="$1"
    _sd_shown=0
    _sd_old_ifs="$IFS"
    IFS='
'
    # shellcheck disable=SC2086  # deliberate newline-only diagnostic iteration
    for _sd_line in $_sd_details; do
        case "$_sd_line" in
            "Target scaffold failed:"*|usage:*|"./scripts/dev/plugin-create.sh: error:"*)
                task_status info "$_sd_line"
                _sd_shown=1
                ;;
        esac
    done
    IFS="$_sd_old_ifs"
    if [ "$_sd_shown" -eq 0 ]; then
        if [ "$DEBUG_MODE" -eq 1 ]; then
            task_status info "Review the underlying diagnostic above, then retry."
        else
            task_status info \
                "Run ./scripts/dev/plugin-create.sh --debug to inspect the failure."
        fi
    fi
}

begin_operational_output
section_heading success "Target scaffold"
if ! run_action require_python_310 python3 "./scripts/dev/setup.sh"; then
    task_status failure "System Python 3.10 or newer is required."
    task_status info "Install a supported Python, then run ./scripts/dev/setup.sh."
    scaffold_finish 127
fi

if PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
    run_with_progress "Creating the target scaffold..." \
        run_captured python3 -m core.scrapers.tooling.scaffold --shell-output "$@"; then
    scaffold_status=0
else
    scaffold_status=$?
fi

if [ "$scaffold_status" -ne 0 ]; then
    task_status failure "Target scaffold could not be created."
    scaffold_detail "$CAPTURED_COMMAND_STDERR"
    scaffold_finish "$scaffold_status"
fi

tab="$(printf '\t')"
scaffold_target="${CAPTURED_COMMAND_OUTPUT##*"$tab"}"
expected_result="scaffold${tab}1${tab}${scaffold_target}"
if [ "$CAPTURED_COMMAND_OUTPUT" != "$expected_result" ] ||
   ! is_valid_target "$scaffold_target"; then
    task_status failure "Target scaffold returned an invalid result."
    task_status info "Run ./scripts/dev/plugin-create.sh --debug to inspect the failure."
    scaffold_finish 1
fi

task_status success "[$scaffold_target] Created the target source package."
task_status success "[$scaffold_target] Created the target test package."
printf '\n'
section_heading success "Next steps"
task_status info "Run ./scripts/dev/setup.sh --$scaffold_target."
task_status info "Run ./scripts/dev/plugin-check.sh --$scaffold_target."
printf '\n'
section_heading success "Scaffold result"
task_status success "[$scaffold_target] Target scaffold created."
scaffold_finish 0
