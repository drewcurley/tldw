#!/usr/bin/env sh
# Fixture test for scripts/aggregate-docs.sh. POSIX sh + git only; deterministic.
# Run: sh scripts/test/test-aggregate-docs.sh   Exit: 0 all pass, 1 any failure.

set -u
test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
playbook_src=$(CDPATH='' cd -- "$test_dir/../.." && pwd)
sut="$playbook_src/scripts/aggregate-docs.sh"

pass=0; fail=0
ok()  { pass=$((pass + 1)); printf "  PASS: %s\n" "$1"; }
bad() { fail=$((fail + 1)); printf "  FAIL: %s\n" "$1" >&2; }

# build a workspace: <root>/playbook (git) + named repos each with /docs
make_ws() {
  root=$(mktemp -d 2>/dev/null || mktemp -d -t agg)
  mkdir -p "$root/playbook/scripts"
  cp "$sut" "$root/playbook/scripts/aggregate-docs.sh"
  ( cd "$root/playbook" && git init -q && git config user.email t@t && git config user.name t )
  cat > "$root/playbook/workspace.manifest.md" <<'EOF'
| dir | role | url | branch | contracts |
|-----|------|-----|--------|-----------|
| `web` | f | u | main | docs/contracts.md |
EOF
  echo "$root"
}
add_repo_docs() {  # add_repo_docs <root> <name>
  mkdir -p "$1/$2/docs"
  ( cd "$1/$2" && git init -q && git config user.email t@t && git config user.name t
    echo "# $2" > docs/contracts.md && git add -A && git commit -qm "init $2 T-000" )
}
run()   { ( cd "$1/playbook" && sh scripts/aggregate-docs.sh "$2" >/dev/null 2>&1; echo $? ); }

echo "test-aggregate-docs.sh"

# 1) --check on never-built rollup → stale (rc 1)
r=$(make_ws); add_repo_docs "$r" web
rc=$(run "$r" --check)
if [ "$rc" -eq 1 ]; then ok "check flags stale when rollup missing"; else bad "should be stale (rc=$rc)"; fi

# 2) refresh builds the mirror + stamp
run "$r" "" >/dev/null
if [ -f "$r/playbook/docs/repos/web/contracts.md" ] && [ -f "$r/playbook/docs/repos/web/.source-sha" ]; then
  ok "refresh mirrors repo docs + writes SHA stamp"
else bad "mirror or stamp missing after refresh"; fi

# 3) --check now current (rc 0)
rc=$(run "$r" --check)
if [ "$rc" -eq 0 ]; then ok "check passes when current"; else bad "should be current (rc=$rc)"; fi

# 4) source change → stale again
( cd "$r/web" && echo "x" >> docs/contracts.md && git add -A && git commit -qm "update T-001" )
rc=$(run "$r" --check)
if [ "$rc" -eq 1 ]; then ok "check flags stale after source change"; else bad "should be stale after change (rc=$rc)"; fi

# 5) deletion in source propagates on refresh
( cd "$r/web" && echo "# extra" > docs/extra.md && git add -A && git commit -qm "add extra T-002" )
run "$r" "" >/dev/null
if [ -f "$r/playbook/docs/repos/web/extra.md" ]; then ok "addition mirrored"; else bad "addition not mirrored"; fi
( cd "$r/web" && git rm -q docs/extra.md && git commit -qm "rm extra T-903" )
run "$r" "" >/dev/null
if [ ! -f "$r/playbook/docs/repos/web/extra.md" ]; then ok "deletion propagates to mirror"; else bad "deleted file lingers in mirror"; fi
rm -rf "$r"

# 6) single-repo (no manifest) → no-op, rc 0
r2=$(mktemp -d); mkdir -p "$r2/scripts"; cp "$sut" "$r2/scripts/aggregate-docs.sh"
rc=$( ( cd "$r2" && sh scripts/aggregate-docs.sh --check >/dev/null 2>&1; echo $? ) )
if [ "$rc" -eq 0 ]; then ok "no manifest = safe no-op"; else bad "no-manifest should be no-op (rc=$rc)"; fi
rm -rf "$r2"

echo
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
