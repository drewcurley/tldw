#!/usr/bin/env sh
# ci-base.sh — print the diff base for a push (non-PR) CI build of HEAD.
#
# WHY this exists: both pipelines need the same answer, and the inline versions
# masked git errors (`git rev-parse HEAD~1 2>/dev/null || git rev-parse HEAD`
# turned any failure into an empty HEAD...HEAD range that passed every gate) or
# handed ci-checks.sh GitHub's all-zeros "before" on a branch's first push (starter T-013).
#
# Usage:  sh scripts/ci-base.sh [<before-sha>]
#   <before-sha>  the pre-push tip, if the CI system gives one (GitHub's
#                 github.event.before). Empty or all zeros (new branch, first push)
#                 falls back to HEAD's first parent.
# Output: the base on stdout. A root commit has no parent, so its base is the
#         empty-tree SHA, which tells ci-checks.sh to check the whole tree (a
#         root commit's entire content is its diff). ci-checks.sh verifies
#         whatever this prints.
# Exit:   0 printed a base, 1 git failed (never a silent fallback).

set -u

before="${1:-}"
case "$before" in
  *[!0]*) printf '%s\n' "$before"; exit 0 ;;   # a real SHA (any non-zero digit)
esac

parents=$(git rev-list --parents -n 1 HEAD) || {
  printf 'ci-base: git rev-list HEAD failed; cannot resolve a diff base.\n' >&2
  exit 1
}
# shellcheck disable=SC2086  # split "<HEAD> [<parent>...]" into words
set -- $parents
if [ "$#" -ge 2 ]; then printf '%s\n' "$2"; exit 0; fi
git hash-object -t tree /dev/null || {
  printf 'ci-base: git hash-object failed; cannot resolve a diff base.\n' >&2
  exit 1
}
