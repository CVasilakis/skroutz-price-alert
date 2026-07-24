#!/bin/sh
# Shared prerequisite checks. This file is sourced; entry points own set -eu.

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

    if ! _rp_version="$("$_rp_python" -c \
        'import sys; print(".".join(map(str, sys.version_info[:3])))' 2>/dev/null)"; then
        _rp_version="unusable"
    fi
    if ! "$_rp_python" -c \
        'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
        >/dev/null 2>&1; then
        [ -n "$_rp_version" ] || _rp_version="unknown"
        printf '%s\n' \
            "Error: Detected Python $_rp_version; Python 3.10 or newer is required." >&2
        printf '%s\n' "Run $_rp_setup after installing a supported Python." >&2
        return 1
    fi
}

require_git_worktree() {
    require_command git Git || return 1
    if ! _rgw_inside="$(git -C "$BASE_DIR" rev-parse --is-inside-work-tree 2>/dev/null)" ||
       [ "$_rgw_inside" != "true" ]; then
        printf '%s\n' "Error: $BASE_DIR is not a Git worktree." >&2
        return 1
    fi
    if ! _rgw_bare="$(git -C "$BASE_DIR" rev-parse --is-bare-repository 2>/dev/null)" ||
       [ "$_rgw_bare" != "false" ]; then
        printf '%s\n' "Error: $BASE_DIR is a bare or unusable Git repository." >&2
        return 1
    fi
}

require_clean_worktree() {
    if ! _rcw_status="$(git -C "$BASE_DIR" status --porcelain --untracked-files=normal)"; then
        printf '%s\n' "Error: Could not inspect the Git working tree." >&2
        return 1
    fi
    if [ -n "$_rcw_status" ]; then
        printf '%s\n' "Error: The working tree contains tracked changes or nonignored untracked files." >&2
        printf '%s\n' "Commit or stash your work before running update.sh; nothing was changed." >&2
        return 1
    fi
}

require_main_branch() {
    if ! _rmb_branch="$(git -C "$BASE_DIR" symbolic-ref --quiet --short HEAD 2>/dev/null)"; then
        printf '%s\n' "Error: The checkout is in detached-HEAD state; update.sh requires branch 'main'." >&2
        return 1
    fi
    if [ "$_rmb_branch" != "main" ]; then
        printf '%s\n' "Error: update.sh requires branch 'main' (current branch: '$_rmb_branch')." >&2
        printf '%s\n' "Switch branches yourself after saving any work, then retry." >&2
        return 1
    fi
}

require_origin_remote() {
    if ! git -C "$BASE_DIR" remote get-url origin >/dev/null 2>&1; then
        printf '%s\n' "Error: Git remote 'origin' is missing or unusable." >&2
        return 1
    fi
}

require_origin_main() {
    require_origin_remote || return 1
    if ! git -C "$BASE_DIR" rev-parse --verify --quiet \
        'refs/remotes/origin/main^{commit}' >/dev/null; then
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
        if ! git -C "$BASE_DIR" cat-file -e "$_rrp_revision:$_rrp_path" 2>/dev/null; then
            printf '%s\n' \
                "Error: Fetched revision $_rrp_revision is missing required file '$_rrp_path'." >&2
            return 1
        fi
    done
}
