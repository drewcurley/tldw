#!/usr/bin/env sh
# Fixture test for scripts/ci-checks.sh.
#
# Stack-neutral: POSIX sh + git only. Builds throwaway git repos in a temp dir,
# makes commits that should pass/fail each gate, and asserts ci-checks.sh exits
# accordingly. Deterministic (no network, no wall-clock, no random).
#
# Run:  sh scripts/test/test-ci-checks.sh
#       CI_CHECKS=<path> sh scripts/test/test-ci-checks.sh   (test another copy)
#       SH=dash sh scripts/test/test-ci-checks.sh            (run it under dash)
# Exit: 0 all pass, 1 any failure.

set -u

test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
playbook_src=$(CDPATH='' cd -- "$test_dir/../.." && pwd)
sut="${CI_CHECKS:-$playbook_src/scripts/ci-checks.sh}"
case "$sut" in /*) ;; *) sut="$PWD/$sut" ;; esac
SH="${SH:-sh}"

# Stubs and scratch files live outside the fixture repos (make_repo runs git add -A).
tmp=$(mktemp -d 2>/dev/null || mktemp -d -t cc)
trap 'rm -rf "$tmp"' EXIT

# Ignore the runner's own git config, attributes and any inherited repo pointers.
mkdir -p "$tmp/home" "$tmp/xdg"
HOME="$tmp/home" XDG_CONFIG_HOME="$tmp/xdg"
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1 GIT_ATTR_NOSYSTEM=1
export HOME XDG_CONFIG_HOME GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM GIT_ATTR_NOSYSTEM
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE GIT_EXTERNAL_DIFF

pass=0
fail=0
ok()  { pass=$((pass + 1)); printf "  PASS: %s\n" "$1"; }
bad() { fail=$((fail + 1)); printf "  FAIL: %s\n" "$1" >&2; }
# expect_rc <actual> <pass-if:0|nonzero> <description>
expect_rc() {
  case "$2" in
    0)       if [ "$1" -eq 0 ]; then ok "$3"; else bad "$3"; fi ;;
    nonzero) if [ "$1" -ne 0 ]; then ok "$3"; else bad "$3"; fi ;;
  esac
}

# Build a repo with a baseline commit; echo its path. Copies the real ci-checks.sh
# and a .placeholder-allow so allowlist behavior matches production.
make_repo() {
  root=$(mktemp -d 2>/dev/null || mktemp -d -t cc)
  mkdir -p "$root/scripts"
  cp "$sut" "$root/scripts/ci-checks.sh"
  printf '# allowlist\nREADME.md\n' > "$root/.placeholder-allow"
  (
    cd "$root" && git init -q && git config user.email t@t && git config user.name t \
      && git config commit.gpgsign false
    echo "baseline" > file.txt
    git add -A && git commit -qm "baseline T-000"
  )
  echo "$root"
}

# run_checks <root>: run ci-checks.sh over base(first commit)..head(HEAD); echo rc.
run_checks() {
  ( cd "$1" \
    && base=$(git rev-list --max-parents=0 HEAD) \
    && head=$(git rev-parse HEAD) \
    && "$SH" scripts/ci-checks.sh "$base" "$head" >/dev/null 2>&1; echo $? )
}

echo "test-ci-checks.sh"

# 1) Clean change with a Task ID passes.
r=$(make_repo)
( cd "$r" && echo "clean line" >> file.txt && git add -A && git commit -qm "feat: clean T-101" )
rc=$(run_checks "$r"); expect_rc "$rc" 0 "clean change passes"
rm -rf "$r"

# 2) Unreplaced placeholder fails.
r=$(make_repo)
( cd "$r" && printf 'use [insert thing here] now\n' >> file.txt && git add -A && git commit -qm "feat: ph T-102" )
rc=$(run_checks "$r"); expect_rc "$rc" nonzero "placeholder in code fails"
rm -rf "$r"

# 3) Placeholder in an allowlisted file passes.
r=$(make_repo)
( cd "$r" && printf 'doc with [insert thing here]\n' >> README.md && git add -A && git commit -qm "docs: ph in allowlisted T-103" )
rc=$(run_checks "$r"); expect_rc "$rc" 0 "placeholder in allowlisted file passes"
rm -rf "$r"

# 4) Secret (fake AWS key) fails. Build the literal at runtime so this test file
#    itself never contains a string the secret-scanner (hook/CI) would flag.
r=$(make_repo)
fake_key="AKIA$(printf 'IOSFODNN7EXAMPLE')"
( cd "$r" && printf 'key=%s\n' "$fake_key" >> file.txt && git add -A && git commit -qm "chore: secret T-104" )
rc=$(run_checks "$r"); expect_rc "$rc" nonzero "secret pattern fails"
rm -rf "$r"

# 5) Commit missing a Task ID fails.
r=$(make_repo)
( cd "$r" && echo "x" >> file.txt && git add -A && git commit -qm "feat: no task id here" )
rc=$(run_checks "$r"); expect_rc "$rc" nonzero "missing Task ID fails"
rm -rf "$r"

# 6) docs/* change without consensus assertion fails.
r=$(make_repo)
( cd "$r" && mkdir -p docs && echo "doc" >> docs/x.md && git add -A && git commit -qm "docs: change T-106" )
rc=$(run_checks "$r"); expect_rc "$rc" nonzero "docs change without consensus fails"
rm -rf "$r"

# 7) docs/* change WITH consensus assertion passes.
r=$(make_repo)
( cd "$r" && mkdir -p docs && echo "doc" >> docs/x.md && git add -A \
   && git commit -qm "docs: change T-107

unanimous-consensus: T-107" )
rc=$(run_checks "$r"); expect_rc "$rc" 0 "docs change with consensus passes"
rm -rf "$r"

# 8) Private-key block fails. Its pattern starts with '-' (which silently disabled
#    the pre-commit scan, starter T-012); assembled at runtime for the same reason as 4).
r=$(make_repo)
d=-----
( cd "$r" && printf '%sBEGIN RSA PRIVATE KEY%s\n' "$d" "$d" >> file.txt && git add -A && git commit -qm "chore: key T-108" )
err=$( cd "$r" \
  && base=$(git rev-list --max-parents=0 HEAD) && head=$(git rev-parse HEAD) \
  && "$SH" scripts/ci-checks.sh "$base" "$head" 2>&1 >/dev/null ); rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$err" | grep -qF "Possible secret detected"; then ok "private key block fails"
else bad "private key block fails (rc=$rc, stderr: $err)"; fi
rm -rf "$r"

# 9) Secret scan fails closed: a grep that errors (exit 2) on the secret pattern
#    must fail the check, not read as "no match".
r=$(make_repo)
real_grep=$(command -v grep)
mkdir -p "$r/.stub"
cat > "$r/.stub/grep" <<EOF
#!/bin/sh
for a in "\$@"; do case "\$a" in *PRIVATE*) echo "grep: stub error" >&2; exit 2 ;; esac; done
exec "$real_grep" "\$@"
EOF
chmod +x "$r/.stub/grep"
( cd "$r" && echo "clean line" >> file.txt && git add file.txt && git commit -qm "feat: clean T-109" )
err=$( cd "$r" \
  && base=$(git rev-list --max-parents=0 HEAD) && head=$(git rev-parse HEAD) \
  && PATH="$r/.stub:$PATH" "$SH" scripts/ci-checks.sh "$base" "$head" 2>&1 >/dev/null ); rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$err" | grep -qF "Secret scanner error"; then ok "grep error fails closed"
else bad "grep error fails closed (rc=$rc, stderr: $err)"; fi
rm -rf "$r"

# --- Fail closed on git errors (starter T-013) ----------------------------------------
# Each case asserts: exit 1, ci-checks' own message, and no "all gates passed".

# make_stub <cmd> <exit>: a dir holding a <cmd> that, on the $STUB_NTH-th (default
# 1st) call whose argument string matches the glob $STUB_FAIL, prints an error,
# touches $STUB_HIT and exits <exit>; every other call runs the real <cmd>. Put it
# on PATH only for the script under test.
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
make_stub grep 2
make_stub sed 2

# run_ci <root> <base> <head> [<stubbed-cmd> <glob> [<nth>]]: run ci-checks.sh;
# sets $rc and $out (stdout + stderr).
run_ci() {
  rm -f "$tmp/hit" "$tmp/hit.n"
  if [ $# -ge 5 ]; then
    out=$( cd "$1" && PATH="$tmp/stub-$4:$PATH" STUB_FAIL="$5" STUB_NTH="${6:-1}" STUB_HIT="$tmp/hit" \
      "$SH" scripts/ci-checks.sh "$2" "$3" 2>&1 ); rc=$?
  else
    out=$( cd "$1" && "$SH" scripts/ci-checks.sh "$2" "$3" 2>&1 ); rc=$?
  fi
}

# expect_closed <needle> <description> [stubbed]: exit 1 naming <needle>, no pass
# line, and (when a stub was used) proof the targeted call actually ran.
expect_closed() {
  if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -qF -- "$1" \
     && ! printf '%s' "$out" | grep -qF "all procedure gates passed" \
     && { [ $# -lt 3 ] || [ -e "$tmp/hit" ]; }
  then ok "$2"; else bad "$2 (rc=$rc, out: $out)"; fi
}

# A repo whose range touches file.txt and an authored docs file, so every git
# call in ci-checks.sh is reached. Sets $r, $b (base), $h (head).
range_repo() {
  r=$(make_repo)
  ( cd "$r" && mkdir -p docs && echo "x" >> file.txt && echo "d" > docs/x.md && git add -A \
     && git commit -qm "docs: change T-110

unanimous-consensus: T-110" )
  b=$(cd "$r" && git rev-list --max-parents=0 HEAD); h=$(cd "$r" && git rev-parse HEAD)
}

# 10) Control: the range used below passes, with empty stderr, when nothing fails.
range_repo
err=$( cd "$r" && "$SH" scripts/ci-checks.sh "$b" "$h" 2>&1 >/dev/null ); rc=$?
if [ "$rc" -eq 0 ] && [ -z "$err" ]; then ok "fail-closed fixture passes with empty stderr when git works"
else bad "fail-closed fixture passes with empty stderr when git works (rc=$rc, stderr: $err)"; fi

# 11) Base / head that don't resolve to a commit.
run_ci "$r" deadbeef "$h";  expect_closed "Base 'deadbeef' does not resolve" "unresolvable base fails closed"
run_ci "$r" "$b" deadbeef;  expect_closed "Head 'deadbeef' does not resolve" "unresolvable head fails closed"
z=0000000000000000000000000000000000000000
run_ci "$r" "$z" "$h";      expect_closed "does not resolve to a commit" "all-zeros base fails closed"
blob=$(cd "$r" && git hash-object -w file.txt)
run_ci "$r" "$blob" "$h";   expect_closed "does not resolve to a commit" "non-commit (blob) base fails closed"

# 12) A base that looks like an option is never passed to git as one.
run_ci "$r" "--output=$tmp/pwned" "$h"
expect_closed "does not resolve to a commit" "option-like base fails closed"
# git would take the whole range as the path (pwned...<sha>), so look for any pwned*.
leaked=$(find "$tmp" -maxdepth 1 -name 'pwned*')
if [ -z "$leaked" ]; then ok "option-like base is not run as a git option"; else bad "option-like base wrote $leaked"; fi

# 13) No merge base (unrelated histories).
( cd "$r" && cur=$(git symbolic-ref --short HEAD) && git checkout -q --orphan other && git rm -rqf . \
   && echo o > o.txt && git add o.txt && git commit -qm "orphan T-111" && git checkout -q "$cur" )
o=$(cd "$r" && git rev-parse other)
run_ci "$r" "$o" "$h";      expect_closed "No merge base" "unrelated histories fail closed"

# 14) Each git call that feeds a gate fails closed when git errors.
# The range's commit list is just $h, so each gate's git calls happen once, in
# order; the docs gate's `git log -1` is the 2nd one (the task-ID gate's is 1st).
run_ci "$r" "$b" "$h" git 'diff *-U0';                        expect_closed "git diff $b..$h failed" "full diff error fails closed" stub
run_ci "$r" "$b" "$h" git '*--name-only --diff-filter=*'; expect_closed "git diff --name-only $b..$h failed" "changed-file list error fails closed" stub
run_ci "$r" "$b" "$h" git '*diff *-U0 -- *';                   expect_closed "git diff $b..$h -- 'docs/x.md' failed" "per-file diff error fails closed" stub
run_ci "$r" "$b" "$h" git 'log --format=%H *';                expect_closed "git log $b..$h failed" "commit list error fails closed" stub
run_ci "$r" "$b" "$h" git 'log -1 *' 1;                       expect_closed "git log -1 $h failed" "task-ID gate message error fails closed" stub
run_ci "$r" "$b" "$h" git 'log -1 *' 2;                       expect_closed "git log -1 $h failed" "docs gate message error fails closed" stub
run_ci "$r" "$b" "$h" git '*--no-renames --name-only [!-]*';           expect_closed "git diff --name-only $b..$h failed" "docs file list error fails closed" stub
run_ci "$r" "$b" "$h" git '*show *';                           expect_closed "git show $h failed" "per-commit file list error fails closed" stub

# 15) Filters fail closed too: a broken sed can't empty the diff or a file list,
#     and a broken placeholder grep can't read as "no placeholder".
run_ci "$r" "$b" "$h" sed '-n /^+++/*';                       expect_closed "Filtering the diff of $b..$h failed" "diff filter error fails closed" stub
run_ci "$r" "$b" "$h" sed '-n /^"*' 1;                     expect_closed "Filtering the changed-file list failed" "docs file list filter error fails closed" stub
run_ci "$r" "$b" "$h" sed '-n /^"*' 2;                     expect_closed "Filtering the files of $h failed" "per-commit docs filter error fails closed" stub
run_ci "$r" "$b" "$h" grep '*insert*';                        expect_closed "Placeholder scanner error" "placeholder grep error fails closed" stub
rm -rf "$r"

# 16) A grep that errors on the old '^+' line filter can't hide a secret.
r=$(make_repo)
( cd "$r" && printf 'key=AKIA%s\n' "IOSFODNN7EXAMPLE" >> file.txt && git add -A && git commit -qm "chore: secret T-112" )
b=$(cd "$r" && git rev-list --max-parents=0 HEAD); h=$(cd "$r" && git rev-parse HEAD)
run_ci "$r" "$b" "$h" grep '^+'
expect_closed "Possible secret detected" "secret still caught when a line-filter grep would error"
rm -rf "$r"

# 17) Shallow clone that lacks the base fails closed instead of scanning nothing.
r=$(make_repo)
( cd "$r" && echo "x" >> file.txt && git add -A && git commit -qm "feat: x T-113" )
b=$(cd "$r" && git rev-list --max-parents=0 HEAD)
git clone -q --depth 1 "file://$r" "$tmp/shallow"
h=$(cd "$tmp/shallow" && git rev-parse HEAD)
run_ci "$tmp/shallow" "$b" "$h"; expect_closed "Shallow checkout" "shallow clone missing the base fails closed"
rm -rf "$r" "$tmp/shallow"

# 18) Root mode (base = empty-tree SHA, what ci-base.sh prints for a root commit):
#     the whole tree and every commit are checked, not an empty head...head range.
#     ci-checks.sh stays untracked here so its own text isn't part of the tree.
root_repo() {  # root_repo <file-content> <message>: one-commit repo; sets $r, $h
  r=$(mktemp -d 2>/dev/null || mktemp -d -t cc)
  mkdir -p "$r/scripts"; cp "$sut" "$r/scripts/ci-checks.sh"
  ( cd "$r" && git init -q && git config user.email t@t && git config user.name t \
      && git config commit.gpgsign false && printf '%s\n' "$1" > file.txt \
      && git add file.txt && git commit -qm "$2" )
  h=$(cd "$r" && git rev-parse HEAD)
}
et=$(git hash-object -t tree /dev/null)
root_repo "clean" "chore: init T-114"; run_ci "$r" "$et" "$h"
if [ "$rc" -eq 0 ]; then ok "root mode: clean root commit passes"; else bad "root mode: clean root commit passes (rc=$rc, out: $out)"; fi
rm -rf "$r"
root_repo "key=AKIA$(printf 'IOSFODNN7EXAMPLE')" "chore: init T-115"; run_ci "$r" "$et" "$h"
expect_closed "Possible secret detected" "root mode: secret in a root commit fails"
rm -rf "$r"
root_repo "clean" "chore: init, no id"; run_ci "$r" "$et" "$h"
expect_closed "missing a Task ID" "root mode: root commit without a Task ID fails"
rm -rf "$r"

# --- Trusted allowlist and content the diff can't hide (starter T-014) ----------------
ph=$(printf 'use [%s thing here]' insert)
aws="key=AKIA$(printf 'IOSFODNN7EXAMPLE')"

# commit_on <root> <message>: commit everything in <root>; set $b (root) and $h (HEAD).
commit_on() {
  ( cd "$1" && git add -A && git commit -qm "$2" )
  b=$(cd "$1" && git rev-list --max-parents=0 HEAD); h=$(cd "$1" && git rev-parse HEAD)
}

# 19) CI_PLACEHOLDER_ALLOW replaces the working tree's allowlist, both ways.
printf '# none\n' > "$tmp/allow-none"; printf 'x.md\n' > "$tmp/allow-x"
r=$(make_repo); printf '%s\n' "$ph" >> "$r/README.md"; commit_on "$r" "docs: ph T-116"
out=$( cd "$r" && CI_PLACEHOLDER_ALLOW="$tmp/allow-none" "$SH" scripts/ci-checks.sh "$b" "$h" 2>&1 ); rc=$?
expect_closed "allowlist is read from the target branch" "CI_PLACEHOLDER_ALLOW without the file overrides the tree's allowlist"
out=$( cd "$r" && CI_PLACEHOLDER_ALLOW="" "$SH" scripts/ci-checks.sh "$b" "$h" 2>&1 ); rc=$?
expect_closed "Unreplaced" "CI_PLACEHOLDER_ALLOW set but empty allowlists nothing"
rm -rf "$r"
r=$(make_repo); printf '%s\n' "$ph" > "$r/x.md"; commit_on "$r" "docs: ph T-117"
out=$( cd "$r" && CI_PLACEHOLDER_ALLOW="$tmp/allow-x" "$SH" scripts/ci-checks.sh "$b" "$h" 2>&1 ); rc=$?
if [ "$rc" -eq 0 ]; then ok "CI_PLACEHOLDER_ALLOW with the file allowlists it"; else bad "CI_PLACEHOLDER_ALLOW with the file allowlists it (rc=$rc, out: $out)"; fi
rm -rf "$r"

# 20) Nothing can turn a secret into "Binary files differ" or an empty diff:
#     .gitattributes -diff / binary / textconv, an external diff, a NUL byte. Each
#     case first proves plain `git diff` really hides it, so it can't pass vacuously.
# check_hidden <root> <description> [VAR=value]: run plain diff and ci-checks.sh
# (with the optional env assignment), expect the secret hidden then caught.
check_hidden() {
  plain=$( cd "$1" && env ${3:+"$3"} git diff "$b" "$h" -U0 2>&1 ); prc=$?
  if [ "$prc" -ne 0 ] || printf '%s' "$plain" | grep -aq AKIA; then bad "$2: precondition (plain git diff must succeed and hide the secret; rc=$prc)"; fi
  out=$( cd "$1" && env ${3:+"$3"} "$SH" scripts/ci-checks.sh "$b" "$h" 2>&1 ); rc=$?
  expect_closed "Possible secret detected" "$2 can't hide a secret"
  rm -rf "$1"
}
r=$(make_repo); printf 's.cfg -diff\n' > "$r/.gitattributes"; printf '%s\n' "$aws" > "$r/s.cfg"
commit_on "$r" "chore: hidden T-118"; check_hidden "$r" ".gitattributes -diff"
r=$(make_repo); printf 's.cfg binary\n' > "$r/.gitattributes"; printf '%s\n' "$aws" > "$r/s.cfg"
commit_on "$r" "chore: hidden T-119"; check_hidden "$r" ".gitattributes binary"
r=$(make_repo); printf 's.cfg diff=hide\n' > "$r/.gitattributes"; printf '%s\n' "$aws" > "$r/s.cfg"
( cd "$r" && git config diff.hide.textconv true )
commit_on "$r" "chore: hidden T-120"; check_hidden "$r" "a textconv driver"
r=$(make_repo); printf '%s\n' "$aws" > "$r/s.cfg"
commit_on "$r" "chore: hidden T-121"; check_hidden "$r" "an external diff (GIT_EXTERNAL_DIFF)" GIT_EXTERNAL_DIFF=true
r=$(make_repo); printf 'x\000y\n%s\n' "$aws" > "$r/s.dat"
commit_on "$r" "chore: hidden T-122"; check_hidden "$r" "a NUL byte"

# 21) Binary content under a UTF-8 locale still passes (BSD sed/grep reject invalid
#     UTF-8 unless the script runs with LC_ALL=C). Only meaningful on macOS/BSD:
#     GNU sed never fails on these bytes, so on Linux CI this passes regardless.
r=$(make_repo); printf 'img\377\376\001\n' > "$r/logo.png"; commit_on "$r" "feat: logo T-123"
out=$( cd "$r" && LC_ALL=C.UTF-8 "$SH" scripts/ci-checks.sh "$b" "$h" 2>&1 ); rc=$?
if [ "$rc" -eq 0 ]; then ok "binary file under a UTF-8 locale passes"; else bad "binary file under a UTF-8 locale passes (rc=$rc, out: $out)"; fi
rm -rf "$r"

# 22) The per-file placeholder diff can't be hidden by .gitattributes either.
r=$(make_repo); printf 'x.cfg -diff\n' > "$r/.gitattributes"; printf '%s\n' "$ph" > "$r/x.cfg"
commit_on "$r" "chore: hidden ph T-124"
out=$( cd "$r" && "$SH" scripts/ci-checks.sh "$b" "$h" 2>&1 ); rc=$?
expect_closed "Unreplaced [insert ... here] placeholder in 'x.cfg'" "placeholder in a -diff file is caught"
rm -rf "$r"

# 23) Renaming an allowlisted file to a non-allowlisted path carries its placeholder
#     out (--no-renames shows the move as delete + add, so the content is checked).
r=$(make_repo); printf '%s\n' "$ph" > "$r/README.md"; commit_on "$r" "docs: allowlisted ph T-125"
mid=$h; ( cd "$r" && git mv README.md notes.md && git commit -qm "chore: move T-126" ); h=$(cd "$r" && git rev-parse HEAD)
out=$( cd "$r" && "$SH" scripts/ci-checks.sh "$mid" "$h" 2>&1 ); rc=$?
expect_closed "placeholder in 'notes.md'" "rename out of an allowlisted path is caught"
rm -rf "$r"

# 24) Filenames are data: no glob expansion, no pathspec magic, no quoting, no
#     word splitting can make a file's placeholder be checked as another path.
# name_case <filename> <needle> <description>: commit <filename> holding a placeholder
# (README.md, allowlisted, exists so a glob could expand to it); expect a failure.
name_case() {
  r=$(make_repo); echo readme > "$r/README.md"; printf '%s\n' "$ph" > "$r/$1"
  commit_on "$r" "chore: odd name T-127"
  out=$( cd "$r" && "$SH" scripts/ci-checks.sh "$b" "$h" 2>&1 ); rc=$?
  expect_closed "$2" "$3"
  [ ! -e "$r/PWNED" ] || : > "$tmp/PWNED"   # surface a payload that ran in the repo
  rm -rf "$r"
}
name_case 'READM[E].md'  "placeholder in 'READM[E].md'"  "glob-like filename isn't expanded to an allowlisted file"
name_case ':(top)x.md'   "placeholder in ':(top)x.md'"   "pathspec-magic filename is read literally"
name_case "$(printf 'caf\303\251.md')" "placeholder in 'caf" "non-ASCII filename is checked"
name_case 'a b.md'       "placeholder in 'a b.md'"       "filename with a space is checked whole"
name_case "$(printf 'a\tb.md')" "Unusual filename" "filename git must quote fails closed"
# A filename is never code: under GNU sed, `#;e cmd;#` in an interpolated sed script
# runs cmd. Expect the placeholder line reported verbatim and no PWNED file.
name_case 'x#;e touch PWNED;#.md' "  x#;e touch PWNED;#.md: 1:+use [" "filename with sed syntax is reported, not run"
if [ ! -e PWNED ] && [ ! -e "$tmp/PWNED" ]; then ok "filename with sed syntax runs nothing"
else bad "filename with sed syntax ran a command (PWNED exists)"; fi
r=$(make_repo); mkdir -p "$r/docs"; echo d > "$r/docs/$(printf 'caf\303\251.md')"; commit_on "$r" "docs: odd name T-128"
out=$( cd "$r" && "$SH" scripts/ci-checks.sh "$b" "$h" 2>&1 ); rc=$?
expect_closed "touches authored /docs/*" "non-ASCII /docs filename still needs consensus"
rm -rf "$r"
r=$(make_repo); mkdir -p "$r/docs"; echo d > "$r/docs/$(printf 'a\tb.md')"; commit_on "$r" "docs: tab name T-129"
out=$( cd "$r" && "$SH" scripts/ci-checks.sh "$b" "$h" 2>&1 ); rc=$?
expect_closed "touches authored /docs/*" "/docs filename git must quote still needs consensus"
rm -rf "$r"

# 25) Moving a file out of /docs is a /docs change (--no-renames: the delete shows),
#     so it needs the consensus assertion like any other /docs edit.
r=$(make_repo); mkdir -p "$r/docs"; echo d > "$r/docs/x.md"
( cd "$r" && git add -A && git commit -qm "docs: add T-130

unanimous-consensus: T-130" )
mid=$(cd "$r" && git rev-parse HEAD)
( cd "$r" && mkdir notes && git mv docs/x.md notes/x.md && git commit -qm "chore: move doc out T-131" )
h=$(cd "$r" && git rev-parse HEAD)
out=$( cd "$r" && "$SH" scripts/ci-checks.sh "$mid" "$h" 2>&1 ); rc=$?
expect_closed "touches authored /docs/*" "moving a file out of /docs needs consensus"
rm -rf "$r"

echo
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
