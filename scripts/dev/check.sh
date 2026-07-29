#!/bin/sh
set -eu

PROJECT_ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
BASE_DIR="$PROJECT_ROOT"
# shellcheck source=scripts/lib/common.sh
. "$PROJECT_ROOT/scripts/lib/common.sh"
# shellcheck source=scripts/lib/preflight.sh
. "$PROJECT_ROOT/scripts/lib/preflight.sh"

print_help() {
    printf '\n%s\n\n' \
        "Usage: ./scripts/dev/check.sh [-h] [--debug] [full|static|shell|tests]"
    printf '%s\n' "Run the project's non-mutating local/CI acceptance checks."
    printf '%s\n\n' "With no check mode, run the complete local pre-push gate."
    printf '%s\n' "Check modes:"
    printf '%s\n' "  full              run the complete local pre-push gate"
    printf '%s\n' "  static            run Ruff and basedpyright"
    printf '%s\n' "  shell             run ShellCheck and POSIX shell syntax checks"
    printf '%s\n\n' "  tests             run the full pytest suite"
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

check_failure() {
    _cf_status="$1"
    shift
    task_status failure "$*"
    if [ "$DEBUG_MODE" -eq 1 ]; then
        task_status warning "Review the underlying diagnostic above, then retry."
    else
        task_status warning \
            "Run ./scripts/dev/check.sh --debug $CHECK_MODE to inspect the failure."
    fi
    printf '\n'
    section_heading success "Check result"
    task_status failure "Requested checks failed."
    finish_checks "$_cf_status"
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

CHECK_PYTHON="${SCROOGE_CHECK_PYTHON:-$PROJECT_ROOT/venv/bin/python3}"

# Invoked through run_action.
# shellcheck disable=SC2329
require_python() {
    if [ -z "${SCROOGE_CHECK_PYTHON:-}" ]; then
        reject_project_venv_symlink || return 127
    fi
    require_python_310 "$CHECK_PYTHON" "./scripts/dev/setup.sh" || return 127
}

require_check_python() {
    if ! run_action require_python; then
        check_failure 127 \
            "Python 3.10 or newer is unavailable. Run ./scripts/dev/setup.sh first."
    fi
}

run_static() {
    section_heading success "Static analysis"
    require_check_python
    if run_action "$CHECK_PYTHON" -m ruff check \
        "$PROJECT_ROOT/src" "$PROJECT_ROOT/tests"; then
        task_status success "Ruff lint passed."
    else
        check_status=$?
        check_failure "$check_status" "Ruff lint failed."
    fi
    if run_action "$CHECK_PYTHON" -m ruff format --check \
        "$PROJECT_ROOT/src" "$PROJECT_ROOT/tests"; then
        task_status success "Ruff formatting passed."
    else
        check_status=$?
        check_failure "$check_status" "Ruff formatting failed."
    fi
    if (
        cd "$PROJECT_ROOT"
        run_action "$CHECK_PYTHON" -m basedpyright src
    ); then
        task_status success "basedpyright passed."
    else
        check_status=$?
        check_failure "$check_status" "basedpyright failed."
    fi
}

# Invoked through run_action.
# shellcheck disable=SC2329
resolve_shellcheck() {
    shellcheck_binary="${SCROOGE_SHELLCHECK:-$PROJECT_ROOT/venv/bin/shellcheck}"
    if [ ! -x "$shellcheck_binary" ]; then
        shellcheck_binary="$(command -v shellcheck || true)"
    fi
    [ -n "$shellcheck_binary" ]
}

# Invoked through run_action.
# shellcheck disable=SC2329
enumerate_shell_paths() {
    git ls-files --cached --others --exclude-standard -z > "$shell_paths"
}

# Invoked through run_action.
# shellcheck disable=SC2329
validate_shell_paths() {
    # The child sh, not this parent script, must expand positional values.
    # shellcheck disable=SC2016
    xargs -0 -n 1 sh -c '
            set -eu
            shellcheck_command="$1"
            dash_command="$2"
            stage_file="$3"
            path="${4:-}"
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
            printf "%s\n" "shellcheck" > "$stage_file"
            "$shellcheck_command" -x "$path"
            printf "%s\n" "syntax" > "$stage_file"
            sh -n "$path"
            [ -z "$dash_command" ] || "$dash_command" -n "$path"
        ' sh "$shellcheck_binary" "$dash_binary" "$shell_stage" < "$shell_paths"
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
    if ! run_captured git rev-parse --is-inside-work-tree; then
        check_failure 1 "Shell checks require a Git worktree."
    fi
    if [ "$CAPTURED_COMMAND_OUTPUT" != "true" ]; then
        check_failure 1 "Shell checks require a Git worktree."
    fi

    shell_paths="$(mktemp "${TMPDIR:-/tmp}/scrooge-shell-paths.XXXXXX")"
    trap 'rm -f "$shell_paths"' 0 HUP INT TERM
    shell_stage="$(mktemp "${TMPDIR:-/tmp}/scrooge-shell-stage.XXXXXX")"
    trap 'rm -f "$shell_paths" "$shell_stage"' 0 HUP INT TERM
    if run_action enumerate_shell_paths; then
        if [ "$DEBUG_MODE" -eq 1 ]; then
            cat "$shell_paths" >&2
        fi
    else
        check_failure 1 "Could not enumerate shell files from Git."
    fi
    if run_action validate_shell_paths; then
        task_status success "ShellCheck passed."
        task_status success "POSIX syntax checks passed."
    else
        check_status=$?
        if [ "$(cat "$shell_stage")" = "syntax" ]; then
            check_failure "$check_status" "POSIX syntax checks failed."
        fi
        check_failure "$check_status" "ShellCheck failed."
    fi
    rm -f "$shell_paths" "$shell_stage"
    trap - 0 HUP INT TERM
}

run_dependencies() {
    section_heading success "Dependencies"
    if run_action "$CHECK_PYTHON" -m pip check; then
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
    "$CHECK_PYTHON" -m pytest
}

run_tests() {
    section_heading success "Tests"
    require_check_python
    if run_captured run_pytest; then
        test_count="$(
            printf '%s\n' "$CAPTURED_COMMAND_OUTPUT" |
                awk '
                    match($0, /[0-9]+ passed/) {
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
        )"
        if [ -n "$test_count" ]; then
            task_status success "$test_count tests passed."
        else
            task_status success "Tests passed."
        fi
    else
        check_status=$?
        check_failure "$check_status" "Tests failed."
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
