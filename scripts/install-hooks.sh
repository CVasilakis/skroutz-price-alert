#!/bin/sh
set -eu

PROJECT_ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/.." && pwd)"

if ! command -v git >/dev/null 2>&1 ||
    ! git -C "$PROJECT_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
    printf '%s\n' "Git hooks were not configured (no Git worktree found)."
    exit 0
fi

git -C "$PROJECT_ROOT" config --local core.hooksPath .githooks
printf '%s\n' "Git pre-push checks are enabled for this worktree."
