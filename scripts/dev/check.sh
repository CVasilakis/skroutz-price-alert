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
    printf '%s\n' \
        "Usage: ./scripts/dev/check.sh [-h] [--debug] [full|static|shell|tests]"
    printf '\n'
    printf '%s\n' "Run the project's non-mutating local/CI acceptance checks."
    printf '%s\n' "With no check mode, run the complete local pre-push gate."
    printf '\n'
    printf '%s\n' "Check modes:"
    printf '%s\n' "  full              run the complete local pre-push gate"
    printf '%s\n' "  static            run Ruff and basedpyright"
    printf '%s\n' "  shell             run ShellCheck and POSIX shell syntax checks"
    printf '%s\n' "  tests             run the full pytest suite"
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

CHECK_MODE=full
mode_count=0
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
        full|static|shell|tests)
            mode_count=$((mode_count + 1))
            CHECK_MODE="$argument"
            ;;
        *)
            [ -n "$invalid_argument" ] ||
                invalid_argument="Invalid argument: $argument"
            ;;
    esac
done

finish_checks() {
    end_operational_output
    exit "$1"
}

finish_check_failure() {
    _fcf_status="$1"
    if [ "$DEBUG_MODE" -eq 1 ]; then
        task_status warning "Review the underlying diagnostic above, then retry."
    else
        task_status warning \
            "Run ./scripts/dev/check.sh --debug $CHECK_MODE to inspect the failure."
    fi
    printf '\n'
    section_heading success "Check result"
    task_status failure "Requested checks failed."
    finish_checks "$_fcf_status"
}

check_failure() {
    _cf_status="$1"
    shift
    task_status failure "$*"
    finish_check_failure "$_cf_status"
}

begin_operational_output
if [ -n "$invalid_argument" ] || [ "$mode_count" -gt 1 ] ||
   [ "$debug_count" -gt 1 ]; then
    section_heading success "Check arguments"
    if [ -n "$invalid_argument" ]; then
        task_status failure "$invalid_argument"
    elif [ "$debug_count" -gt 1 ]; then
        task_status failure "Specify --debug at most once."
    else
        task_status failure "Select at most one check mode."
    fi
    task_status info "Run ./scripts/dev/check.sh --help for usage."
    finish_checks 2
fi

# CI runs this gate from a checkout that has no ./venv, so it selects the
# workflow interpreter through SCROOGE_CHECK_PYTHON. Not a public interface.
CHECK_PYTHON="${SCROOGE_CHECK_PYTHON:-$PROJECT_ROOT/venv/bin/python3}"

require_check_python() {
    if ! run_action reject_project_venv_symlink_for "$CHECK_PYTHON"; then
        check_failure 1 "The development venv path is a symlink."
    fi
    if ! run_action require_python_310 "$CHECK_PYTHON" "./scripts/dev/setup.sh"; then
        check_failure 127 \
            "Python 3.10 or newer is unavailable. Run ./scripts/dev/setup.sh first."
    fi
}

run_static() {
    section_heading success "Static analysis"
    require_check_python
    if run_with_progress "Running Ruff lint..." \
        run_action "$CHECK_PYTHON" -m ruff check \
        "$PROJECT_ROOT/src" "$PROJECT_ROOT/tests"; then
        task_status success "Ruff lint passed."
    else
        check_status=$?
        check_failure "$check_status" "Ruff lint failed."
    fi
    if run_with_progress "Checking Ruff formatting..." \
        run_action "$CHECK_PYTHON" -m ruff format --check \
        "$PROJECT_ROOT/src" "$PROJECT_ROOT/tests"; then
        task_status success "Ruff formatting passed."
    else
        check_status=$?
        check_failure "$check_status" "Ruff formatting failed."
    fi
    if (
        cd "$PROJECT_ROOT"
        run_with_progress "Running basedpyright..." \
            run_action "$CHECK_PYTHON" -m basedpyright src
    ); then
        task_status success "basedpyright passed."
    else
        check_status=$?
        check_failure "$check_status" "basedpyright failed."
    fi
}

