#!/usr/bin/env sh
# ci-checks.sh — server-side mirror of .githooks/, shared by every CI system.
#
# WHY this exists: the procedure gates must run identically whether CI is GitHub
# Actions, Azure DevOps, or anything else. Keeping the logic here (not inline in a
# pipeline YAML) means the platforms can't drift. Each pipeline just calls this
# script with the diff range; the PR-body-template check is platform-specific
# (different APIs) and stays in the pipeline.
#
# Usage:   sh scripts/ci-checks.sh <base-sha> <head-sha>
#   <base-sha>  merge-base / target-branch tip (range start, exclusive), or the
#               empty-tree SHA (`git hash-object -t tree /dev/null`) for a root
#               commit: then everything reachable from <head-sha> is checked.
#               scripts/ci-base.sh picks this for push builds.
#   <head-sha>  tip being validated (range end)
# Exit:    0 all checks pass, 1 any failure. Prints findings to stderr.
#
# Mirrors: .githooks/pre-commit (placeholders, secrets) + .githooks/commit-msg
#          (task ID, /docs consensus). Keep the three in sync.
#
# Keep this ONE self-contained file (no sourcing siblings): PR builds extract just
# this file from the target branch and run it (scripts/ci-pr-checks.sh, starter T-014).
# CI_PLACEHOLDER_ALLOW=<file> replaces ./.placeholder-allow (ci-pr-checks.sh passes
# the target branch's copy, so a PR can't allowlist itself); set but empty or
# missing means nothing is allowlisted.

set -u
# Bytes, not characters: BSD sed/grep stop on invalid UTF-8, which binary content
# in a --text diff is (starter T-014).
LC_ALL=C; export LC_ALL

base="${1:?usage: ci-checks.sh <base-sha> <head-sha>}"
head="${2:?usage: ci-checks.sh <base-sha> <head-sha>}"

fail=0
err() { printf 'CI-CHECK FAIL: %s\n' "$*" >&2; fail=1; }
# die: an input every gate reads is unknown, so no gate's result can be trusted.
# Call it from the top-level shell only, never inside $( ) or a pipeline: those are
# subshells, and exit there would not stop this script.
die() { printf 'CI-CHECK FAIL: %s Failing closed: the gates could not all run.\n' "$*" >&2; exit 1; }

# Fail closed on git errors (starter T-013). Each git call is captured on its own and its
# status checked: in `git diff | grep ... || true` the status is grep's, so a git
# error used to read as an empty diff and pass every gate. Filters use sed, which
# exits 0 on no match, so no `|| true` is needed to hide grep's exit 1.
added_only()    { sed -n '/^+++/d;/^+/p'; }
# A path git still quotes ("docs/a\tb.md") counts too, so quoting can't dodge the gate.
authored_docs() { sed -n '/^"\{0,1\}docs\/repos\//d;/^"\{0,1\}docs\//p'; }

# Resolve both ends to full SHAs; later calls use only these, so an argument can't
# reach git as an option (one starting with '-' is refused outright).
hint="Shallow checkout (needs fetchDepth: 0 / fetch-depth: 0), or a force-pushed base?"
base_arg=$base head_arg=$head
case "$base_arg" in -*) die "Base '$base_arg' does not resolve to a commit (looks like an option)." ;; esac
case "$head_arg" in -*) die "Head '$head_arg' does not resolve to a commit (looks like an option)." ;; esac
head=$(git rev-parse --verify --quiet "$head_arg^{commit}") \
  || die "Head '$head_arg' does not resolve to a commit. $hint"
empty_tree=$(git hash-object -t tree /dev/null) || die "git hash-object failed."
if [ "$base_arg" = "$empty_tree" ]; then
  # Root mode: head has no parent to diff against. Check its whole tree and every
  # commit it reaches, not an empty head...head range that passes unscanned.
  from=$empty_tree
  commit_range=$head
else
  base=$(git rev-parse --verify --quiet "$base_arg^{commit}") \
    || die "Base '$base_arg' does not resolve to a commit. $hint"
  # `git diff A...B` means "diff merge-base(A,B) to B"; resolve it explicitly so a
  # missing merge base is an error, not an empty diff.
  from=$(git merge-base "$base" "$head") \
    || die "No merge base between base $base and head $head. $hint"
  commit_range="$base..$head"
fi

rng="$from..$head"
# --text --no-textconv --no-ext-diff: a .gitattributes entry (-diff, binary, a
# textconv driver), an external diff, or a NUL byte must not turn content into
# "Binary files differ" or anything else the scans can't see (starter T-014).
# --no-renames: a rename shows as delete + add, so the moved content is scanned
# (a rename out of an allowlisted path would otherwise carry its placeholders along).
full_diff=$(git diff --text --no-textconv --no-ext-diff --no-renames "$from" "$head" -U0) || die "git diff $rng failed."
added=$(printf '%s\n' "$full_diff" | added_only) || die "Filtering the diff of $rng failed."

