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
        "Usage: ./scripts/dev/plugin-create.sh"
    printf '%s\n' \
        "       ./scripts/dev/plugin-create.sh [-h] [--debug] <target> --display-name <name>"
    printf '%s\n' "       --domain <domain> [--domain <domain> ...] --url-prefix <prefix>"
    printf '%s\n' "       --result-type <price|listing> --default-interval <interval>"
    printf '%s\n\n' "       --transport <http|bare> <--with-tests|--without-tests> [options]"
    printf '%s\n' "With no arguments, launch the guided Rich wizard. Any argument selects"
    printf '%s\n\n' "strict non-interactive mode; all required choices must then be supplied."
    printf '%s\n' "Required arguments:"
    printf '%s\n' "  <target>                  non-reserved snake_case target name"
    printf '%s\n' "  --display-name <name>     user-facing store name"
    printf '%s\n' "  --domain <domain>         repeatable supported hostname or IP address"
    printf '%s\n' "  --url-prefix <prefix>     URL path prefix beginning with /"
    printf '%s\n' "  --result-type <type>      price or listing result scaffold"
    printf '%s\n' "  --default-interval <time> canonical framework execution interval"
    printf '%s\n' "  --transport <type>        shared http transport or bare client"
    printf '%s\n\n' "  --with-tests/--without-tests  explicitly choose example tests"
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  --required-item-field KEY TYPE EXAMPLE_JSON"
    printf '%s\n' "  --optional-item-field KEY TYPE DEFAULT_JSON EXAMPLE_JSON"
    printf '%s\n' "  --required-setting KEY TYPE EXAMPLE_JSON"
    printf '%s\n' "  --optional-setting KEY TYPE DEFAULT_JSON EXAMPLE_JSON"
    printf '%s\n' "  --sensitive-setting KEY  mark a declared custom setting sensitive"
    printf '%s\n' "  --dependency <requirement> add a private Python requirement"
    printf '%s\n' "                              TYPE: text, integer, number,"
    printf '%s\n' "                              nonnegative-number, boolean, text-list"
    printf '%s\n' "  -h, --help                show this help message and exit"
    printf '%s\n' "  --debug                   show underlying command output"
    printf '%s\n\n' \
        "Example field: --optional-item-field title_terms text-list '[]' '[\"Pixel\"]'"
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

ORIGINAL_ARGUMENT_COUNT=$#

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

if [ "$ORIGINAL_ARGUMENT_COUNT" -eq 0 ]; then
    if ! run_action reject_project_venv_symlink; then
        task_status failure "The development venv path is a symlink."
        task_status info "Recreate it with ./scripts/dev/setup.sh --debug."
        exit 1
    fi
    wizard_python="${SCROOGE_PLUGIN_CREATE_PYTHON:-$PROJECT_ROOT/venv/bin/python3}"
    if ! run_action require_python_310 "$wizard_python" "./scripts/dev/setup.sh --debug"; then
        task_status failure "The guided wizard requires the development venv."
        task_status info "Run ./scripts/dev/setup.sh --debug, then retry."
        exit 127
    fi
    if ! run_action "$wizard_python" -c 'import rich'; then
        task_status failure "The guided wizard requires the pinned Rich dependency."
        task_status info "Run ./scripts/dev/setup.sh --debug, then retry."
        exit 1
    fi
    PYTHONPATH="$PROJECT_ROOT/src${PYTHONPATH:+:$PYTHONPATH}" \
        "$wizard_python" -m core.scrapers.tooling.scaffold --interactive
    exit $?
fi

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
scaffold_tests="${CAPTURED_COMMAND_OUTPUT##*"$tab"}"
scaffold_without_tests="${CAPTURED_COMMAND_OUTPUT%"$tab"*}"
scaffold_target="${scaffold_without_tests##*"$tab"}"
expected_result="scaffold${tab}1${tab}${scaffold_target}${tab}${scaffold_tests}"
if [ "$CAPTURED_COMMAND_OUTPUT" != "$expected_result" ] ||
   ! is_valid_target "$scaffold_target" ||
   { [ "$scaffold_tests" != "0" ] && [ "$scaffold_tests" != "1" ]; }; then
    task_status failure "Target scaffold returned an invalid result."
    task_status info "Run ./scripts/dev/plugin-create.sh --debug to inspect the failure."
    scaffold_finish 1
fi

task_status success "[$scaffold_target] Created the target source package."
if [ "$scaffold_tests" -eq 1 ]; then
    task_status success "[$scaffold_target] Created the target test package."
else
    task_status warning "[$scaffold_target] Example tests were skipped."
fi
printf '\n'
section_heading success "Next steps"
task_status info "Run ./scripts/dev/setup.sh --$scaffold_target."
task_status info "Run ./scripts/dev/plugin-check.sh --$scaffold_target."
task_status info "Run ./scripts/dev/check.sh --debug before submitting."
printf '\n'
section_heading success "Scaffold result"
task_status success "[$scaffold_target] Target scaffold created."
scaffold_finish 0