# The three helpers below are run_shell's steps, split out only because
# run_action and run_with_progress take a command rather than a block. They
# exchange values through run_shell's variables — shellcheck_binary, dash_binary,
# and shell_paths — because run_action owns the command's streams: it discards
# stdout normally and mirrors it to the terminal in debug mode, so a helper
# cannot report a value by printing it. Read them as the body of run_shell.

# Invoked through run_action.
# shellcheck disable=SC2329
resolve_shellcheck() {
    # CI pins and selects its own binary through SCROOGE_SHELLCHECK so runner
    # versions cannot drift from the one pre-push checks exercise.
    shellcheck_binary="${SCROOGE_SHELLCHECK:-$PROJECT_ROOT/venv/bin/shellcheck}"
    if [ ! -x "$shellcheck_binary" ]; then
        shellcheck_binary="$(command -v shellcheck || true)"
    fi
    [ -n "$shellcheck_binary" ]
}

# Invoked through run_action.
# shellcheck disable=SC2329
enumerate_shell_paths() {
    # git ls-files lists only what is below the current directory and reports it
    # relative to that directory, so the gate is anchored to PROJECT_ROOT like
    # the static and test checks already are. Enumerating from wherever the
    # caller happened to stand would silently narrow the file list rather than
    # fail: from src/ it finds no shell file at all and still reports a pass.
    # The cd is a subshell because run_action runs this in the caller's shell.
    (cd "$PROJECT_ROOT" && git ls-files --cached --others --exclude-standard -z) \
        > "$shell_paths"
}

# Invoked through run_action.
# shellcheck disable=SC2329
validate_shell_paths() {
    # The enumerated paths are relative to PROJECT_ROOT, so resolve them there.
    # Keeping them relative rather than absolute preserves the paths ShellCheck
    # prints and the ones the debug dump above lists. The cd is a subshell
    # because run_action runs this in the caller's shell.
    # The child sh, not this parent script, must expand positional values.
    # shellcheck disable=SC2016
    (cd "$PROJECT_ROOT" && xargs -0 -n 1 sh -c '
            set -eu
            shellcheck_command="$1"
            dash_command="$2"
            path="${3:-}"
            [ -n "$path" ] && [ -f "$path" ] || exit 0
            case "$path" in
                *.sh) ;;
                *)
                    IFS= read -r first_line < "$path" || first_line=""
                    case "$first_line" in
                        "#!"*"/sh"*|"#!"*" env sh"*|"#!"*"/dash"*|"#!"*" env dash"*) ;;
                        *) exit 0 ;;
                    esac
                    ;;
            esac
            "$shellcheck_command" -x "$path"
            sh -n "$path"
            [ -z "$dash_command" ] || "$dash_command" -n "$path"
        ' sh "$shellcheck_binary" "$dash_binary") < "$shell_paths"
}

run_shell() {
    section_heading success "Shell validation"
    require_check_python
    if ! run_action resolve_shellcheck; then
        check_failure 127 \
            "ShellCheck is unavailable. Run ./scripts/dev/setup.sh first."
    fi
    dash_binary="$(command -v dash || true)"
    if [ -n "${CI:-}" ] && [ -z "$dash_binary" ]; then
        check_failure 127 "dash is required for shell syntax checks in CI."
    fi
    if ! run_captured git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree; then
        check_failure 1 "Shell checks require a Git worktree."
    fi
    if [ "$CAPTURED_COMMAND_OUTPUT" != "true" ]; then
        check_failure 1 "Shell checks require a Git worktree."
    fi

    shell_paths="$(mktemp "${TMPDIR:-/tmp}/scrooge-shell-paths.XXXXXX")"
    trap 'rm -f "$shell_paths"' 0 HUP INT TERM
    if run_action enumerate_shell_paths; then
        if [ "$DEBUG_MODE" -eq 1 ]; then
            # The enumerated list is NUL-separated for xargs -0 below. Print it
            # one path per line so the dump is readable and so the terminating
            # newline keeps the following task status on its own line.
            xargs -0 -n 1 printf '%s\n' < "$shell_paths" >&2
        fi
    else
        check_failure 1 "Could not enumerate shell files from Git."
    fi
    if run_with_progress "Running ShellCheck and POSIX syntax checks..." \
        run_action validate_shell_paths; then
        task_status success "ShellCheck passed."
        task_status success "POSIX syntax checks passed."
    else
        # xargs runs every remaining file after one fails and reports its own
        # aggregate status, so the parent cannot attribute the failure to a
        # specific file or to ShellCheck rather than the syntax pass. Name both
        # checks instead of guessing; the underlying output shown by --debug
        # already identifies the tool and the file.
        check_status=$?
        check_failure "$check_status" "ShellCheck or POSIX syntax checks failed."
    fi
    rm -f "$shell_paths"
    trap - 0 HUP INT TERM
}

