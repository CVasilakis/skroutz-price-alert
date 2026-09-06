#!/bin/sh
set -eu

PROJECT_ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
BASE_DIR="$PROJECT_ROOT"
# shellcheck source=scripts/lib/common.sh
. "$PROJECT_ROOT/scripts/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$PROJECT_ROOT/scripts/lib/preflight.sh"

print_help() {
    printf '\n'
    printf '%s\n' "Usage: ./scripts/dev/plugin-check.sh [-h] [--debug] --<target>"
    printf '\n'
    printf '%s\n' "Verify one target against its source, optional tests, and private dependencies."
    printf '%s\n' "Missing tests produce a warning; existing test failures still block."
    printf '\n'
    printf '%s\n' "Required arguments:"
    printf '%s\n' "  --<target>        target to verify (for example, --skroutz)"
    printf '\n'
    printf '%s\n' "Optional arguments:"
    printf '%s\n' "  -h, --help        show this help message and exit"
    printf '%s\n' "  --debug           show underlying command output"
    printf '\n'
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

verification_detail() {
    _vd_details="$1"
    _vd_match=''
    _vd_old_ifs="$IFS"
    IFS='
'
    for _vd_line in $_vd_details; do
        case "$_vd_line" in
            "Plugin check failed:"*) _vd_match="$_vd_line" ;;
        esac
    done
    IFS="$_vd_old_ifs"
    [ -n "$_vd_match" ] || return 1
    task_status info "$_vd_match"
}

verification_failure() {
    _vf_status="$1"
    shift
    _vf_summary="$1"
    _vf_detail="${2:-}"
    task_status failure "[$target] $_vf_summary"
    if [ "$DEBUG_MODE" -eq 0 ] && verification_detail "$_vf_detail"; then
        :
    elif [ "$DEBUG_MODE" -eq 1 ]; then
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

# cd for the same sys.path reason documented at catalog_cli in lib/common.sh.
# Invoked indirectly through run_captured.
# shellcheck disable=SC2329
source_contract_check() {
    (
        CDPATH='' cd -- "$BASE_DIR" || exit 1
        PYTHONPATH="$BASE_DIR/src" \
            exec "$plugin_check_python" -m core.scrapers.tooling.cli plugin-check "$target"
    )
}

# Invoked indirectly through run_with_progress.
# shellcheck disable=SC2329
run_source_contract_check() {
    run_captured source_contract_check
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
# CI verifies each plugin against a throwaway venv holding only that plugin's
# declared dependencies, selected through SCROOGE_PLUGIN_CHECK_PYTHON.
plugin_check_python="${SCROOGE_PLUGIN_CHECK_PYTHON:-$BASE_DIR/venv/bin/python3}"
if ! run_action reject_project_venv_symlink_for "$plugin_check_python"; then
    verification_failure 1 "The development venv path is a symlink."
fi
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
    run_source_contract_check; then
    contract_report="$CAPTURED_COMMAND_OUTPUT"
    task_status success "[$target] Source and dependency contract passed."
else
    verification_status=$?
    verification_failure "$verification_status" "Source and dependency contract failed." \
        "${CAPTURED_COMMAND_STDERR:-}"
fi

plugin_has_tests=''
plugin_warnings=0
tab="$(printf '\t')"
old_ifs="$IFS"
IFS='
'
for report_row in $contract_report; do
    report_kind="${report_row%%"$tab"*}"
    report_value="${report_row#*"$tab"}"
    case "$report_kind" in
        ok) : ;;
        tests)
            if [ -n "$plugin_has_tests" ] ||
               { [ "$report_value" != "0" ] && [ "$report_value" != "1" ]; }; then
                IFS="$old_ifs"
                verification_failure 1 "Source contract returned an invalid test report."
            fi
            plugin_has_tests="$report_value"
            ;;
        warning)
            plugin_warnings=$((plugin_warnings + 1))
            task_status warning "[$target] $report_value"
            ;;
        *) : ;; # debug/test harness diagnostics are not part of the TSV report
    esac
done
IFS="$old_ifs"
if [ -z "$plugin_has_tests" ]; then
    verification_failure 1 "Source contract did not report test availability."
fi

printf '\n'
section_heading success "Target tests"
if [ "$plugin_has_tests" -eq 0 ]; then
    task_status warning "[$target] No target tests to run; continuing with source checks."
else
    if run_with_progress "[$target] Running target tests..." \
        run_action "$plugin_check_python" -m pytest --no-cov \
        "$BASE_DIR/tests/plugins/$target"; then
        task_status success "[$target] Tests passed."
    else
        verification_status=$?
        verification_failure "$verification_status" "Tests failed."
    fi
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
# Argv holds the two Ruff invocations' shared path list; this script's own
# arguments were consumed into scalars by the argument scan above.
set -- "$BASE_DIR/src/core/scrapers/plugins/$target"
if [ "$plugin_has_tests" -eq 1 ]; then
    set -- "$@" "$BASE_DIR/tests/plugins/$target"
fi
if run_with_progress "[$target] Running Ruff lint..." \
    run_action "$plugin_check_python" -m ruff check "$@"; then
    task_status success "[$target] Ruff lint passed."
else
    verification_status=$?
    verification_failure "$verification_status" "Ruff lint failed."
fi
if run_with_progress "[$target] Checking Ruff formatting..." \
    run_action "$plugin_check_python" -m ruff format --check "$@"; then
    task_status success "[$target] Ruff formatting passed."
else
    verification_status=$?
    verification_failure "$verification_status" "Ruff formatting failed."
fi

printf '\n'
section_heading success "Verification result"
if [ "$plugin_warnings" -gt 0 ]; then
    task_status warning "[$target] Target verification complete with warnings."
else
    task_status success "[$target] Target verification complete."
fi
task_status info "Run ./scripts/dev/check.sh --debug before submitting."
finish_verification 0
