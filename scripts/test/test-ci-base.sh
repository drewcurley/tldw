#!/usr/bin/env sh
# Fixture test for scripts/ci-base.sh (push-build diff base).
#
# Stack-neutral: POSIX sh + git only. Builds throwaway git repos in a temp dir and
# asserts the base ci-base.sh prints, or that it fails instead of guessing.
# Deterministic (no network, no wall-clock, no random; global/system git config
# ignored).
#
# Run:  sh scripts/test/test-ci-base.sh
#       SH=dash sh scripts/test/test-ci-base.sh   (run it under dash)
# Exit: 0 all pass, 1 any failure.

set -u

test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
playbook_src=$(CDPATH='' cd -- "$test_dir/../.." && pwd)
sut="$playbook_src/scripts/ci-base.sh"
SH="${SH:-sh}"

GIT_CONFIG_GLOBAL=/dev/null
GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE

tmp=$(mktemp -d 2>/dev/null || mktemp -d -t cb)
trap 'rm -rf "$tmp"' EXIT

pass=0
fail=0
ok()  { pass=$((pass + 1)); printf "  PASS: %s\n" "$1"; }
bad() { fail=$((fail + 1)); printf "  FAIL: %s\n" "$1" >&2; }

# expect_out <want> <description>: ci-base.sh exited 0 and printed exactly <want>.
expect_out() {
  if [ "$rc" -eq 0 ] && [ "$out" = "$1" ]; then ok "$2"; else bad "$2 (rc=$rc, out: $out, want: $1)"; fi
}

r="$tmp/repo"
mkdir -p "$r"
( cd "$r" && git init -q && git config user.email t@t && git config user.name t \
    && git config commit.gpgsign false && echo a > f && git add f && git commit -qm "root T-000" )
root=$(cd "$r" && git rev-parse HEAD)

echo "test-ci-base.sh"

# 1) Root commit: no parent, so the base is the empty tree (ci-checks.sh then
#    checks the whole tree instead of an empty HEAD...HEAD range).
out=$( cd "$r" && "$SH" "$sut" ); rc=$?
expect_out "$(cd "$r" && git hash-object -t tree /dev/null)" "root commit: base is the empty tree"

( cd "$r" && echo b >> f && git commit -qam "two T-001" )
tip=$(cd "$r" && git rev-parse HEAD)

# 2) No before / all-zeros before (first push): HEAD's first parent.
out=$( cd "$r" && "$SH" "$sut" ); rc=$?
expect_out "$root" "no before: base is HEAD~1"
out=$( cd "$r" && "$SH" "$sut" "" ); rc=$?
expect_out "$root" "empty before: base is HEAD~1"
out=$( cd "$r" && "$SH" "$sut" 0000000000000000000000000000000000000000 ); rc=$?
expect_out "$root" "all-zeros before (first push): base is HEAD~1"

# 3) A real before SHA is passed through (ci-checks.sh verifies it).
out=$( cd "$r" && "$SH" "$sut" "$tip" ); rc=$?
expect_out "$tip" "real before SHA is used as the base"

# 4) Merge commit: first parent, not the merged-in branch.
( cd "$r" && git checkout -q -b side "$root" && echo s > s && git add s && git commit -qm "side T-002" \
    && git checkout -q - && git merge -q --no-edit side >/dev/null )
out=$( cd "$r" && "$SH" "$sut" ); rc=$?
expect_out "$tip" "merge commit: base is the first parent"

# 5) git failure exits 1 with a message and prints no base (no silent fallback).
mkdir -p "$tmp/stub"
cat > "$tmp/stub/git" <<'STUB'
#!/bin/sh
echo "stub: injected git failure" >&2; exit 128
STUB
chmod +x "$tmp/stub/git"
out=$( cd "$r" && PATH="$tmp/stub:$PATH" "$SH" "$sut" 2>"$tmp/err" ); rc=$?
if [ "$rc" -eq 1 ] && [ -z "$out" ] && grep -qF "cannot resolve a diff base" "$tmp/err"; then ok "git error fails with no base printed"
else bad "git error fails with no base printed (rc=$rc, out: $out, stderr: $(cat "$tmp/err"))"; fi

# 6) Outside a git repo: fails, prints no base.
mkdir -p "$tmp/nogit"
out=$( cd "$tmp/nogit" && GIT_CEILING_DIRECTORIES="$tmp" "$SH" "$sut" 2>/dev/null ); rc=$?
if [ "$rc" -eq 1 ] && [ -z "$out" ]; then ok "outside a repo fails with no base printed"
else bad "outside a repo fails with no base printed (rc=$rc, out: $out)"; fi

echo
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
