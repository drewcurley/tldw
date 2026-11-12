#!/usr/bin/env sh
# Claude Code's push prompts cover the branches the pre-push hook protects (starter T-017):
# .claude/settings.json must ask before a push naming any branch in the hook's list and
# before a force flag, keep the force-push denies, and leave feature-branch pushes unprompted.
# This pins the rule strings. It can't check how Claude Code matches them.
#
# Run:  sh scripts/test/test-claude-settings.sh
#       CLAUDE_SETTINGS_ROOT=<dir> sh scripts/test/test-claude-settings.sh   (check another tree)
# Exit: 0 all pass, 1 any failure. A repo with no .claude/ prints a SKIP: line for that check.

set -u
LC_ALL=C; export LC_ALL

test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
root="${CLAUDE_SETTINGS_ROOT:-$(CDPATH='' cd -- "$test_dir/../.." && pwd)}"

tmp=$(mktemp -d 2>/dev/null || mktemp -d -t cs)
trap 'rm -rf "$tmp"' EXIT

pass=0
fail=0
ok()  { pass=$((pass + 1)); printf "  PASS: %s\n" "$1"; }
bad() { fail=$((fail + 1)); printf "  FAIL: %s\n" "$1" >&2; }

# section <file> <key>: the lines of a multi-line JSON array.
section() {
  awk -v key="\"$2\":" 'found && /^[ \t]*\]/ { exit } found { print } index($0, key) { found = 1 }' "$1"
}
# has <section text> <rule>
has() { printf '%s\n' "$1" | grep -qF -- "\"$2\""; }

# check_settings <root>: one "FAIL: ..." line per problem.
check_settings() {
  s="$1/.claude/settings.json"
  h="$1/.githooks/pre-push"
  [ -f "$s" ] || { echo "FAIL: .claude/settings.json is missing"; return 0; }
  [ -f "$h" ] || { echo "FAIL: .githooks/pre-push is missing"; return 0; }
  protected=$(sed -n "s/^protected='\(.*\)'\$/\1/p" "$h")
  [ -n "$protected" ] || echo "FAIL: no protected list found in .githooks/pre-push"
  allow=$(section "$s" allow)
  ask=$(section "$s" ask)
  deny=$(section "$s" deny)
  for b in $protected; do
    has "$ask" "Bash(git push *$b*)" || echo "FAIL: ask has no rule for pushes naming $b"
  done
  for r in 'Bash(git push *--force*)' 'Bash(git push * -f*)'; do
    has "$ask" "$r" || echo "FAIL: ask is missing $r"
  done
  for r in 'Bash(git push --force:*)' 'Bash(git push -f:*)'; do
    has "$deny" "$r" || echo "FAIL: deny is missing $r"
  done
  if has "$ask" 'Bash(git push:*)'; then echo "FAIL: ask prompts on every push, feature branches included"; fi
  if printf '%s\n' "$allow" | grep -qF 'Bash(git push'; then echo "FAIL: allow must not pre-approve git push"; fi
}

# report <description> <check output>
report() {
  if printf '%s\n' "$2" | grep -q '^FAIL'; then
    bad "$1"; printf '%s\n' "$2" | grep '^FAIL' | sed 's/^/    /' >&2
  else ok "$1"; fi
}
# expect_fail <description> <needle> <check output>
expect_fail() {
  if printf '%s\n' "$3" | grep -qF -- "$2"; then ok "$1"
  else bad "$1 (wanted '$2'; got: $3)"; fi
}

echo "test-claude-settings.sh"

if [ -d "$root/.claude" ]; then
  report "push rules cover the hook's protected branches ($root)" "$(check_settings "$root")"
else
  echo "SKIP: no .claude/ directory in $root"
fi

# --- Fixtures -------------------------------------------------------------------
fx() {
  rm -rf "$tmp/fx"; mkdir -p "$tmp/fx/.claude" "$tmp/fx/.githooks"
  printf "#!/bin/sh\nprotected='main trunk'\n" > "$tmp/fx/.githooks/pre-push"
  cat > "$tmp/fx/.claude/settings.json" <<'EOF'
{
  "permissions": {
    "allow": [
      "Bash(git status:*)"
    ],
    "ask": [
      "Bash(git push *main*)",
      "Bash(git push *trunk*)",
      "Bash(git push *--force*)",
      "Bash(git push * -f*)"
    ],
    "deny": [
      "Bash(git push --force:*)",
      "Bash(git push -f:*)"
    ]
  }
}
EOF
}
# drop <fixed string>: remove the settings lines containing it.
drop() {
  grep -vF -- "$1" "$tmp/fx/.claude/settings.json" > "$tmp/fx/new"
  mv "$tmp/fx/new" "$tmp/fx/.claude/settings.json"
}
# swap <from> <to>: replace one settings line with another.
swap() {
  awk -v from="$1" -v to="$2" 'index($0, from) { print "      \"" to "\","; next } { print }' \
    "$tmp/fx/.claude/settings.json" > "$tmp/fx/new"
  mv "$tmp/fx/new" "$tmp/fx/.claude/settings.json"
}

fx; report "fixture: complete rules pass" "$(check_settings "$tmp/fx")"
fx; drop '*main*'
expect_fail "a protected branch with no ask rule is caught" "ask has no rule for pushes naming main" "$(check_settings "$tmp/fx")"
fx; printf "protected='main trunk staging'\n" >> "$tmp/fx/.githooks/pre-push"
expect_fail "a branch added to the hook's list needs a rule" "ask has no rule for pushes naming staging" "$(check_settings "$tmp/fx")"
fx; printf '#!/bin/sh\n' > "$tmp/fx/.githooks/pre-push"
expect_fail "a hook with no protected list fails closed" "no protected list found" "$(check_settings "$tmp/fx")"
fx; drop '*--force*'
expect_fail "a missing --force ask rule is caught" 'ask is missing Bash(git push *--force*)' "$(check_settings "$tmp/fx")"
fx; drop '* -f*'
expect_fail "a missing -f ask rule is caught" 'ask is missing Bash(git push * -f*)' "$(check_settings "$tmp/fx")"
fx; drop 'git push --force:*'
expect_fail "a missing --force deny is caught" 'deny is missing Bash(git push --force:*)' "$(check_settings "$tmp/fx")"
fx; drop 'git push -f:*'
expect_fail "a missing -f deny is caught" 'deny is missing Bash(git push -f:*)' "$(check_settings "$tmp/fx")"
fx; swap 'git push *trunk*' 'Bash(git push:*)'
expect_fail "a blanket push prompt is caught" "ask prompts on every push" "$(check_settings "$tmp/fx")"
fx; swap 'git status:*' 'Bash(git push:*)'
expect_fail "a pre-approved push is caught" "allow must not pre-approve git push" "$(check_settings "$tmp/fx")"
fx; swap 'git status:*' 'Bash(git push *main*)'; drop '      "Bash(git push *trunk*)",'
expect_fail "a rule outside ask doesn't count" "ask has no rule for pushes naming trunk" "$(check_settings "$tmp/fx")"
fx; rm "$tmp/fx/.claude/settings.json"
expect_fail "a missing settings file fails" ".claude/settings.json is missing" "$(check_settings "$tmp/fx")"

echo ""
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
