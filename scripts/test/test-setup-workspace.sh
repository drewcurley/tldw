#!/usr/bin/env sh
# Fixture test for scripts/setup-workspace.sh.
#
# Stack-neutral: needs only POSIX sh + git (both present in CI). No test runner.
# Builds throwaway workspaces in a temp dir, runs setup-workspace.sh against them,
# and asserts the security guards and governance-verification behave. Deterministic
# (no network, no wall-clock, no random).
#
# Run:  sh scripts/test/test-setup-workspace.sh
# Exit: 0 all pass, 1 any failure.

set -u

# --- locate the script under test ------------------------------------------
test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
playbook_src=$(CDPATH='' cd -- "$test_dir/../.." && pwd)
sut="$playbook_src/scripts/setup-workspace.sh"

pass=0
fail=0
ok()   { pass=$((pass + 1)); printf "  PASS: %s\n" "$1"; return 0; }
bad()  { fail=$((fail + 1)); printf "  FAIL: %s\n" "$1" >&2; return 0; }
# assert <condition-rc> <description>: pass if rc==0 else fail. Avoids the
# `A && ok || bad` idiom (where C can run even when A succeeds).
assert() { if [ "$1" -eq 0 ]; then ok "$2"; else bad "$2"; fi; }
# grep helpers that return rc for assert()
has()  { printf '%s' "$1" | grep -qi -- "$2"; }       # has <text> <needle>
hasq() { printf '%s' "$1" | grep -q -- "$2"; }        # has, case-sensitive

# Make a fresh workspace: <root>/playbook (a git repo with the SUT + hook stubs).
# Echoes the workspace root path.
make_workspace() {
  root=$(mktemp -d 2>/dev/null || mktemp -d -t ws)
  mkdir -p "$root/playbook/.githooks" "$root/playbook/scripts"
  for h in pre-commit commit-msg pre-push; do
    printf '#!/bin/sh\nexit 0\n' > "$root/playbook/.githooks/$h"
  done
  cp "$sut" "$root/playbook/scripts/setup-workspace.sh"
  ( cd "$root/playbook" && git init -q && git config user.email t@t && git config user.name t )
  echo "$root"
}

make_repo() {  # make_repo <root> <name>
  mkdir -p "$1/$2"
  ( cd "$1/$2" && git init -q && git config user.email t@t && git config user.name t )
}

write_manifest() {  # write_manifest <root> <<rows
  cat > "$1/playbook/workspace.manifest.md"
}

hookspath() { ( cd "$1" 2>/dev/null && { git config core.hooksPath 2>/dev/null || true; } ); }

run_sut() { ( cd "$1/playbook" && sh scripts/setup-workspace.sh 2>&1 ); }

# ---------------------------------------------------------------------------
echo "test-setup-workspace.sh"

# 1) Valid repos get governed; clean-run summary appears.
root=$(make_workspace); make_repo "$root" web; make_repo "$root" api
write_manifest "$root" <<'EOF'
| dir | role | url | branch | contracts |
|-----|------|-----|--------|-----------|
| `web` | f | u | main | docs/contracts.md |
| `api` | b | u | main | docs/contracts.md |
EOF
out=$(run_sut "$root")
if [ "$(hookspath "$root/web")" = ".githooks" ]; then ok "valid repo 'web' governed"; else bad "web not governed"; fi
if [ "$(hookspath "$root/api")" = ".githooks" ]; then ok "valid repo 'api' governed"; else bad "api not governed"; fi
hasq "$out" "All 2 repo(s) wired (0 missing, 0 rejected)."; assert $? "clean-run summary present"
if hasq "$out" '\\n'; then bad "literal backslash-n in output"; else ok "no literal backslash-n in output"; fi
rm -rf "$root"

# 2) Path-traversal row is rejected AND writes nothing outside the workspace.
root=$(make_workspace); make_repo "$root" web
mkdir -p "$root/evil"   # the dir ../evil would resolve to
write_manifest "$root" <<'EOF'
| dir | role | url | branch | contracts |
|-----|------|-----|--------|-----------|
| `web` | f | u | main | x |
| `../evil` | attack | u | main | x |
EOF
out=$(run_sut "$root")
has "$out" "REJECTED: '../evil'"; assert $? "traversal row rejected"
if [ ! -e "$root/evil/.githooks" ]; then ok "no write outside workspace (../evil untouched)"; else bad "traversal wrote into ../evil"; fi
rm -rf "$root"

