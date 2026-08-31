#!/bin/sh
# Shared prerequisite checks. This file is sourced; entry points own set -eu.
# Source common.sh first: every check below reports through run_captured or
# run_action, and require_clean_worktree formats a command with command_text.

require_command() {
    _rc_command="$1"
    _rc_description="${2:-$_rc_command}"
    if ! command -v "$_rc_command" >/dev/null 2>&1; then
        printf '%s\n' "Error: $_rc_description is not installed or not available." >&2
        return 1
    fi
}

python_command_exists() {
    case "$1" in
        */*) [ -x "$1" ] ;;
        *) command -v "$1" >/dev/null 2>&1 ;;
    esac
}

# require_python_310 <interpreter> [setup-command]
require_python_310() {
    _rp_python="$1"
    _rp_setup="${2:-./scripts/dev/setup.sh}"
    if ! python_command_exists "$_rp_python"; then
        printf '%s\n' "Error: Python interpreter not found: $_rp_python." >&2
        printf '%s\n' "Python 3.10 or newer is required. Run $_rp_setup." >&2
        return 1
    fi

    if run_captured "$_rp_python" -c \
        'import sys; print(".".join(map(str, sys.version_info[:3])))'; then
        _rp_version="$CAPTURED_COMMAND_OUTPUT"
    else
        _rp_version="unusable"
    fi
    if ! run_action "$_rp_python" -c \
        'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)'; then
        [ -n "$_rp_version" ] || _rp_version="unknown"
        printf '%s\n' \
            "Error: Detected Python $_rp_version; Python 3.10 or newer is required." >&2
        printf '%s\n' "Run $_rp_setup after installing a supported Python." >&2
        return 1
    fi
}

# Every git check below runs its command through run_captured instead of a
# plain $(...) carrying its own redirections. run_captured is quiet by default
# and mirrors both streams in debug mode, so `./scrooge-alert update --debug`
# shows git's own diagnostic beside the verdict here, while a normal run stays
# silent because update.sh invokes these through run_update_helper, which
# discards both streams. Writing the suppression into the check itself would
# make the debug mode structurally unable to explain a refusal.
#
# require_fast_forward_to_origin is the deliberate exception: it captures no
# output and suppresses none, so it is already debug-transparent as written.
require_git_worktree() {
    require_command git Git || return 1
    if ! run_captured git -C "$BASE_DIR" rev-parse --is-inside-work-tree ||
       [ "$CAPTURED_COMMAND_OUTPUT" != "true" ]; then
        printf '%s\n' "Error: $BASE_DIR is not a Git worktree." >&2
        return 1
    fi
    if ! run_captured git -C "$BASE_DIR" rev-parse --is-bare-repository ||
       [ "$CAPTURED_COMMAND_OUTPUT" != "false" ]; then
        printf '%s\n' "Error: $BASE_DIR is a bare or unusable Git repository." >&2
        return 1
    fi
}

require_clean_worktree() {
    if ! run_captured git -C "$BASE_DIR" status \
        --porcelain --untracked-files=normal; then
        printf '%s\n' "Error: Could not inspect the Git working tree." >&2
        return 1
    fi
    if [ -n "$CAPTURED_COMMAND_OUTPUT" ]; then
        printf '%s\n' "Error: The working tree contains tracked changes or nonignored untracked files." >&2
        printf '%s\n' "Commit or stash your work before running $(command_text './scrooge-alert update'); nothing was changed." >&2
        return 1
    fi
}

require_main_branch() {
    if ! run_captured git -C "$BASE_DIR" symbolic-ref --short HEAD; then
        printf '%s\n' "Error: The checkout is in detached-HEAD state; $(command_text './scrooge-alert update') requires branch 'main'." >&2
        return 1
    fi
    if [ "$CAPTURED_COMMAND_OUTPUT" != "main" ]; then
        printf '%s\n' "Error: $(command_text './scrooge-alert update') requires branch 'main' (current branch: '$CAPTURED_COMMAND_OUTPUT')." >&2
        printf '%s\n' "Switch branches yourself after saving any work, then retry." >&2
        return 1
    fi
}

require_origin_remote() {
    if ! run_captured git -C "$BASE_DIR" remote get-url origin; then
        printf '%s\n' "Error: Git remote 'origin' is missing or unusable." >&2
        return 1
    fi
}

require_origin_main() {
    require_origin_remote || return 1
    if ! run_captured git -C "$BASE_DIR" rev-parse --verify \
        'refs/remotes/origin/main^{commit}'; then
        printf '%s\n' "Error: origin/main is missing or does not name a commit." >&2
        return 1
    fi
}

require_fast_forward_to_origin() {
    if git -C "$BASE_DIR" merge-base --is-ancestor HEAD origin/main; then
        return 0
    fi
    if git -C "$BASE_DIR" merge-base --is-ancestor origin/main HEAD; then
        printf '%s\n' "Error: Local main has commits that are not contained in origin/main." >&2
        printf '%s\n' "Publish or reconcile those commits before updating." >&2
    else
        printf '%s\n' "Error: Local main and origin/main have diverged." >&2
        printf '%s\n' "Reconcile the histories manually before updating." >&2
    fi
    return 1
}

require_revision_paths() {
    _rrp_revision="$1"
    shift
    for _rrp_path in "$@"; do
        if ! run_captured git -C "$BASE_DIR" cat-file -e \
            "$_rrp_revision:$_rrp_path"; then
            printf '%s\n' \
                "Error: Fetched revision $_rrp_revision is missing required file '$_rrp_path'." >&2
            return 1
        fi
    done
}
