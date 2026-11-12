#!/usr/bin/env sh
# Fixture test for .githooks/pre-push (no force-push to a protected branch), T-003.
#
# Until T-003 nothing ran this hook: other suites only check it is executable and
# read its protected= list. This suite feeds it the "<local ref> <local sha>
# <remote ref> <remote sha>" lines git sends on stdin and asserts the verdict, then
# proves git really calls it with a real push to a local bare remote. It also checks
# pre-commit and pre-push protect the same set of branches.
#
# Stack-neutral: POSIX sh + git only. Deterministic (no network, no wall-clock, no
# random; global/system git config ignored).
#
# Run:  sh scripts/test/test-pre-push.sh
#       PRE_PUSH_HOOK=<path> sh scripts/test/test-pre-push.sh   (test another hook)
#       SH=dash sh scripts/test/test-pre-push.sh                (run it under dash)
# Exit: 0 all pass, 1 any failure.

set -u

test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
playbook_src=$(CDPATH='' cd -- "$test_dir/../.." && pwd)
sut="${PRE_PUSH_HOOK:-$playbook_src/.githooks/pre-push}"
case "$sut" in /*) ;; *) sut="$PWD/$sut" ;; esac
pre_commit="$playbook_src/.githooks/pre-commit"
SH="${SH:-sh}"

tmp=$(mktemp -d 2>/dev/null || mktemp -d -t pp)
trap 'rm -rf "$tmp"' EXIT

mkdir -p "$tmp/home" "$tmp/xdg"
HOME="$tmp/home" XDG_CONFIG_HOME="$tmp/xdg"
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export HOME XDG_CONFIG_HOME GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE

pass=0
fail=0
ok()  { pass=$((pass + 1)); printf "  PASS: %s\n" "$1"; }
bad() { fail=$((fail + 1)); printf "  FAIL: %s\n" "$1" >&2; }

zero=0000000000000000000000000000000000000000

# A repo with history base -> ff (a fast-forward of base) and a sibling `other`
# that does not contain base's successor: other is a rewrite relative to ff.
r="$tmp/repo"
mkdir -p "$r"
(
  cd "$r" && git init -q && git symbolic-ref HEAD refs/heads/main \
    && git config user.email t@t && git config user.name t && git config commit.gpgsign false
  echo a > f && git add f && git commit -qm "base T-000"
  echo b >> f && git commit -qam "ff T-000"
  git checkout -q -b side HEAD~1
  echo c >> f && git commit -qam "other T-000"
  git checkout -q main
)
base=$(cd "$r" && git rev-parse main~1)
ff=$(cd "$r" && git rev-parse main)
other=$(cd "$r" && git rev-parse side)

# feed <stdin lines>: run the hook in the repo. Sets $rc and $err.
feed() {
  err=$( cd "$r" && printf '%s\n' "$1" | "$SH" "$sut" origin file:///dev/null 2>&1 >/dev/null ); rc=$?
}
expect_block() {  # <stdin> <needle> <description>
  feed "$1"
  if [ "$rc" -eq 1 ] && printf '%s' "$err" | grep -qF -- "$2"; then ok "$3"; else bad "$3 (rc=$rc, stderr: $err)"; fi
}
expect_pass() {   # <stdin> <description>
  feed "$1"
  if [ "$rc" -eq 0 ]; then ok "$2"; else bad "$2 (rc=$rc, stderr: $err)"; fi
}

echo "test-pre-push.sh"

# 1) hackathon/demo, this fork's integration branch.
expect_block "refs/heads/x $other refs/heads/hackathon/demo $ff" \
  "Force-push to 'hackathon/demo'" "a history rewrite of hackathon/demo blocks"
expect_pass  "refs/heads/x $ff refs/heads/hackathon/demo $base" \
  "a fast-forward of hackathon/demo passes"
expect_pass  "refs/heads/x $ff refs/heads/hackathon/demo $zero" \
  "creating hackathon/demo (remote sha all zeros) passes"
# Deleting: local sha is all zeros, which is not a descendant of the remote tip,
# so today's hook treats a delete as a rewrite. Pinned so a change is deliberate.
expect_block "(delete) $zero refs/heads/hackathon/demo $ff" \
  "Force-push to 'hackathon/demo'" "deleting hackathon/demo blocks"

# 2) Look-alikes are not protected: the match is the whole branch name.
for b in hackathon/demo-x feat/hackathon/demo hackathon/demo/x hackathon; do
  expect_pass "refs/heads/x $other refs/heads/$b $ff" "a rewrite of look-alike '$b' passes"
done

# 3) The starter's own protected branches still block.
for b in main master production release trunk; do
  expect_block "refs/heads/x $other refs/heads/$b $ff" "Force-push to '$b'" "a rewrite of $b blocks"
done

# 4) Several refs in one push: one protected rewrite blocks the whole push.
expect_block "refs/heads/a $other refs/heads/feat/a $ff
refs/heads/b $other refs/heads/hackathon/demo $ff" \
  "Force-push to 'hackathon/demo'" "one protected rewrite among several refs blocks"
expect_pass "refs/heads/a $other refs/heads/feat/a $ff
refs/heads/b $ff refs/heads/hackathon/demo $base" \
  "several refs with no protected rewrite pass"

# 5) End to end: git itself runs the hook on a real push.
bare="$tmp/remote.git"
git init -q --bare "$bare"
cp "$sut" "$tmp/pre-push" && chmod +x "$tmp/pre-push"
mkdir -p "$tmp/hooks" && cp "$tmp/pre-push" "$tmp/hooks/pre-push"
if ( cd "$r" && git config core.hooksPath "$tmp/hooks" \
       && git push -q "$bare" "$ff:refs/heads/hackathon/demo" 2>/dev/null ); then
  ok "a real push creating hackathon/demo succeeds"
else bad "a real push creating hackathon/demo succeeds"; fi
err=$( cd "$r" && git push --force "$bare" "$other:refs/heads/hackathon/demo" 2>&1 ); rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$err" | grep -qF "Force-push to 'hackathon/demo'"; then
  ok "a real git push --force to hackathon/demo is refused by the hook"
else bad "a real git push --force to hackathon/demo is refused by the hook (rc=$rc: $err)"; fi
tip=$(git --git-dir="$bare" rev-parse refs/heads/hackathon/demo)
if [ "$tip" = "$ff" ]; then ok "...and the remote branch is unchanged"; else bad "...and the remote branch is unchanged (now $tip)"; fi
( cd "$r" && git push -q "$bare" "$ff:refs/heads/feat/x" 2>/dev/null )
# The setup push must have landed, or the force-push below only creates the branch.
if [ "$(git --git-dir="$bare" rev-parse refs/heads/feat/x 2>/dev/null)" = "$ff" ]; then ok "setup: feat/x exists on the remote at ff"
else bad "setup: feat/x exists on the remote at ff"; fi
err=$( cd "$r" && git push --force "$bare" "$other:refs/heads/feat/x" 2>&1 ); rc=$?
if [ "$rc" -eq 0 ] && [ "$(git --git-dir="$bare" rev-parse refs/heads/feat/x)" = "$other" ]; then
  ok "a real force-push rewriting a feature branch goes through"
else bad "a real force-push rewriting a feature branch goes through (rc=$rc: $err)"; fi

# 6) pre-commit (no direct commits) and pre-push (no rewrites) protect the same set.
push_set=$(sed -n "s/^protected='\(.*\)'$/\1/p" "$sut" | tr ' ' '\n' | grep -v '^$' | sort)
commit_set=$(awk '/case "\$branch" in/ { getline; sub(/^[ \t]*/, ""); sub(/\)[ \t]*$/, ""); print; exit }' "$pre_commit" \
  | tr '|' '\n' | grep -v '^$' | sort)
if [ -n "$push_set" ] && [ "$push_set" = "$commit_set" ]; then
  ok "pre-commit and pre-push protect the same branches ($(printf '%s' "$push_set" | tr '\n' ' '))"
else
  bad "pre-commit and pre-push protect the same branches (pre-push: $(printf '%s' "$push_set" | tr '\n' ' '); pre-commit: $(printf '%s' "$commit_set" | tr '\n' ' '))"
fi
case "
$push_set
" in *"
hackathon/demo
"*) ok "hackathon/demo is in the protected set" ;; *) bad "hackathon/demo is in the protected set" ;; esac

echo
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