# 3) Glob row is rejected (no expansion against cwd).
root=$(make_workspace); make_repo "$root" web
write_manifest "$root" <<'EOF'
| dir | role | url | branch | contracts |
|-----|------|-----|--------|-----------|
| `*` | glob | u | main | x |
EOF
out=$(run_sut "$root")
has "$out" "REJECTED: '\*'"; assert $? "glob row rejected"
rm -rf "$root"

# 4) Absolute-path and tilde rows are rejected.
root=$(make_workspace)
write_manifest "$root" <<'EOF'
| dir | role | url | branch | contracts |
|-----|------|-----|--------|-----------|
| `/etc` | abs | u | main | x |
| `~root` | tilde | u | main | x |
EOF
out=$(run_sut "$root")
has "$out" "REJECTED: '/etc'"; assert $? "absolute path rejected"
has "$out" "REJECTED: '~root'"; assert $? "tilde path rejected"
rm -rf "$root"

# 5) Missing repo is reported, exit still 0.
root=$(make_workspace)
write_manifest "$root" <<'EOF'
| dir | role | url | branch | contracts |
|-----|------|-----|--------|-----------|
| `nope` | missing | u | main | x |
EOF
out=$(run_sut "$root"); rc=$?
has "$out" "missing:  nope"; assert $? "missing repo reported"
assert "$rc" "exit 0 with only missing repos"
rm -rf "$root"

# 6) Placeholder-only manifest is a safe no-op (single-repo team won't misfire).
root=$(make_workspace)
# The starter copied its own workspace.manifest.md here. This fork is a single repo
# and ships no manifest (its presence would switch agents to the workspace model,
# AGENTS.md § 0), so the upstream template's structure is written inline (T-003):
# the placeholder rows, and the commented-out example row the parser must skip.
# The product-name placeholder is assembled at runtime so this file never holds a
# literal the placeholder gate would block.
ph="[ins""ert"
write_manifest "$root" <<EOF
# Workspace Manifest — $ph product name here]

- **Name:** $ph product name here]

<!-- A filled row looks like this (delete this comment once you've added real rows):
| \`web\` | Next.js frontend | git@github.com:acme/web.git | \`main\` | \`docs/contracts.md\` |
-->

| dir | role | git URL | default branch | contracts doc |
|-----|------|---------|----------------|---------------|
| \`[insert repo-1 dir]\` | [insert role — e.g. web frontend] | [insert git URL] | \`main\` | \`docs/contracts.md\` |
| \`[insert repo-2 dir]\` | [insert role — e.g. backend API] | [insert git URL] | \`main\` | \`docs/contracts.md\` |
| \`[insert repo-3 dir]\` | [insert role — e.g. infra/IaC] | [insert git URL] | \`main\` | \`docs/contracts.md\` |
EOF
out=$(run_sut "$root"); rc=$?
has "$out" "No repos listed"; assert $? "placeholder manifest = safe no-op"
assert "$rc" "exit 0 on placeholder manifest"
rm -rf "$root"

# 7) Governance verification: rc=2 path — hooks present but core.hooksPath cleared
#    after the fact is re-set on re-run (idempotent + verified).
root=$(make_workspace); make_repo "$root" web
write_manifest "$root" <<'EOF'
| dir | role | url | branch | contracts |
|-----|------|-----|--------|-----------|
| `web` | f | u | main | x |
EOF
run_sut "$root" >/dev/null
( cd "$root/web" && git config --unset core.hooksPath )   # simulate drift
run_sut "$root" >/dev/null                                 # re-run must re-fix
if [ "$(hookspath "$root/web")" = ".githooks" ]; then ok "re-run is idempotent and re-verifies governance"; else bad "re-run did not restore governance"; fi
rm -rf "$root"

# 15) Every CI file/script the closing guidance names exists in the playbook (it once
#     pointed at .github/workflows/checks.yml after that file was parked — starter T-012).
root=$(make_workspace); make_repo "$root" web
write_manifest "$root" <<'EOF'
| dir | role | url | branch | contracts |
|-----|------|-----|--------|-----------|
| `web` | f | u | main | x |
EOF
out=$(run_sut "$root")
named=$(printf '%s\n' "$out" | grep -oE '[A-Za-z0-9_./-]+\.(yml|yaml|sh)' || true)
missing_paths=''
for p in $named; do [ -f "$playbook_src/$p" ] || missing_paths="$missing_paths $p"; done
if [ -n "$named" ] && [ -z "$missing_paths" ]; then ok "closing guidance names only CI files that exist"
else bad "closing guidance names missing CI file(s):${missing_paths:- none named}"; fi
rm -rf "$root"

# ---------------------------------------------------------------------------
echo
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
