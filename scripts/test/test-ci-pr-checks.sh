#!/usr/bin/env sh
# Fixture test for scripts/ci-pr-checks.sh and the "trusted-gate" block in both CI
# files (starter T-014: a PR can't loosen the gates it is judged by), and the Azure
# step's choice between the PR gate and the branch scan (starter T-022).
#
# Stack-neutral: POSIX sh + git only. Builds throwaway repos whose `main` carries
# the real gate files and whose `pr` branch tries to weaken them, then runs the
# gate the way a PR build does. Deterministic (no network, no wall-clock, no
# random; global/system/XDG git config and attributes ignored).
#
# Run:  sh scripts/test/test-ci-pr-checks.sh
#       CI_PR_CHECKS=<path> sh scripts/test/test-ci-pr-checks.sh  (test another runner;
#         a shim running `sh scripts/ci-checks.sh "$1" "$2"` is the pipeline before starter T-014)
#       CI_FILES_ROOT=<dir> sh scripts/test/test-ci-pr-checks.sh (read the two CI files from <dir>)
#       SH=dash sh scripts/test/test-ci-pr-checks.sh             (run it under dash)
# Exit: 0 all pass, 1 any failure.

set -u

test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
playbook_src=$(CDPATH='' cd -- "$test_dir/../.." && pwd)
runner="${CI_PR_CHECKS:-$playbook_src/scripts/ci-pr-checks.sh}"
case "$runner" in /*) ;; *) runner="$PWD/$runner" ;; esac
ci_root="${CI_FILES_ROOT:-$playbook_src}"
SH="${SH:-sh}"

tmp=$(mktemp -d 2>/dev/null || mktemp -d -t pr)
trap 'rm -rf "$tmp"' EXIT

# Ignore the runner's own git config, attributes and any inherited repo pointers.
mkdir -p "$tmp/home" "$tmp/xdg"
HOME="$tmp/home" XDG_CONFIG_HOME="$tmp/xdg"
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_ATTR_NOSYSTEM=1
export HOME XDG_CONFIG_HOME GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM GIT_ATTR_NOSYSTEM
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_EXTERNAL_DIFF
# Plain-text warnings: the scripts switch to ##[warning] / ::warning:: under CI. Azure's
# build variables are cleared too, so a run inside Azure tests what a local run does.
unset TF_BUILD GITHUB_ACTIONS BUILD_REASON SYSTEM_PULLREQUEST_TARGETBRANCH

pass=0
fail=0
ok()  { pass=$((pass + 1)); printf "  PASS: %s\n" "$1"; }
bad() { fail=$((fail + 1)); printf "  FAIL: %s\n" "$1" >&2; }

# Literals the gates would flag are assembled at runtime (see test-pre-commit.sh).
secret="key=AKIA$(printf 'IOSFODNN7EXAMPLE')"
placeholder=$(printf 'use [%s thing here]' insert)
tamper=$(printf '#!/bin/sh\necho TAMPERED\nexit 0\n')

# make_fixture: a repo whose `main` has the real gate files, checked out on a new
# `pr` branch. Sets $r. Every commit carries a Task ID.
make_fixture() {
  r=$(mktemp -d "$tmp/repo.XXXXXX")
  mkdir -p "$r/scripts"
  cp "$playbook_src/scripts/ci-checks.sh" "$playbook_src/scripts/ci-pr-checks.sh" "$r/scripts/"
  printf '# allowlist\nREADME.md\n' > "$r/.placeholder-allow"
  ( cd "$r" && git init -q && git symbolic-ref HEAD refs/heads/main \
      && git config user.email t@t && git config user.name t && git config commit.gpgsign false \
      && echo base > file.txt && echo readme > README.md && git add -A && git commit -qm "base T-901" \
      && git checkout -q -b pr )
}
on_main() { ( cd "$r" && git checkout -q main && sh -c "$1" && git add -A && git commit -qm "main: $2 T-902" && git checkout -q pr ); }
pr_commit() { ( cd "$r" && git add -A && git commit -qm "pr: $1 T-903" ); }

# make_stub <cmd> <exit>: fails the $STUB_NTH-th (default 1st) call whose argument
# string matches $STUB_FAIL (touching $STUB_HIT); every other call runs the real <cmd>.
make_stub() {
  real=$(command -v "$1")
  mkdir -p "$tmp/stub-$1"
  cat > "$tmp/stub-$1/$1" <<EOF
#!/bin/sh
# shellcheck disable=SC2254
case "\$*" in \$STUB_FAIL)
  n=\$(( \$(cat "\$STUB_HIT.n" 2>/dev/null || echo 0) + 1 )); echo "\$n" > "\$STUB_HIT.n"
  if [ "\$n" -eq "\${STUB_NTH:-1}" ]; then : > "\$STUB_HIT"; echo "stub: injected $1 failure" >&2; exit $2; fi ;;
esac
exec "$real" "\$@"
EOF
  chmod +x "$tmp/stub-$1/$1"
}
make_stub git 128

# run_pr [<target> [<glob> [<nth>]]]: run the gate for HEAD against <target>
# (default main) from the repo root, git stubbed on <glob>. Sets $rc, $out.
run_pr() {
  rm -f "$tmp/hit" "$tmp/hit.n"
  h=$(cd "$r" && git rev-parse HEAD)
  if [ $# -ge 2 ]; then
    out=$( cd "$r" && PATH="$tmp/stub-git:$PATH" STUB_FAIL="$2" STUB_NTH="${3:-1}" STUB_HIT="$tmp/hit" \
      "$SH" "$runner" "$1" "$h" 2>&1 ); rc=$?
  else
    out=$( cd "$r" && "$SH" "$runner" "${1:-main}" "$h" 2>&1 ); rc=$?
  fi
}
expect_pass() {  # expect_pass <description>
  if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -qF "all procedure gates passed"; then ok "$1"
  else bad "$1 (rc=$rc, out: $out)"; fi
}
expect_fail() {  # expect_fail <needle> <description> [stubbed]: exit 1 naming <needle>
  if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -qF -- "$1" \
     && ! printf '%s' "$out" | grep -qF "all procedure gates passed" \
     && ! printf '%s' "$out" | grep -qF "TAMPERED" \
     && { [ $# -lt 3 ] || [ -e "$tmp/hit" ]; }
  then ok "$2"; else bad "$2 (rc=$rc, out: $out)"; fi
}

echo "test-ci-pr-checks.sh"

# 1) Control: a clean PR passes, by branch name and by raw SHA.
make_fixture; echo "clean" >> "$r/file.txt"; pr_commit "clean"
run_pr main; expect_pass "clean PR passes (target by name)"
run_pr "$(cd "$r" && git rev-parse main)"; expect_pass "clean PR passes (target by SHA)"

# 2) The PR's edits to the gate don't apply to the PR itself.
make_fixture; printf '%s' "$tamper" > "$r/scripts/ci-checks.sh"; printf '%s\n' "$secret" >> "$r/file.txt"
pr_commit "tamper"; run_pr
expect_fail "Possible secret detected" "PR that guts ci-checks.sh still has its secret caught"

# 3) The PR can't allowlist its own placeholder.
make_fixture; printf '%s\n' "$placeholder" > "$r/notes.md"; printf 'notes.md\n' >> "$r/.placeholder-allow"
pr_commit "self-allowlist"; run_pr
expect_fail "allowlist is read from the target branch" "PR that allowlists its own placeholder still fails"

# 4) A target with no .placeholder-allow allowlists nothing (not the PR's list).
make_fixture; on_main "git rm -q .placeholder-allow" "drop allowlist"
echo "clean" >> "$r/file.txt"; pr_commit "clean"; run_pr
expect_pass "target without an allowlist: clean PR passes"
printf '# allowlist\nREADME.md\n' > "$r/.placeholder-allow"; printf '%s\n' "$placeholder" >> "$r/README.md"
pr_commit "allowlist + placeholder"; run_pr
expect_fail "Unreplaced" "target without an allowlist: PR's own allowlist is ignored"

# 5) The target's gate can't be loaded: fail closed, gates not run.
make_fixture; on_main "git rm -q scripts/ci-checks.sh" "drop gate"
echo "clean" >> "$r/file.txt"; pr_commit "clean"; run_pr
expect_fail "has no scripts/ci-checks.sh" "target without ci-checks.sh fails closed"
make_fixture; on_main "git rm -q scripts/ci-checks.sh && ln -s ci-pr-checks.sh scripts/ci-checks.sh" "symlink gate"
echo "clean" >> "$r/file.txt"; pr_commit "clean"; run_pr
expect_fail "is not a regular file" "target whose ci-checks.sh is a symlink fails closed"
make_fixture; on_main "git rm -q .placeholder-allow && ln -s scripts/ci-checks.sh .placeholder-allow" "symlink allowlist"
echo "clean" >> "$r/file.txt"; pr_commit "clean"; run_pr
expect_fail "Target's .placeholder-allow is not a regular file" "target whose allowlist is a symlink fails closed"
run_pr nope;                    expect_fail "CI-PR-CHECK FAIL: Target 'nope' does not resolve" "unresolvable target fails closed"
out=$( cd "$r" && "$SH" "$runner" main deadbeef 2>&1 ); rc=$?
expect_fail "CI-PR-CHECK FAIL: Head 'deadbeef' does not resolve" "unresolvable head fails closed"
out=$( cd "$r" && "$SH" "$runner" main --output=x 2>&1 ); rc=$?
expect_fail "CI-PR-CHECK FAIL: Head '--output=x'" "option-like head is refused"
run_pr "--output=$tmp/pwned"
expect_fail "CI-PR-CHECK FAIL: Target '--output=" "option-like target is refused"
leaked=$(find "$tmp" -maxdepth 1 -name 'pwned*')
if [ -z "$leaked" ]; then ok "option-like target is not run as a git option"; else bad "option-like target wrote $leaked"; fi
out=$( cd "$r" && "$SH" "$runner" main 2>&1 ); rc=$?
expect_fail "CI-PR-CHECK FAIL: Usage" "missing head argument is refused"

# 6) Git errors while loading the target's files fail closed.
make_fixture; echo "clean" >> "$r/file.txt"; pr_commit "clean"
run_pr main 'ls-tree *' 1;    expect_fail "CI-PR-CHECK FAIL: git ls-tree" "gate lookup error fails closed" stub
run_pr main 'cat-file blob *' 1; expect_fail "Reading the target's scripts/ci-checks.sh failed" "gate read error fails closed" stub
run_pr main 'ls-tree *' 2;    expect_fail "CI-PR-CHECK FAIL: git ls-tree" "allowlist lookup error fails closed" stub
run_pr main 'cat-file blob *' 2; expect_fail "Reading the target's .placeholder-allow failed" "allowlist read error fails closed" stub

# 7) A target whose gate predates CI_PLACEHOLDER_ALLOW is run, with a warning.
make_fixture; on_main "printf '#!/bin/sh\necho OLD-GATE\nexit 0\n' > scripts/ci-checks.sh" "old gate"
echo "clean" >> "$r/file.txt"; pr_commit "clean"; run_pr
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -qF "CI-PR-CHECK WARN" && printf '%s' "$out" | grep -qF "OLD-GATE"
then ok "target gate without CI_PLACEHOLDER_ALLOW runs with a warning"
else bad "target gate without CI_PLACEHOLDER_ALLOW runs with a warning (rc=$rc, out: $out)"; fi

# 8) Synthetic merge head (what ADO checks out) after the target moved on.
make_fixture; printf '%s\n' "$secret" >> "$r/file.txt"; pr_commit "secret"
on_main "echo later > later.txt" "moved on"
( cd "$r" && git checkout -q --detach main && git merge -q --no-ff --no-edit pr )
run_pr; expect_fail "Possible secret detected" "synthetic merge head: PR's secret is caught"
make_fixture; echo "clean" >> "$r/file.txt"; pr_commit "clean"
on_main "echo later > later.txt" "moved on"
( cd "$r" && git checkout -q --detach main && git merge -q --no-ff --no-edit pr )
run_pr; expect_pass "synthetic merge head: clean PR passes"

# 9) The trusted-gate block in both CI files: identical, and it takes the runner
#    from the target branch (the PR's copy only when the target has none).
blk_az=$(sed -n '/# BEGIN trusted-gate/,/# END trusted-gate/p' "$ci_root/azure-pipelines.yml" | sed 's/^[[:space:]]*//')
gh_ci=.github/workflows-disabled/checks.yml
[ -f "$ci_root/.github/workflows/checks.yml" ] && gh_ci=.github/workflows/checks.yml
blk_gh=$(sed -n '/# BEGIN trusted-gate/,/# END trusted-gate/p' "$ci_root/$gh_ci" | sed 's/^[[:space:]]*//')
if [ -n "$blk_az" ] && [ "$blk_az" = "$blk_gh" ]; then ok "trusted-gate block present and identical in both CI files"
else bad "trusted-gate block present and identical in both CI files"; fi
run_block() {  # run_block: run the CI block in $r for HEAD vs main. Sets $rc, $out.
  # shellcheck disable=SC2016  # literal script text for the block's preamble
  { printf 'set -e\ntarget_sha=$(git rev-parse main)\nhead=$(git rev-parse HEAD)\n'
    printf '%s\n' "$blk_az"; } > "$tmp/block.sh"
  out=$( cd "$r" && "$SH" "$tmp/block.sh" 2>&1 ); rc=$?
}
make_fixture; printf '%s' "$tamper" > "$r/scripts/ci-pr-checks.sh"; printf '%s\n' "$secret" >> "$r/file.txt"
pr_commit "tamper runner"; run_block
expect_fail "Possible secret detected" "CI block runs the target's ci-pr-checks.sh, not the PR's"
if printf '%s' "$out" | grep -qF "Gate runner: the target's scripts/ci-pr-checks.sh (100"; then ok "CI block logs which runner judged the PR"
else bad "CI block logs which runner judged the PR (out: $out)"; fi
make_fixture; echo "clean" >> "$r/file.txt"; pr_commit "clean"
rm -f "$tmp/hit" "$tmp/hit.n"
# shellcheck disable=SC2016  # literal script text for the block's preamble
{ printf 'set -e\ntarget_sha=$(git rev-parse main)\nhead=$(git rev-parse HEAD)\n'; printf '%s\n' "$blk_az"; } > "$tmp/block.sh"
out=$( cd "$r" && PATH="$tmp/stub-git:$PATH" STUB_FAIL='ls-tree *' STUB_HIT="$tmp/hit" "$SH" "$tmp/block.sh" 2>&1 ); rc=$?
if [ "$rc" -ne 0 ] && [ -e "$tmp/hit" ] && ! printf '%s' "$out" | grep -qF "all procedure gates passed"
then ok "CI block fails closed on a git error"; else bad "CI block fails closed on a git error (rc=$rc, out: $out)"; fi
make_fixture; on_main "git rm -q scripts/ci-pr-checks.sh" "no runner yet"
cp "$playbook_src/scripts/ci-pr-checks.sh" "$r/scripts/"; echo "clean" >> "$r/file.txt"; pr_commit "add runner"
run_block
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -qF "WARNING: the target branch has no scripts/ci-pr-checks.sh"
then ok "CI block bootstraps with the PR's runner, with a warning"
else bad "CI block bootstraps with the PR's runner, with a warning (rc=$rc, out: $out)"; fi

