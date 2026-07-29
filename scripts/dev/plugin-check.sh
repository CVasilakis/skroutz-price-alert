#!/bin/sh
set -eu

PROJECT_ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
BASE_DIR="$PROJECT_ROOT"
. "$PROJECT_ROOT/scripts/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$PROJECT_ROOT/scripts/lib/preflight.sh"

print_help() {
    printf '\n%s\n\n' "Usage: ./scripts/dev/plugin-check.sh [-h] [--debug] --<target>"
    printf '%s\n\n' "Verify one target against its source, tests, and private dependencies."
    printf '%s\n' "Required arguments:"
    printf '%s\n\n' "  --<target>        target to verify (for example, --skroutz)"
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n\n' "  --debug           show underlying command output"
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

target=''
target_count=0
debug_count=0
invalid_argument=''
for argument in "$@"; do
    case "$argument" in
        --debug)
            debug_count=$((debug_count + 1))
            DEBUG_MODE=1
            SCROOGE_INTERNAL_DEBUG=1
            export DEBUG_MODE SCROOGE_INTERNAL_DEBUG
            ;;
        --?*)
            target_count=$((target_count + 1))
            target="${argument#--}"
            if ! is_valid_target "$target"; then
                invalid_argument="Invalid target '$target' (expected a nonblank snake_case name)."
            fi
            ;;
        *)
            [ -n "$invalid_argument" ] ||
                invalid_argument="Invalid argument: $argument"
            ;;
    esac
done

finish_verification() {
    end_operational_output
    exit "$1"
}

verification_failure() {
    _vf_status="$1"
    shift
    task_status failure "[$target] $1"
    if [ "$DEBUG_MODE" -eq 1 ]; then
        task_status warning "Review the underlying diagnostic above, then retry."
    else
        task_status warning \
            "Run ./scripts/dev/plugin-check.sh --debug --$target to inspect the failure."
    fi
    printf '\n'
    section_heading success "Verification result"
    task_status failure "[$target] Target verification failed."
    finish_verification "$_vf_status"
}

begin_operational_output
if [ -n "$invalid_argument" ] || [ "$target_count" -ne 1 ] ||
   [ "$debug_count" -gt 1 ]; then
    section_heading success "Verification arguments"
    if [ -n "$invalid_argument" ]; then
        task_status failure "$invalid_argument"
    elif [ "$debug_count" -gt 1 ]; then
        task_status failure "Specify --debug at most once."
    else
        task_status failure "Select exactly one target."
    fi
    task_status info "Run ./scripts/dev/plugin-check.sh --help for usage."
    finish_verification 2
fi

section_heading success "Target verification"
if [ -z "${SCROOGE_PLUGIN_CHECK_PYTHON:-}" ]; then
    if ! run_action reject_project_venv_symlink; then
        verification_failure 1 "The development venv path is a symlink."
    fi
fi
plugin_check_python="${SCROOGE_PLUGIN_CHECK_PYTHON:-$BASE_DIR/venv/bin/python3}"
if ! run_action require_python_310 "$plugin_check_python" "./scripts/dev/setup.sh"; then
    verification_failure 127 "Python 3.10 or newer is unavailable."
fi
plugin_check_python="$(
    CDPATH='' cd -- "$(dirname -- "$plugin_check_python")" && pwd
)/$(basename -- "$plugin_check_python")"
plugin_check_venv_dir="$(dirname -- "$(dirname -- "$plugin_check_python")")"
[ "$(basename -- "$plugin_check_venv_dir")" = "venv" ] || {
    verification_failure 2 \
        "The selected Python does not belong to a virtual environment named venv."
}
plugin_check_venv_parent="$(dirname -- "$plugin_check_venv_dir")"

if run_with_progress "[$target] Checking the source and dependency contract..." \
    run_action env PYTHONPATH="$BASE_DIR/src" "$plugin_check_python" \
    -m core.scrapers.tooling.cli plugin-check "$target"; then
    task_status success "[$target] Source and dependency contract passed."
else
    verification_status=$?
    verification_failure "$verification_status" "Source and dependency contract failed."
fi

printf '\n'
section_heading success "Target tests"
if run_with_progress "[$target] Running target tests..." \
    run_action "$plugin_check_python" -m pytest --no-cov \
    "$BASE_DIR/tests/plugins/$target"; then
    task_status success "[$target] Tests passed."
else
    verification_status=$?
    verification_failure "$verification_status" "Tests failed."
fi

printf '\n'
section_heading success "Static analysis"
if run_with_progress "[$target] Running type checking..." \
    run_action "$plugin_check_python" -m basedpyright \
    --venvpath "$plugin_check_venv_parent" \
    "$BASE_DIR/src/core/scrapers/plugins/$target"; then
    task_status success "[$target] Type checking passed."
else
    verification_status=$?
    verification_failure "$verification_status" "Type checking failed."
fi
if run_with_progress "[$target] Running Ruff lint..." \
    run_action "$plugin_check_python" -m ruff check \
    "$BASE_DIR/src/core/scrapers/plugins/$target" "$BASE_DIR/tests/plugins/$target"; then
    task_status success "[$target] Ruff lint passed."
else
    verification_status=$?
    verification_failure "$verification_status" "Ruff lint failed."
fi
if run_with_progress "[$target] Checking Ruff formatting..." \
    run_action "$plugin_check_python" -m ruff format --check \
    "$BASE_DIR/src/core/scrapers/plugins/$target" "$BASE_DIR/tests/plugins/$target"; then
    task_status success "[$target] Ruff formatting passed."
else
    verification_status=$?
    verification_failure "$verification_status" "Ruff formatting failed."
fi

printf '\n'
section_heading success "Verification result"
task_status success "[$target] Target verification complete."
finish_verification 0