# --- 1) Unreplaced [insert ... here] placeholders (allowlist-aware) ----------
allow_file="${CI_PLACEHOLDER_ALLOW-.placeholder-allow}"
allowed() {
  [ -f "$allow_file" ] || return 1
  while IFS= read -r pat; do
    case "$pat" in ''|\#*) continue ;; esac
    # shellcheck disable=SC2254  # glob match intended
    case "$1" in $pat) return 0 ;; esac
  done < "$allow_file"
  return 1
}
# Filenames come from the PR. List them unquoted (core.quotePath=false), split on
# newlines only, never glob-expand them (set -f), and read each back as a literal
# path (--literal-pathspecs): otherwise `a b.md`, `AGENT[S].md`, `:(top)x` or
# `café.md` is checked as some other path, or none (starter T-014). Names git still quotes
# (tab, newline, quote, backslash) can't be read back reliably, so they fail.
changed_files=$(git -c core.quotePath=false diff --no-renames --name-only --diff-filter=ACMR "$from" "$head") \
  || die "git diff --name-only $rng failed."
old_ifs=$IFS
IFS='
'
set -f
for f in $changed_files; do
  case "$f" in \"*) err "Unusual filename $f: git quotes it, so its placeholders can't be checked. Rename it."; continue ;; esac
  allowed "$f" && continue
  f_diff=$(git --literal-pathspecs diff --text --no-textconv --no-ext-diff --no-renames "$from" "$head" -U0 -- "$f") \
    || die "git diff $rng -- '$f' failed."
  f_added=$(printf '%s\n' "$f_diff" | added_only) || die "Filtering the diff of '$f' failed."
  ph_rc=0
  printf '%s\n' "$f_added" | grep -qE '\[insert [^]]+ here\]' || ph_rc=$?
  case "$ph_rc" in
    0)
      # Never put $f inside a sed script: a filename like `x#;e cmd;#` would run
      # cmd under GNU sed (starter T-014). printf treats it as data.
      printf '%s\n' "$f_added" | grep -nE '\[insert [^]]+ here\]' \
        | while IFS= read -r hit; do printf '  %s: %s\n' "$f" "$hit"; done >&2
      err "Unreplaced [insert ... here] placeholder in '$f' (not allowlisted; see .placeholder-allow).${CI_PLACEHOLDER_ALLOW+ The allowlist is read from the target branch, so a new entry must land in an earlier PR.}"
      ;;
    1) ;;
    *) err "Placeholder scanner error (grep exit $ph_rc) on '$f'. Failing closed: file was not scanned." ;;
  esac
done
set +f
IFS=$old_ifs

# --- 2) Basic secret scan ----------------------------------------------------
secret_patterns='AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|gh[ps]_[A-Za-z0-9]{36,}|xox[abprs]-[A-Za-z0-9-]+|sk-[A-Za-z0-9]{20,}|sk-ant-[A-Za-z0-9_-]{20,}'
# `--` guards against a pattern parsed as a grep option; grep exit >=2 is a
# scanner error, not "no match" — fail closed (mirrors scan() in pre-commit).
secret_rc=0
printf '%s\n' "$added" | grep -qE -- "$secret_patterns" || secret_rc=$?
case "$secret_rc" in
  0) err "Possible secret detected in diff. Sanitize before merge." ;;
  1) ;;
  *) err "Secret scanner error (grep exit $secret_rc). Failing closed: diff was not scanned." ;;
esac

# --- 3) Every non-merge commit references a Task ID --------------------------
commits=$(git log --format=%H "$commit_range") || die "git log $commit_range failed."
for sha in $commits; do
  msg=$(git log -1 --format=%B "$sha") || die "git log -1 $sha failed."
  first=$(printf '%s' "$msg" | head -n1)
  case "$first" in
    Merge*|Revert*|fixup!*|squash!*|amend!*) continue ;;
  esac
  if ! printf '%s' "$msg" | grep -qE '\bT-[0-9]+(\.[0-9]+)?\b'; then
    err "Commit $sha is missing a Task ID (T-NNN). First line: $first"
  fi
done

# --- 4) /docs/* changes require unanimous-consensus assertion ---------------
# Exception: docs/repos/** is a GENERATED mirror (scripts/aggregate-docs.sh) whose
# content was already gated by consensus in its SOURCE repo — re-gating the mirror
# would demand an assertion on every routine rollup refresh. So a commit needs the
# assertion only if it touches authored /docs files OUTSIDE docs/repos/.
all_changed=$(git -c core.quotePath=false diff --no-renames --name-only "$from" "$head") || die "git diff --name-only $rng failed."
docs_changed=$(printf '%s\n' "$all_changed" | authored_docs) || die "Filtering the changed-file list failed."
if [ -n "$docs_changed" ]; then
  for sha in $commits; do
    sha_files=$(git -c core.quotePath=false show --no-renames --name-only --pretty="" "$sha") || die "git show $sha failed."
    files=$(printf '%s\n' "$sha_files" | authored_docs) || die "Filtering the files of $sha failed."
    [ -z "$files" ] && continue
    msg=$(git log -1 --format=%B "$sha") || die "git log -1 $sha failed."
    if ! printf '%s' "$msg" | grep -qiE 'unanimous-consensus:[[:space:]]*T-[0-9]+(\.[0-9]+)?'; then
      err "Commit $sha touches authored /docs/* but is missing 'unanimous-consensus: T-NNN' in the message."
    fi
  done
fi

if [ "$fail" -eq 0 ]; then
  echo "ci-checks: all procedure gates passed ($rng)"
fi
exit "$fail"