# 10) The Azure procedure step's "pr-mode" block (starter T-022) picks the PR gate or
#     the branch scan from Azure's env vars, and fails closed when it can't tell.
az="$ci_root/azure-pipelines.yml"
blk_mode=$(sed -n '/# BEGIN pr-mode/,/# END pr-mode/p' "$az" | sed 's/^[[:space:]]*//')
if [ "$(grep -c '# BEGIN pr-mode' "$az")" -eq 1 ] && printf '%s' "$blk_mode" | grep -qF '# BEGIN trusted-gate'
then ok "pr-mode block marked once in azure-pipelines.yml, around the trusted gate"
else bad "pr-mode block marked once in azure-pipelines.yml, around the trusted gate"; fi
if grep -Eq '\$\((System|Build)\.' "$az"; then bad "azure-pipelines.yml pastes no Build./System. macros into scripts ($(grep -En '\$\((System|Build)\.' "$az" | tr '\n' ' '))"
else ok "azure-pipelines.yml pastes no Build./System. macros into scripts"; fi
# shellcheck disable=SC2016  # literal script text for the block's preamble
{ printf 'set -e\nhead=$(git rev-parse HEAD)\n'; printf '%s\n' "$blk_mode"; } > "$tmp/mode.sh"
# run_mode <TF_BUILD> <BUILD_REASON> <SYSTEM_PULLREQUEST_TARGETBRANCH>: every Azure
# variable the block reads is set explicitly. Sets $rc, $out.
run_mode() {
  out=$( cd "$r" && TF_BUILD=$1 BUILD_REASON=$2 SYSTEM_PULLREQUEST_TARGETBRANCH=$3 "$SH" "$tmp/mode.sh" 2>&1 ); rc=$?
}
mode_fixture() {
  make_fixture
  on_main "cp '$playbook_src/scripts/ci-base.sh' scripts/" "add ci-base"
  ( cd "$r" && git remote add origin "$r" )   # the block fetches the target from origin
  ( cd "$r" && git rebase -q main )
  echo "clean" >> "$r/file.txt"; pr_commit "clean"
}
ran_gate() { printf '%s' "$out" | grep -qF "Gate runner: the target's scripts/ci-pr-checks.sh"; }
mode_fixture
for t in main refs/heads/main; do
  run_mode True PullRequest "$t"
  if [ "$rc" -eq 0 ] && ran_gate && printf '%s' "$out" | grep -qF "PR build: target main"; then ok "PR build targeting '$t' runs the target's gate"
  else bad "PR build targeting '$t' runs the target's gate (rc=$rc, out: $out)"; fi
