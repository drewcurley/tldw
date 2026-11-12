#!/usr/bin/env sh
# ci-deck-needed.sh — does this CI build need the overview deck job? (starter T-022)
#
# The deck job installs five Python packages and builds the deck: the slowest job in CI.
# A PR that changes none of the files scripts/test/test-deck.sh reads can't change that
# suite's result, so the job reports a quick SKIP instead. Only a confirmed PR build,
# checked out as the synthetic merge of the PR into its target, can skip. Pushes, manual
# runs and anything unclear build the deck.
#
# Usage: sh scripts/ci-deck-needed.sh          decide for HEAD: prints "run: ..." or "skip: ..."
#        sh scripts/ci-deck-needed.sh --list   print the files that make the deck job run
# Exit:  0 run, 1 skip. The CI files run the TARGET branch's copy (their "deck-gate"
#        block) and treat anything but exit 1 with a "skip:" line as run.

set -u
LC_ALL=C; export LC_ALL

# Everything test-deck.sh reads. Its self-test copies exactly this list into its
# fixture, so a file missing here makes that self-test fail.
triggers() {
  cat <<'EOF'
Agentic-Starter-Overview.pptx
scripts/build_deck.py
scripts/requirements-deck.txt
scripts/test/test-deck.sh
scripts/ci-deck-needed.sh
azure-pipelines.yml
.github/workflows/checks.yml
.github/workflows-disabled/checks.yml
.github/CODEOWNERS
ADOPTING.md
EOF
}
if [ "${1:-}" = "--list" ]; then triggers; exit 0; fi

run() { echo "run: $*"; exit 0; }

# 1) A confirmed PR build, and the PR's head commit.
if [ "${GITHUB_EVENT_NAME:-}" = "pull_request" ]; then
  case "${GITHUB_REF:-}" in refs/pull/*/merge) ;; *) run "a pull_request build not on a refs/pull/<n>/merge ref" ;; esac
  { [ -n "${GITHUB_EVENT_PATH:-}" ] && [ -f "$GITHUB_EVENT_PATH" ]; } || run "no event payload to read the PR head from"
  command -v jq >/dev/null 2>&1 || run "no jq to read the PR head"
  pr_head=$(jq -r '.pull_request.head.sha // empty' "$GITHUB_EVENT_PATH" 2>/dev/null) || run "unreadable event payload"
elif [ "${BUILD_REASON:-}" = "PullRequest" ]; then
  case "${BUILD_SOURCEBRANCH:-}" in refs/pull/*/merge) ;; *) run "a PR build not on a refs/pull/<n>/merge ref" ;; esac
  pr_head=${SYSTEM_PULLREQUEST_SOURCECOMMITID:-}
else
  run "not a PR build"
fi
[ -n "$pr_head" ] || run "the PR's head commit is unknown"

# 2) HEAD is the synthetic merge: first parent the target tip, second the PR head.
base=$(git rev-parse --verify -q 'HEAD^1^{commit}') || run "HEAD has no first parent here (shallow or root)"
second=$(git rev-parse --verify -q 'HEAD^2^{commit}') || run "HEAD is not a merge commit"
[ "$second" = "$pr_head" ] || run "HEAD's second parent ($second) isn't the PR head ($pr_head)"

# 3) What the PR changes. Written to a file, so a git failure can't read as "nothing".
tmp=$(mktemp 2>/dev/null || mktemp -t deckneed) || run "mktemp failed"
trap 'rm -f "$tmp" "$tmp.t" "$tmp.h"' EXIT
git diff --no-renames --name-only "$base" HEAD > "$tmp" 2>/dev/null || run "git diff failed"
triggers > "$tmp.t" || run "couldn't write the file list"
rc=0
grep -Fx -f "$tmp.t" "$tmp" > "$tmp.h" 2>/dev/null || rc=$?
[ "$rc" -le 1 ] || run "grep failed (exit $rc)"
[ "$rc" -ne 0 ] || run "$(head -n 1 "$tmp.h") changed"
echo "skip: this PR changes $(wc -l < "$tmp" | tr -d ' ') file(s), none that the deck suite reads"
exit 1
