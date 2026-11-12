#!/usr/bin/env sh
# Fixture test for scripts/setup-hooks.sh.
#
# Stack-neutral: POSIX sh + git only. Runs setup-hooks.sh in a throwaway git repo
# and asserts it installs the hooks and only points operators at CI files that
# exist. Deterministic (no network, no wall-clock, no random; global/system git
# config ignored).
#
# Run:  sh scripts/test/test-setup-hooks.sh
# Exit: 0 all pass, 1 any failure.

set -u

test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
playbook_src=$(CDPATH='' cd -- "$test_dir/../.." && pwd)
sut="$playbook_src/scripts/setup-hooks.sh"

GIT_CONFIG_GLOBAL=/dev/null
GIT_CONFIG_NOSYSTEM=1
export GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM

pass=0
fail=0
ok()  { pass=$((pass + 1)); printf "  PASS: %s\n" "$1"; }
bad() { fail=$((fail + 1)); printf "  FAIL: %s\n" "$1" >&2; }

echo "test-setup-hooks.sh"

root=$(mktemp -d 2>/dev/null || mktemp -d -t sh)
nogit=$(mktemp -d 2>/dev/null || mktemp -d -t sh)
trap 'rm -rf "$root" "$nogit"' EXIT
mkdir -p "$root/.githooks"
for h in pre-commit commit-msg pre-push; do
  printf '#!/bin/sh\nexit 0\n' > "$root/.githooks/$h"
done
( cd "$root" && git init -q )
out=$( cd "$root" && sh "$sut" 2>&1 ); rc=$?

# 1) Installs: exit 0, core.hooksPath set, hooks executable.
if [ "$rc" -eq 0 ]; then ok "exits 0"; else bad "exits 0 (rc=$rc: $out)"; fi
hp=$(git -C "$root" config core.hooksPath || true)
if [ "$hp" = ".githooks" ]; then ok "sets core.hooksPath to .githooks"; else bad "sets core.hooksPath (got '$hp')"; fi
if [ -x "$root/.githooks/pre-commit" ] && [ -x "$root/.githooks/commit-msg" ] && [ -x "$root/.githooks/pre-push" ]
then ok "makes hooks executable"; else bad "makes hooks executable"; fi

# 2) Every CI file/script the guidance names exists (it once pointed at
#    .github/workflows/checks.yml after that file was parked — starter T-012).
named=$(printf '%s\n' "$out" | grep -oE '[A-Za-z0-9_./-]+\.(yml|yaml|sh)' || true)
# ci_path_ok <repo> <path>: the path exists, or it's the GitHub workflow and the repo
# keeps it at the other path (active, or parked as ADOPTING.md § 4b describes).
ci_path_ok() {
  [ -f "$1/$2" ] && return 0
  case "$2" in
    .github/workflows/checks.yml) [ -f "$1/.github/workflows-disabled/checks.yml" ] ;;
    .github/workflows-disabled/checks.yml) [ -f "$1/.github/workflows/checks.yml" ] ;;
    *) return 1 ;;
  esac
}
missing_paths=''
for p in $named; do ci_path_ok "$playbook_src" "$p" || missing_paths="$missing_paths $p"; done
if [ -n "$named" ] && [ -z "$missing_paths" ]; then ok "names only CI files that exist"
else bad "names missing CI file(s):${missing_paths:- none named}"; fi
mkdir -p "$nogit/moved/.github/workflows"; : > "$nogit/moved/.github/workflows/checks.yml"
if ci_path_ok "$nogit/moved" .github/workflows-disabled/checks.yml; then ok "accepts the parked path once the workflow is moved into place"
else bad "accepts the parked path once the workflow is moved into place"; fi
mkdir -p "$nogit/parked/.github/workflows-disabled"; : > "$nogit/parked/.github/workflows-disabled/checks.yml"
if ci_path_ok "$nogit/parked" .github/workflows/checks.yml; then ok "accepts the active path in a repo that parked the workflow"
else bad "accepts the active path in a repo that parked the workflow"; fi
if ci_path_ok "$nogit/moved" azure-pipelines.yml; then bad "still reports other missing CI files"
else ok "still reports other missing CI files"; fi

# 3) Outside a git repo: refuses with exit 1 and says why.
err=$( cd "$nogit" && GIT_CEILING_DIRECTORIES="$nogit/.." sh "$sut" 2>&1 >/dev/null ); rc=$?
if [ "$rc" -eq 1 ] && printf '%s' "$err" | grep -qF "Not in a git repository"; then ok "refuses outside a git repo"
else bad "refuses outside a git repo (rc=$rc, stderr: $err)"; fi

# 4) Hooks and shell scripts are committed executable, so git runs a fresh clone's hooks
#    and setup-hooks.sh doesn't leave them showing as modified (starter T-018).
# check_modes <repo>: each tracked hook or shell script that isn't mode 100755.
check_modes() {
  ( cd "$1" && git ls-files -s -- .githooks 'scripts/*.sh' ) \
    | awk '$1 != "100755" && $4 !~ /\.md$/ { print $4 }'
}
tracked=$( cd "$playbook_src" && git ls-files -- .githooks/pre-commit .githooks/commit-msg .githooks/pre-push | wc -l )
wrong=$(check_modes "$playbook_src")
if [ "$tracked" -eq 3 ] && [ -z "$wrong" ]; then ok "hooks and shell scripts are committed executable"
else bad "not committed executable (tracked hooks: $tracked): $wrong"; fi

modes="$nogit/modes"
mkdir -p "$modes/.githooks" "$modes/scripts"
printf '#!/bin/sh\n' > "$modes/.githooks/pre-push"
printf 'docs\n' > "$modes/.githooks/README.md"
( cd "$modes" && git init -q && git add . && git update-index --chmod=-x .githooks/pre-push )
wrong=$(check_modes "$modes")
if [ "$wrong" = ".githooks/pre-push" ]; then ok "fixture: a non-executable hook is caught, README.md is ignored"
else bad "fixture: wanted .githooks/pre-push, got: $wrong"; fi
( cd "$modes" && git update-index --chmod=+x .githooks/pre-push )
wrong=$(check_modes "$modes")
if [ -z "$wrong" ]; then ok "fixture: an executable hook passes"
else bad "fixture: wanted no findings, got: $wrong"; fi

echo
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