# Not a selectable mode, and deliberately without require_check_python. The three
# modes above exist because CI selects them as separate jobs; there is no
# dependency job, since each CI job runs its own pip check against the
# environment it just built. This step is therefore local-gate only, reachable
# only from full, where run_static has already validated CHECK_PYTHON.
run_dependencies() {
    section_heading success "Dependencies"
    if run_with_progress "Checking installed dependencies..." \
        run_action "$CHECK_PYTHON" -m pip check; then
        task_status success "Installed dependencies are consistent."
    else
        check_status=$?
        check_failure "$check_status" "Installed dependencies are inconsistent."
    fi
}

# Invoked through run_captured.
# shellcheck disable=SC2329
run_pytest() {
    cd "$PROJECT_ROOT"
    # The parent run_captured call still mirrors pytest's complete output in
    # debug mode. Do not leak that mode into the test process itself: shell tests
    # must remain able to exercise and assert their normal/default behavior.
    DEBUG_MODE=0 SCROOGE_INTERNAL_DEBUG=0 "$CHECK_PYTHON" -m pytest
}

pytest_summary_count() {
    _psc_pattern="$1"
    printf '%s\n%s\n' "$CAPTURED_COMMAND_OUTPUT" "$CAPTURED_COMMAND_STDERR" |
        awk -v pattern="$_psc_pattern" '
            match($0, "[0-9]+ " pattern) {
                summary = substr($0, RSTART, RLENGTH)
                split(summary, fields, " ")
                count = fields[1]
            }
            END {
                if (count != "") {
                    print count
                }
            }
        '
}

report_pytest_count() {
    _rpc_status="$1"
    _rpc_count="$2"
    _rpc_singular="$3"
    _rpc_plural="$4"
    [ -n "$_rpc_count" ] || return 0
    if [ "$_rpc_count" -eq 1 ]; then
        task_status "$_rpc_status" "1 $_rpc_singular."
    else
        task_status "$_rpc_status" "$_rpc_count $_rpc_plural."
    fi
}

report_pytest_summary() {
    pytest_passed_count="$(pytest_summary_count 'passed')"
    pytest_warning_count="$(pytest_summary_count 'warnings?')"
    pytest_failed_count="$(pytest_summary_count 'failed')"
    pytest_error_count="$(pytest_summary_count 'errors?')"

    report_pytest_count success "$pytest_passed_count" "test passed" "tests passed"
    report_pytest_count warning "$pytest_warning_count" \
        "test warning" "test warnings"
    report_pytest_count failure "$pytest_failed_count" \
        "test failed" "tests failed"
    report_pytest_count failure "$pytest_error_count" "test error" "test errors"
}

run_tests() {
    section_heading success "Tests"
    require_check_python
    if run_with_progress "Running the full test suite..." \
        run_captured run_pytest; then
        report_pytest_summary
        if [ -z "$pytest_passed_count" ]; then
            task_status success "Tests passed."
        fi
    else
        check_status=$?
        report_pytest_summary
        if [ -z "$pytest_failed_count" ] && [ -z "$pytest_error_count" ]; then
            task_status failure "Tests failed."
        fi
        finish_check_failure "$check_status"
    fi
}

case "$CHECK_MODE" in
    static) run_static ;;
    shell) run_shell ;;
    tests) run_tests ;;
    full)
        run_static
        printf '\n'
        run_shell
        printf '\n'
        run_dependencies
        printf '\n'
        run_tests
        ;;
esac

printf '\n'
section_heading success "Check result"
task_status success "All requested checks passed."
finish_checks 0