done
run_mode True Manual main
if [ "$rc" -eq 0 ] && ran_gate; then ok "a non-PR reason with a PR target still takes the PR gate"
else bad "a non-PR reason with a PR target still takes the PR gate (rc=$rc, out: $out)"; fi
run_mode True IndividualCI ''
if [ "$rc" -eq 0 ] && ! ran_gate && printf '%s' "$out" | grep -qF "Diff range:"; then ok "a branch build takes the branch scan"
else bad "a branch build takes the branch scan (rc=$rc, out: $out)"; fi
run_mode True PullRequest ''
if [ "$rc" -ne 0 ] && ! ran_gate && printf '%s' "$out" | grep -qF "PR build with no target branch" \
   && ! printf '%s' "$out" | grep -qF "all procedure gates passed"
then ok "a PR build with no target fails closed without running a gate"
else bad "a PR build with no target fails closed without running a gate (rc=$rc, out: $out)"; fi
run_mode True '' ''
if [ "$rc" -ne 0 ] && ! ran_gate && printf '%s' "$out" | grep -qF "BUILD_REASON is empty"; then ok "an unknown build type on Azure fails closed"
else bad "an unknown build type on Azure fails closed (rc=$rc, out: $out)"; fi
run_mode True PullRequest '-x'
if [ "$rc" -ne 0 ] && ! ran_gate && printf '%s' "$out" | grep -qF "looks like an option"; then ok "a PR target that looks like an option fails closed"
else bad "a PR target that looks like an option fails closed (rc=$rc, out: $out)"; fi
printf '%s\n' "$secret" >> "$r/file.txt"; pr_commit "secret"
run_mode True PullRequest main
if [ "$rc" -ne 0 ] && ran_gate && printf '%s' "$out" | grep -qF "Possible secret detected"; then ok "the PR path really runs the gate (a secret fails it)"
else bad "the PR path really runs the gate (a secret fails it) (rc=$rc, out: $out)"; fi

echo
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
