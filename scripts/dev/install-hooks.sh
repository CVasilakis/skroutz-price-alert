#!/bin/sh
set -eu

PROJECT_ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")/../.." && pwd)"

if ! command -v git >/dev/null 2>&1 ||
    [ "$(git -C "$PROJECT_ROOT" rev-parse --is-inside-work-tree 2>/dev/null || true)" != "true" ]; then
    printf '%s\n' "Git hooks were not configured (no Git worktree found)."
    exit 0
fi

HOOK="$PROJECT_ROOT/.githooks/pre-push"
if [ ! -f "$HOOK" ]; then
    printf '%s\n' "Error: Cannot enable hooks; .githooks/pre-push is missing." >&2
    exit 1
fi
chmod +x "$HOOK" || {
    printf '%s\n' "Error: Could not make .githooks/pre-push executable." >&2
    exit 1
}
[ -x "$HOOK" ] || {
    printf '%s\n' "Error: .githooks/pre-push is not executable." >&2
    exit 1
}
if ! sh -n "$HOOK"; then
    printf '%s\n' "Error: .githooks/pre-push has invalid POSIX shell syntax." >&2
    exit 1
fi

git -C "$PROJECT_ROOT" config --local core.hooksPath .githooks
configured="$(git -C "$PROJECT_ROOT" config --local --get core.hooksPath || true)"
if [ "$configured" != ".githooks" ]; then
    printf '%s\n' "Error: Git did not retain the repository-local hooks path." >&2
    exit 1
fi
printf '%s\n' "Git pre-push checks are enabled for this worktree."
