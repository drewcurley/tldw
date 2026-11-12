#!/usr/bin/env sh
# Fixture test for .githooks/commit-msg (task ID + /docs consensus).
#
# Stack-neutral: POSIX sh + git only. Builds throwaway git repos in a temp dir,
# stages files, writes a commit message, runs the hook, and asserts its exit code
# and the gate it names. Deterministic (no network, no wall-clock, no random;
# global/system git config ignored).
#
# Run:  sh scripts/test/test-commit-msg.sh
#       COMMIT_MSG_HOOK=<path> sh scripts/test/test-commit-msg.sh  (test another hook)
#       SH=dash sh scripts/test/test-commit-msg.sh                 (run it under dash)
# Exit: 0 all pass, 1 any failure.

set -u

test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
playbook_src=$(CDPATH='' cd -- "$test_dir/../.." && pwd)
sut="${COMMIT_MSG_HOOK:-$playbook_src/.githooks/commit-msg}"
case "$sut" in /*) ;; *) sut="$PWD/$sut" ;; esac
SH="${SH:-sh}"

# Ignore the runner's own git config and any inherited repo pointers.
GIT_CONFIG_GLOBAL=/dev/null
GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE

# Stubs and the message file live outside the fixture repos.
tmp=$(mktemp -d 2>/dev/null || mktemp -d -t cm)
trap 'rm -rf "$tmp"' EXIT

pass=0
fail=0
ok()  { pass=$((pass + 1)); printf "  PASS: %s\n" "$1"; }
bad() { fail=$((fail + 1)); printf "  FAIL: %s\n" "$1" >&2; }

# Build a repo with a baseline commit on a feature branch. Echo its path.
make_repo() {
  root=$(mktemp -d 2>/dev/null || mktemp -d -t cm)
  (
    cd "$root" && git init -q && git symbolic-ref HEAD refs/heads/feat/x \
      && git config user.email t@t && git config user.name t \
      && git config commit.gpgsign false
    echo "baseline" > file.txt
    git add file.txt && git commit -qm "baseline T-000"
  )
  echo "$root"
}

# make_stub <cmd> <exit>: a dir holding a <cmd> that prints an error, touches
# $STUB_HIT and exits <exit> when its argument string matches the glob $STUB_FAIL;
# otherwise it runs the real <cmd>. Put it on PATH only for the hook.
make_stub() {
  real=$(command -v "$1")
  mkdir -p "$tmp/stub-$1"
  cat > "$tmp/stub-$1/$1" <<STUB
#!/bin/sh
# shellcheck disable=SC2254
case "\$*" in \$STUB_FAIL) : > "\$STUB_HIT"; echo "stub: injected $1 failure" >&2; exit $2 ;; esac
exec "$real" "\$@"
STUB
  chmod +x "$tmp/stub-$1/$1"
}
make_stub git 128
make_stub sed 2

# run_hook <root> <path-to-stage> <message> [<stubbed-cmd> <glob>]: stage <path>
# (created with one line), write <message>, run the hook. Sets $rc and $err.
run_hook() {
  mkdir -p "$1/$(dirname -- "$2")"
  echo "change" >> "$1/$2"
  ( cd "$1" && git add -- "$2" )
  printf '%s\n' "$3" > "$tmp/msg"
  rm -f "$tmp/hit"
  if [ $# -ge 5 ]; then
    err=$( cd "$1" && PATH="$tmp/stub-$4:$PATH" STUB_FAIL="$5" STUB_HIT="$tmp/hit" \
      "$SH" "$sut" "$tmp/msg" 2>&1 >/dev/null ); rc=$?
  else
    err=$( cd "$1" && "$SH" "$sut" "$tmp/msg" 2>&1 >/dev/null ); rc=$?
  fi
}

expect_pass() {  # expect_pass <description>
  if [ "$rc" -eq 0 ] && [ -z "$err" ]; then ok "$1"; else bad "$1 (rc=$rc, stderr: $err)"; fi
}
expect_block() {  # expect_block <needle> <description> [stubbed]
  if [ "$rc" -eq 1 ] && printf '%s' "$err" | grep -qF -- "$1" \
     && { [ $# -lt 3 ] || [ -e "$tmp/hit" ]; }
  then ok "$2"; else bad "$2 (rc=$rc, stderr: $err)"; fi
}

consensus="docs: change T-201

unanimous-consensus: T-201"

echo "test-commit-msg.sh"

# 1) Task ID gate.
r=$(make_repo); run_hook "$r" file.txt "feat: thing T-101";   expect_pass "message with a Task ID passes"; rm -rf "$r"
r=$(make_repo); run_hook "$r" file.txt "feat: sub-task T-101.2"; expect_pass "message with a subtask ID passes"; rm -rf "$r"
r=$(make_repo); run_hook "$r" file.txt "feat: no id here";    expect_block "must reference a Task ID" "message without a Task ID blocks"; rm -rf "$r"
r=$(make_repo); run_hook "$r" file.txt "Merge branch 'x'";    expect_pass "merge commit skips the gates"; rm -rf "$r"
r=$(make_repo); run_hook "$r" file.txt 'Revert "feat: x"';    expect_pass "revert commit skips the gates"; rm -rf "$r"

# 2) /docs consensus gate.
r=$(make_repo); run_hook "$r" docs/x.md "docs: change T-201"; expect_block "unanimous-consensus" "authored /docs without consensus blocks"; rm -rf "$r"
r=$(make_repo); run_hook "$r" docs/x.md "$consensus";         expect_pass "authored /docs with consensus passes"; rm -rf "$r"
r=$(make_repo); run_hook "$r" docs/repos/web/x.md "docs: rollup T-202"; expect_pass "generated docs/repos/** is exempt"; rm -rf "$r"
r=$(make_repo); run_hook "$r" "docs/$(printf 'caf\303\251.md')" "docs: change T-201"
expect_block "unanimous-consensus" "non-ASCII authored /docs filename still needs consensus (starter T-014)"; rm -rf "$r"
r=$(make_repo); run_hook "$r" "docs/$(printf 'a\tb.md')" "docs: change T-201"
expect_block "unanimous-consensus" "authored /docs filename git must quote still needs consensus (starter T-014)"; rm -rf "$r"

# 3) Fail closed (starter T-013): a git or filter error can't read as "no /docs change".
r=$(make_repo); run_hook "$r" docs/x.md "docs: change T-201" git '*--name-only*'
expect_block "consensus check could not run" "staged file list error fails closed" stub; rm -rf "$r"
r=$(make_repo); run_hook "$r" docs/x.md "docs: change T-201" sed '-n /^"*'
expect_block "consensus check could not run" "file list filter error fails closed" stub; rm -rf "$r"

# 4) First commit in a new repo (no HEAD yet): the /docs gate still applies.
new_repo() {
  r=$(mktemp -d 2>/dev/null || mktemp -d -t cm)
  ( cd "$r" && git init -q && git symbolic-ref HEAD refs/heads/feat/x )
}
new_repo; run_hook "$r" file.txt "chore: init T-203";        expect_pass "initial commit (no HEAD) passes"; rm -rf "$r"
new_repo; run_hook "$r" docs/x.md "docs: init T-203";        expect_block "unanimous-consensus" "initial commit (no HEAD) /docs without consensus blocks"; rm -rf "$r"

echo
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
