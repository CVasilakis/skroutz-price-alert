#!/bin/sh
set -eu

PROJECT_ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"
BASE_DIR="$PROJECT_ROOT"
# shellcheck source=scripts/lib/common.sh
. "$PROJECT_ROOT/scripts/lib/common.sh"

print_help() {
    printf '\n'
    printf '%s\n' "Usage: ./scripts/dev/install-hooks.sh [-h] [--debug]"
    printf '\n'
    printf '%s\n' "Enable the versioned pre-push checks for this Git worktree."
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

INVALID_ARGUMENT=''
for argument in "$@"; do
    case "$argument" in
        --debug)
            DEBUG_MODE=1
            SCROOGE_INTERNAL_DEBUG=1
            export DEBUG_MODE SCROOGE_INTERNAL_DEBUG
            ;;
        *)
            [ -n "$INVALID_ARGUMENT" ] || INVALID_ARGUMENT="$argument"
            ;;
    esac
done

case "${SCROOGE_INSTALL_HOOKS_CONTEXT:-standalone}" in
    setup) EMBEDDED_RUN=1 ;;
    *) EMBEDDED_RUN=0 ;;
esac

hook_section() {
    [ "$EMBEDDED_RUN" -eq 1 ] || section_heading success "Git hook setup"
}

# Wraps the shared renderer because setup.sh runs this script embedded, where
# hook progress belongs to the setup section rather than its own output.
hook_task() {
    [ "$EMBEDDED_RUN" -eq 0 ] || return 0
    task_status "$@"
}

finish_output() {
    [ "$EMBEDDED_RUN" -eq 1 ] || end_operational_output
}

hook_capture() {
    if [ "$DEBUG_MODE" -eq 1 ]; then
        run_captured "$@"
    else
        run_captured "$@" 2>/dev/null
    fi
}

if [ -n "$INVALID_ARGUMENT" ]; then
    [ "$EMBEDDED_RUN" -eq 1 ] || begin_operational_output
    hook_section
    hook_task failure "Invalid argument: $INVALID_ARGUMENT"
    hook_task info "Run ./scripts/dev/install-hooks.sh --help for usage."
    finish_output
    exit 1
fi

[ "$EMBEDDED_RUN" -eq 1 ] || begin_operational_output
hook_section

if ! run_action command -v git; then
    hook_task info "Git hooks were not configured (no Git worktree found)."
    finish_output
    exit 0
fi
if ! hook_capture git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree; then
    hook_task info "Git hooks were not configured (no Git worktree found)."
    finish_output
    exit 0
fi
if [ "$CAPTURED_COMMAND_OUTPUT" != "true" ]; then
    hook_task info "Git hooks were not configured (no Git worktree found)."
    finish_output
    exit 0
fi

HOOK="$PROJECT_ROOT/.githooks/pre-push"
if [ -L "$HOOK" ] || [ ! -f "$HOOK" ]; then
    hook_task failure "Cannot enable hooks; .githooks/pre-push is missing."
    hook_task info "Restore .githooks/pre-push, then retry."
    finish_output
    exit 1
fi
if ! run_action chmod +x "$HOOK"; then
    hook_task failure "Could not make .githooks/pre-push executable."
    hook_task info "Fix its ownership or permissions, then retry."
    finish_output
    exit 1
fi
if [ ! -x "$HOOK" ]; then
    hook_task failure ".githooks/pre-push is not executable."
    hook_task info "Fix its ownership or permissions, then retry."
    finish_output
    exit 1
fi
hook_task success "Pre-push hook is executable."

if ! run_action sh -n "$HOOK"; then
    hook_task failure ".githooks/pre-push has invalid POSIX shell syntax."
    hook_task info "Restore or repair .githooks/pre-push, then retry."
    finish_output
    exit 1
fi
hook_task success "Pre-push hook syntax is valid."

if run_action git -C "$PROJECT_ROOT" config --local core.hooksPath .githooks; then
    :
else
    config_status=$?
    hook_task failure "Could not configure the repository-local hooks path."
    hook_task info "Fix the worktree's .git/config permissions, then retry."
    finish_output
    exit "$config_status"
fi
if ! hook_capture git -C "$PROJECT_ROOT" config --local --get core.hooksPath; then
    hook_task failure "Git did not retain the repository-local hooks path."
    hook_task info "Fix the worktree's .git/config permissions, then retry."
    finish_output
    exit 1
fi
if [ "$CAPTURED_COMMAND_OUTPUT" != ".githooks" ]; then
    hook_task failure "Git did not retain the repository-local hooks path."
    hook_task info "Fix the worktree's .git/config permissions, then retry."
    finish_output
    exit 1
fi
hook_task success "Repository-local pre-push checks are enabled."
finish_output
