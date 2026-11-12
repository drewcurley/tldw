#!/usr/bin/env sh
# Fixture test for .githooks/pre-commit (secret scan + protected branch).
#
# Stack-neutral: POSIX sh + git only. Builds throwaway git repos in a temp dir,
# stages content that should pass/fail each gate, runs the hook, and asserts its
# exit code and the gate it names. Deterministic (no network, no wall-clock, no
# random; global/system git config ignored).
#
# Run:  sh scripts/test/test-pre-commit.sh
#       PRE_COMMIT_HOOK=<path> sh scripts/test/test-pre-commit.sh  (test another hook)
#       SH=dash sh scripts/test/test-pre-commit.sh                 (run it under dash)
# Exit: 0 all pass, 1 any failure.

set -u

test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
playbook_src=$(CDPATH='' cd -- "$test_dir/../.." && pwd)
sut="${PRE_COMMIT_HOOK:-$playbook_src/.githooks/pre-commit}"
case "$sut" in /*) ;; *) sut="$PWD/$sut" ;; esac
SH="${SH:-sh}"

# Stubs and scratch files live outside the fixture repos.
tmp=$(mktemp -d 2>/dev/null || mktemp -d -t pc)
trap 'rm -rf "$tmp"' EXIT

# Ignore the runner's own git config (hooksPath, default branch, signing),
# attributes, and any inherited repo pointers.
mkdir -p "$tmp/home" "$tmp/xdg"
HOME="$tmp/home" XDG_CONFIG_HOME="$tmp/xdg"
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_ATTR_NOSYSTEM=1
export HOME XDG_CONFIG_HOME GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM GIT_ATTR_NOSYSTEM
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_EXTERNAL_DIFF

pass=0
fail=0
ok()  { pass=$((pass + 1)); printf "  PASS: %s\n" "$1"; }
bad() { fail=$((fail + 1)); printf "  FAIL: %s\n" "$1" >&2; }

# Build a repo with a baseline commit on main, then switch to a feature branch
# (so the protected-branch gate isn't what blocks). Echo its path.
make_repo() {
  root=$(mktemp -d 2>/dev/null || mktemp -d -t pc)
  (
    cd "$root" && git init -q && git symbolic-ref HEAD refs/heads/main \
      && git config user.email t@t && git config user.name t \
      && git config commit.gpgsign false
    echo "baseline" > file.txt
    git add file.txt && git commit -qm "baseline T-000"
    git checkout -q -b feat/x
  )
  echo "$root"
}

# run_hook <root> <content>: append content to file.txt, stage only that file,
# run the hook. Sets $rc and $err (hook stderr).
run_hook() {
  printf '%s\n' "$2" >> "$1/file.txt"
  ( cd "$1" && git add file.txt )
  err=$( cd "$1" && "$SH" "$sut" 2>&1 >/dev/null ); rc=$?
}

# expect_block <root> <content> <label> <description>: hook exits 1 naming <label>.
expect_block() {
  run_hook "$1" "$2"
  if [ "$rc" -eq 1 ] && printf '%s' "$err" | grep -qF -- "$3"; then ok "$4"
  else bad "$4 (rc=$rc, stderr: $err)"; fi
}

# rep <char> <n>: print <char> n times.
rep() {
  s='' i=0
  while [ "$i" -lt "$2" ]; do s="$s$1"; i=$((i + 1)); done
  printf '%s' "$s"
}

# Secret literals are assembled at runtime so this file never contains a string
# the secret scanner (hook or CI) would flag. One secret per case: the hook stops
# at the first match, so each case asserts exactly one label.
d=-----
rsa_key=$(printf '%sBEGIN RSA PRIVATE KEY%s\nMIIEow\n%sEND RSA PRIVATE KEY%s' "$d" "$d" "$d" "$d")
ssh_key=$(printf '%sBEGIN OPENSSH PRIVATE KEY%s' "$d" "$d")
aws_key="AKIA$(printf 'IOSFODNN7EXAMPLE')"
gh_token="ghp_$(rep a 36)"
slack_token="xoxb-$(printf '123-abc')"
openai_key="sk-$(rep A 24)"
anthropic_key="sk-ant-api03-$(rep b 24)"
placeholder=$(printf 'use [%s thing here]' insert)   # same reason: the hook blocks the literal

echo "test-pre-commit.sh"

# 1) Clean change passes, and no gate errors out (a grep option-parse error here
#    means some pattern was never actually scanned).
r=$(make_repo)
run_hook "$r" "clean line"
if [ "$rc" -eq 0 ] && [ -z "$err" ]; then ok "clean change passes with no scanner errors"
else bad "clean change passes with no scanner errors (rc=$rc, stderr: $err)"; fi
rm -rf "$r"

# 2) Private-key blocks — the regression: the pattern starts with '-' and was
#    parsed as a grep option, so this gate silently never ran.
r=$(make_repo); expect_block "$r" "$rsa_key" "Private key block" "RSA private key block blocks"; rm -rf "$r"
r=$(make_repo); expect_block "$r" "$ssh_key" "Private key block" "OPENSSH private key block blocks"; rm -rf "$r"

# 3) Every other secret pattern still blocks with its own label.
r=$(make_repo); expect_block "$r" "key=$aws_key"       "AWS access key ID"    "AWS key blocks"; rm -rf "$r"
r=$(make_repo); expect_block "$r" "tok=$gh_token"      "GitHub token"         "GitHub token blocks"; rm -rf "$r"
r=$(make_repo); expect_block "$r" "tok=$slack_token"   "Slack token"          "Slack token blocks"; rm -rf "$r"
r=$(make_repo); expect_block "$r" "key=$openai_key"    "OpenAI-style API key" "OpenAI-style key blocks"; rm -rf "$r"
r=$(make_repo); expect_block "$r" "key=$anthropic_key" "Anthropic API key"    "Anthropic key blocks"; rm -rf "$r"

# 4) Scanner error fails closed: a grep that errors (exit 2) on the private-key
#    pattern must block the commit, not read as "no match".
r=$(make_repo)
real_grep=$(command -v grep)
mkdir -p "$r/.stub"
cat > "$r/.stub/grep" <<EOF
#!/bin/sh
for a in "\$@"; do case "\$a" in *PRIVATE*) echo "grep: stub error" >&2; exit 2 ;; esac; done
exec "$real_grep" "\$@"
EOF
chmod +x "$r/.stub/grep"
printf '%s\n' "clean line" >> "$r/file.txt"
( cd "$r" && git add file.txt )
err=$( cd "$r" && PATH="$r/.stub:$PATH" "$SH" "$sut" 2>&1 >/dev/null ); rc=$?
if [ "$rc" -eq 1 ] && printf '%s' "$err" | grep -qF "Secret scanner error"; then ok "grep error fails closed"
else bad "grep error fails closed (rc=$rc, stderr: $err)"; fi
rm -rf "$r"

# 5) Commit on a protected branch blocks.
r=$(make_repo)
( cd "$r" && git checkout -q main )
expect_block "$r" "clean line" "Direct commit to 'main'" "commit on main blocks"
rm -rf "$r"

# 5b) hackathon/demo is this fork's integration branch (T-003): protected like main.
#     Look-alike names must still commit, so the case match is exact, not a prefix.
r=$(make_repo)
( cd "$r" && git checkout -q -b hackathon/demo )
expect_block "$r" "clean line" "Direct commit to 'hackathon/demo'" "commit on hackathon/demo blocks"
rm -rf "$r"
for b in hackathon/demo-x feat/hackathon/demo hackathon/demo/x; do
  # A fresh repo per name: hackathon/demo and hackathon/demo/x can't coexist.
  r=$(make_repo)
  ( cd "$r" && git checkout -q -b "$b" )
  run_hook "$r" "clean line"
  if [ "$rc" -eq 0 ]; then ok "commit on look-alike '$b' passes"
  else bad "commit on look-alike '$b' passes (rc=$rc, stderr: $err)"; fi
  rm -rf "$r"
done

# --- Fail closed on git errors (starter T-013) ----------------------------------------

# make_stub <cmd> <exit>: a dir holding a <cmd> that prints an error, touches
# $STUB_HIT and exits <exit> when its argument string matches the glob $STUB_FAIL;
# otherwise it runs the real <cmd>. Put it on PATH only for the hook.
make_stub() {
  real=$(command -v "$1")
  mkdir -p "$tmp/stub-$1"
  cat > "$tmp/stub-$1/$1" <<EOF
#!/bin/sh
# shellcheck disable=SC2254
case "\$*" in \$STUB_FAIL) : > "\$STUB_HIT"; echo "stub: injected $1 failure" >&2; exit $2 ;; esac
exec "$real" "\$@"
EOF
  chmod +x "$tmp/stub-$1/$1"
}
make_stub git 128
make_stub sed 2
make_stub grep 2

# expect_stub_block <root> <content> <cmd> <glob> <needle> <description>: stage
# <content>, run the hook with <cmd> failing on <glob>; expect exit 1 naming
# <needle>, and proof the targeted call ran.
expect_stub_block() {
  printf '%s\n' "$2" >> "$1/file.txt"
  ( cd "$1" && git add file.txt )
  rm -f "$tmp/hit"
  err=$( cd "$1" && PATH="$tmp/stub-$3:$PATH" STUB_FAIL="$4" STUB_HIT="$tmp/hit" \
    "$SH" "$sut" 2>&1 >/dev/null ); rc=$?
  if [ "$rc" -eq 1 ] && printf '%s' "$err" | grep -qF -- "$5" && [ -e "$tmp/hit" ]; then ok "$6"
  else bad "$6 (rc=$rc, stderr: $err)"; fi
}

# 6) A git error on any staged-content read blocks instead of reading as empty.
r=$(make_repo); expect_stub_block "$r" "key=$aws_key" git '*--name-only*' \
  "git diff --cached --name-only failed" "staged file list error fails closed"; rm -rf "$r"
r=$(make_repo); expect_stub_block "$r" "key=$aws_key" git 'diff --cached *-U0' \
  "git diff --cached failed" "staged diff error fails closed (secret not missed)"; rm -rf "$r"
r=$(make_repo); expect_stub_block "$r" "$placeholder" git '*diff --cached *-U0 -- *' \
  "git diff --cached -- 'file.txt' failed" "per-file diff error fails closed (placeholder not missed)"; rm -rf "$r"
r=$(make_repo); expect_stub_block "$r" "key=$aws_key" sed '-n /^+++/*' \
  "Filtering the staged diff failed" "diff filter error fails closed"; rm -rf "$r"
r=$(make_repo); expect_stub_block "$r" "$placeholder" grep '*insert*' \
  "Placeholder scanner error" "placeholder grep error fails closed"; rm -rf "$r"

# 7) First commit in a new repo (no HEAD yet) still works: clean passes, a secret blocks.
new_repo() {
  r=$(mktemp -d 2>/dev/null || mktemp -d -t pc)
  ( cd "$r" && git init -q && git symbolic-ref HEAD refs/heads/feat/x )
}
new_repo; run_hook "$r" "clean line"
if [ "$rc" -eq 0 ] && [ -z "$err" ]; then ok "initial commit (no HEAD) clean change passes"
else bad "initial commit (no HEAD) clean change passes (rc=$rc, stderr: $err)"; fi
rm -rf "$r"
new_repo; expect_block "$r" "key=$aws_key" "AWS access key ID" "initial commit (no HEAD) secret blocks"; rm -rf "$r"

# 8) Nothing can hide staged content from the scans (starter T-014): .gitattributes -diff /
#    binary / textconv, an external diff, a NUL byte. Each case first proves a plain
#    `git diff --cached` really hides the secret, so it can't pass vacuously.
# hidden_block <root> <description> [VAR=value]: stage everything, expect the hook
# (with the optional env assignment) to block the AWS key.
hidden_block() {
  ( cd "$1" && git add -A )
  plain=$( cd "$1" && env ${3:+"$3"} git diff --cached -U0 2>&1 ); prc=$?
  if [ "$prc" -ne 0 ] || printf '%s' "$plain" | grep -aq AKIA; then bad "$2: precondition (plain git diff must succeed and hide the secret; rc=$prc)"; fi
  err=$( cd "$1" && env ${3:+"$3"} "$SH" "$sut" 2>&1 >/dev/null ); rc=$?
  if [ "$rc" -eq 1 ] && printf '%s' "$err" | grep -aqF "AWS access key ID"; then ok "$2 can't hide a staged secret"
  else bad "$2 can't hide a staged secret (rc=$rc, stderr: $err)"; fi
  rm -rf "$1"
}
r=$(make_repo); printf 's.cfg -diff\n' > "$r/.gitattributes"; printf 'key=%s\n' "$aws_key" > "$r/s.cfg"
hidden_block "$r" ".gitattributes -diff"
r=$(make_repo); printf 's.cfg binary\n' > "$r/.gitattributes"; printf 'key=%s\n' "$aws_key" > "$r/s.cfg"
hidden_block "$r" ".gitattributes binary"
r=$(make_repo); printf 's.cfg diff=hide\n' > "$r/.gitattributes"; printf 'key=%s\n' "$aws_key" > "$r/s.cfg"
( cd "$r" && git config diff.hide.textconv true ); hidden_block "$r" "a textconv driver"
r=$(make_repo); printf 'key=%s\n' "$aws_key" > "$r/s.cfg"
hidden_block "$r" "an external diff (GIT_EXTERNAL_DIFF)" GIT_EXTERNAL_DIFF=true
r=$(make_repo); printf 'x\000y\nkey=%s\n' "$aws_key" > "$r/s.dat"
hidden_block "$r" "a NUL byte"

# 9) A binary file under a UTF-8 locale still commits (BSD sed/grep reject invalid
#    UTF-8 unless the hook runs with LC_ALL=C). Only meaningful on macOS/BSD: GNU
#    sed never fails on these bytes, so on Linux CI this passes regardless.
r=$(make_repo); printf 'img\377\376\001\n' > "$r/logo.png"; ( cd "$r" && git add logo.png )
err=$( cd "$r" && LC_ALL=C.UTF-8 "$SH" "$sut" 2>&1 >/dev/null ); rc=$?
if [ "$rc" -eq 0 ] && [ -z "$err" ]; then ok "binary file under a UTF-8 locale commits"
else bad "binary file under a UTF-8 locale commits (rc=$rc, stderr: $err)"; fi
rm -rf "$r"

# 10) Placeholders: a -diff file, a rename out of an allowlisted path, and odd
#     filenames (glob, pathspec magic, non-ASCII, space) are all still checked.
# stage_block <root> <needle> <description>: stage everything; hook exits 1 naming <needle>.
stage_block() {
  ( cd "$1" && git add -A )
  err=$( cd "$1" && "$SH" "$sut" 2>&1 >/dev/null ); rc=$?
  if [ "$rc" -eq 1 ] && printf '%s' "$err" | grep -aqF -- "$2"; then ok "$3"
  else bad "$3 (rc=$rc, stderr: $err)"; fi
  [ ! -e "$1/PWNED" ] || : > "$tmp/PWNED"   # surface a payload that ran in the repo
  rm -rf "$1"
}
# allow_repo: make_repo plus an allowlisted, committed README.md. Sets $r.
allow_repo() {
  r=$(make_repo); printf 'README.md\n' > "$r/.placeholder-allow"; echo readme > "$r/README.md"
  ( cd "$r" && git add -A && git commit -qm "chore: allowlist T-910" )
}
r=$(make_repo); printf 'x.cfg -diff\n' > "$r/.gitattributes"; printf '%s\n' "$placeholder" > "$r/x.cfg"
stage_block "$r" "x.cfg: " "placeholder in a -diff file blocks"
allow_repo; printf '%s\n' "$placeholder" > "$r/README.md"; ( cd "$r" && git commit -qam "docs: ph T-911" && git mv README.md notes.md )
stage_block "$r" "notes.md: " "rename out of an allowlisted path blocks"
allow_repo; printf '%s\n' "$placeholder" > "$r/READM[E].md"; stage_block "$r" "READM[E].md: " "glob-like filename isn't expanded to an allowlisted file"
allow_repo; printf '%s\n' "$placeholder" > "$r/:(top)x.md";  stage_block "$r" ":(top)x.md: " "pathspec-magic filename is read literally"
allow_repo; printf '%s\n' "$placeholder" > "$r/$(printf 'caf\303\251.md')"; stage_block "$r" "Unreplaced" "non-ASCII filename is checked"
allow_repo; printf '%s\n' "$placeholder" > "$r/a b.md";      stage_block "$r" "a b.md: " "filename with a space is checked whole"
allow_repo; printf '%s\n' "$placeholder" > "$r/$(printf 'a\tb.md')"; stage_block "$r" "Unusual filename" "filename git must quote blocks"
# A filename is never code: under GNU sed, `#;e cmd;#` in an interpolated sed script
# runs cmd. Expect the placeholder line reported verbatim and no PWNED file.
allow_repo; printf '%s\n' "$placeholder" > "$r/x#;e touch PWNED;#.md"
stage_block "$r" "x#;e touch PWNED;#.md: 1:+use [" "filename with sed syntax is reported, not run"
if [ ! -e "$tmp/PWNED" ] && [ ! -e PWNED ]; then ok "filename with sed syntax runs nothing"
else bad "filename with sed syntax ran a command (PWNED exists)"; fi

# 11) Hand-editing a ledger's INDEX.md warns (exit 0, names the path); other paths
#     don't. Any <dir>/tasks/INDEX.md counts, so the starter's own history ledger
#     is covered without the hook naming it (starter T-015).
# index_warn <path> <yes|no> <description>
index_warn() {
  r=$(make_repo); mkdir -p "$r/$(dirname -- "$1")"; echo row >> "$r/$1"
  ( cd "$r" && git add -- "$1" )
  err=$( cd "$r" && "$SH" "$sut" 2>&1 >/dev/null ); rc=$?
  hit=no; printf '%s' "$err" | grep -qF -- "$1 is regenerated from per-task frontmatter" && hit=yes
  if [ "$rc" -eq 0 ] && [ "$hit" = "$2" ]; then ok "$3"; else bad "$3 (rc=$rc, stderr: $err)"; fi
  rm -rf "$r"
}
index_warn tasks/INDEX.md          yes "hand-edit of tasks/INDEX.md warns"
index_warn .starter/tasks/INDEX.md yes "hand-edit of the starter ledger's INDEX.md warns"
index_warn tasks/T-001.md          no  "a task file doesn't warn"
index_warn tasks/INDEX.md.orig     no  "INDEX.md.orig doesn't warn"
index_warn mytasks/INDEX.md        no  "mytasks/INDEX.md doesn't warn"

echo
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
