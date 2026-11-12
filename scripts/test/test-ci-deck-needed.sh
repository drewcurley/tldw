#!/usr/bin/env sh
# Fixture test for scripts/ci-deck-needed.sh and the "deck-gate" block in both CI files
# (starter T-022): only a confirmed PR build that changes nothing the deck suite reads
# may skip the deck job, the decision comes from the target branch's copy of the
# script, and anything unclear builds the deck.
#
# Stack-neutral: POSIX sh + git (+ jq, which the GitHub path reads the PR head with).
# Builds throwaway repos with real --no-ff merges, the shape both CI systems check out
# for a PR build. No network.
#
# Run:  sh scripts/test/test-ci-deck-needed.sh
#       SH=dash sh scripts/test/test-ci-deck-needed.sh   (run the script under dash)
# Exit: 0 all pass, 1 any failure.

set -u
LC_ALL=C; export LC_ALL

test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
playbook_src=$(CDPATH='' cd -- "$test_dir/../.." && pwd)
script="$playbook_src/scripts/ci-deck-needed.sh"
SH="${SH:-sh}"

tmp=$(mktemp -d 2>/dev/null || mktemp -d -t deckneed)
trap 'rm -rf "$tmp"' EXIT

# Ignore the runner's git config and any CI build variables the script reads.
mkdir -p "$tmp/home" "$tmp/xdg"
HOME="$tmp/home" XDG_CONFIG_HOME="$tmp/xdg"
GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1
export HOME XDG_CONFIG_HOME GIT_CONFIG_GLOBAL GIT_CONFIG_NOSYSTEM
unset GIT_DIR GIT_INDEX_FILE GIT_WORK_TREE TF_BUILD GITHUB_ACTIONS GITHUB_EVENT_NAME GITHUB_REF \
      GITHUB_EVENT_PATH BUILD_REASON BUILD_SOURCEBRANCH SYSTEM_PULLREQUEST_SOURCECOMMITID

pass=0
fail=0
ok()  { pass=$((pass + 1)); printf "  PASS: %s\n" "$1"; }
bad() { fail=$((fail + 1)); printf "  FAIL: %s\n" "$1" >&2; }

echo "test-ci-deck-needed.sh"

if ! command -v jq >/dev/null 2>&1; then
  bad "jq is required to run these tests"
  echo; echo "Results: $pass passed, $fail failed"; exit 1
fi

triggers=$(sh "$script" --list)

# make_repo [<base script>]: `main` holds every listed file plus README.md, and
# <base script> (default: the real one) as scripts/ci-deck-needed.sh; "none" leaves it
# out. Sets $r.
make_repo() {
  r=$(mktemp -d "$tmp/repo.XXXXXX")
  ( cd "$r" && git init -q && git symbolic-ref HEAD refs/heads/main \
      && git config user.email t@t && git config user.name t && git config commit.gpgsign false ) || return 1
  printf '%s\n' "$triggers" | while IFS= read -r f; do
    mkdir -p "$r/$(dirname "$f")" && echo "base $f" > "$r/$f"
  done
  case "${1:-real}" in
    real) cp "$script" "$r/scripts/ci-deck-needed.sh" ;;
    none) rm -f "$r/scripts/ci-deck-needed.sh" ;;
    *) printf '%s\n' "$1" > "$r/scripts/ci-deck-needed.sh" ;;
  esac
  echo readme > "$r/README.md"; mkdir -p "$r/docs"; echo doc > "$r/docs/ADOPTING.md"
  ( cd "$r" && git add -A && git commit -qm "base T-1" )
}
# pr <shell command>: make a PR from main with <command>, then check out the synthetic
# merge (first parent main, second the PR head), as a CI PR build does. Sets $pr_sha.
pr() {
  ( cd "$r" && git checkout -q -b pr main && sh -c "$1" && git add -A && git commit -qm "pr T-2" ) || return 1
  pr_sha=$(cd "$r" && git rev-parse pr)
  ( cd "$r" && git checkout -q --detach main && git merge -q --no-ff --no-edit pr && git branch -q merged )
}
# decide <gh|az|push|ci|none> [<dir> [<PR head>]]: run the script in <dir> (default $r).
# Sets $rc and $out.
decide() {
  d=${2:-$r} h=${3:-$pr_sha}
  printf '{"pull_request":{"head":{"sha":"%s"}}}\n' "$h" > "$tmp/event.json"
  case "$1" in
    gh) out=$(cd "$d" && GITHUB_EVENT_NAME=pull_request GITHUB_REF=refs/pull/7/merge \
                GITHUB_EVENT_PATH="$tmp/event.json" "$SH" "$script" 2>&1); rc=$? ;;
    az) out=$(cd "$d" && BUILD_REASON=PullRequest BUILD_SOURCEBRANCH=refs/pull/7/merge \
                SYSTEM_PULLREQUEST_SOURCECOMMITID="$h" "$SH" "$script" 2>&1); rc=$? ;;
    push) out=$(cd "$d" && GITHUB_EVENT_NAME=push GITHUB_REF=refs/heads/main "$SH" "$script" 2>&1); rc=$? ;;
    ci) out=$(cd "$d" && BUILD_REASON=IndividualCI BUILD_SOURCEBRANCH=refs/heads/main "$SH" "$script" 2>&1); rc=$? ;;
    none) out=$(cd "$d" && "$SH" "$script" 2>&1); rc=$? ;;
  esac
}
expect_skip() {
  if [ "$rc" -eq 1 ] && printf '%s' "$out" | grep -q '^skip:'; then ok "$1"; else bad "$1 (rc=$rc, out: $out)"; fi
}
expect_run() {
  if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q '^run:'; then ok "$1"; else bad "$1 (rc=$rc, out: $out)"; fi
}

# 1) The list names the deck suite, this script and both CI files; CODEOWNERS covers it.
for f in scripts/test/test-deck.sh scripts/ci-deck-needed.sh azure-pipelines.yml .github/workflows/checks.yml; do
  if printf '%s\n' "$triggers" | grep -qx "$f"; then ok "--list names $f"; else bad "--list names $f"; fi
done
if grep -q '^/scripts/ci-\*\.sh[[:space:]]' "$playbook_src/.github/CODEOWNERS"; then ok "CODEOWNERS routes scripts/ci-*.sh, this script included"
else bad "CODEOWNERS routes scripts/ci-*.sh, this script included"; fi

# 2) A PR that changes nothing the deck suite reads skips, on either CI system.
make_repo; pr "echo more >> README.md"
decide gh; expect_skip "GitHub PR build touching only README.md: skip"
decide az; expect_skip "Azure PR build touching only README.md: skip"

# 3) Each listed file, a rename away from one, a deletion: run.
printf '%s\n' "$triggers" | while IFS= read -r f; do
  make_repo; pr "echo changed >> '$f'"; decide az
  if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -qF "run: $f changed"; then echo "ok|a PR changing $f: run"
  else echo "bad|a PR changing $f: run (rc=$rc, out: $out)"; fi
done > "$tmp/each"
while IFS='|' read -r v msg; do if [ "$v" = ok ]; then ok "$msg"; else bad "$msg"; fi; done < "$tmp/each"
n_list=$(printf '%s\n' "$triggers" | grep -c .); n_each=$(grep -c . "$tmp/each")
if [ "$n_list" -eq "$n_each" ]; then ok "every listed file was tried ($n_each)"
else bad "every listed file was tried ($n_each of $n_list)"; fi
make_repo; pr "git mv scripts/build_deck.py scripts/old_deck.py"
decide gh; expect_run "a rename away from scripts/build_deck.py: run"
make_repo; pr "git rm -q Agentic-Starter-Overview.pptx scripts/build_deck.py scripts/requirements-deck.txt"
decide gh; expect_run "a PR deleting the deck files: run"

# 4) Near-misses that aren't the listed paths: skip.
make_repo; pr "echo x >> docs/ADOPTING.md && echo x > ADOPTING.md.bak && echo x > scripts/build_deck.py.orig"
decide az; expect_skip "docs/ADOPTING.md, ADOPTING.md.bak, build_deck.py.orig: skip"

# 5) Anything but a confirmed PR build runs, even on the same merge commit.
make_repo; pr "echo more >> README.md"
decide push; expect_run "a merge commit pushed to main (GitHub push): run"
decide ci; expect_run "a merge commit built by Azure CI: run"
decide none; expect_run "no CI variables: run"
out=$(cd "$r" && GITHUB_EVENT_NAME=pull_request GITHUB_REF=refs/heads/main GITHUB_EVENT_PATH="$tmp/event.json" "$SH" "$script" 2>&1); rc=$?
expect_run "a pull_request build on a branch ref, not the merge ref: run"
out=$(cd "$r" && BUILD_REASON=PullRequest BUILD_SOURCEBRANCH=refs/heads/main \
        SYSTEM_PULLREQUEST_SOURCECOMMITID="$pr_sha" "$SH" "$script" 2>&1); rc=$?
expect_run "an Azure PR build on a branch ref, not the merge ref: run"
out=$(cd "$r" && BUILD_REASON=PullRequest BUILD_SOURCEBRANCH=refs/pull/7/merge "$SH" "$script" 2>&1); rc=$?
expect_run "an Azure PR build with no PR head commit: run"
decide az "$r" 0000000000000000000000000000000000000000; expect_run "HEAD's second parent isn't the PR head: run"
( cd "$r" && git checkout -q pr )
decide az; expect_run "HEAD is the PR head itself, not a merge: run"

# 6) Shallow checkouts: two commits deep decides; one deep can't, so runs.
make_repo; pr "echo more >> README.md"
git clone -q --depth 2 --branch merged "file://$r" "$tmp/shallow2" 2>/dev/null
decide az "$tmp/shallow2"; expect_skip "a two-deep shallow clone still decides (skip)"
git clone -q --depth 1 --branch merged "file://$r" "$tmp/shallow1" 2>/dev/null
decide az "$tmp/shallow1"; expect_run "a one-deep shallow clone: run"

# 7) A root commit, and a git failure, run.
r=$(mktemp -d "$tmp/root.XXXXXX")
( cd "$r" && git init -q && git config user.email t@t && git config user.name t && echo a > a && git add -A && git commit -qm "root T-1" )
decide az "$r" "$(cd "$r" && git rev-parse HEAD)"; expect_run "a root commit: run"
make_repo; pr "echo more >> README.md"
real_git=$(command -v git); mkdir -p "$tmp/stub"
cat > "$tmp/stub/git" <<EOF
#!/bin/sh
case "\$1" in diff) echo "stub: git diff failed" >&2; exit 128 ;; esac
exec "$real_git" "\$@"
EOF
chmod +x "$tmp/stub/git"
out=$(cd "$r" && PATH="$tmp/stub:$PATH" BUILD_REASON=PullRequest BUILD_SOURCEBRANCH=refs/pull/7/merge \
        SYSTEM_PULLREQUEST_SOURCECOMMITID="$pr_sha" "$SH" "$script" 2>&1); rc=$?
expect_run "git diff failing: run, never an empty skip"
cat > "$tmp/stub/grep" <<'EOF'
#!/bin/sh
echo "stub: grep failed" >&2; exit 2
EOF
chmod +x "$tmp/stub/grep"; rm -f "$tmp/stub/git"
out=$(cd "$r" && PATH="$tmp/stub:$PATH" BUILD_REASON=PullRequest BUILD_SOURCEBRANCH=refs/pull/7/merge \
        SYSTEM_PULLREQUEST_SOURCECOMMITID="$pr_sha" "$SH" "$script" 2>&1); rc=$?
expect_run "grep failing: run, never an empty skip"

# 8) The deck-gate block: identical in both CI files; it runs the TARGET's copy and only
#    an explicit skip verdict writes the marker.
gh_ci=.github/workflows-disabled/checks.yml
[ -f "$playbook_src/.github/workflows/checks.yml" ] && gh_ci=.github/workflows/checks.yml
blk_az=$(sed -n '/# BEGIN deck-gate/,/# END deck-gate/p' "$playbook_src/azure-pipelines.yml" | sed 's/^[[:space:]]*//')
blk_gh=$(sed -n '/# BEGIN deck-gate/,/# END deck-gate/p' "$playbook_src/$gh_ci" | sed 's/^[[:space:]]*//')
if [ -n "$blk_az" ] && [ "$blk_az" = "$blk_gh" ]; then ok "deck-gate block present and identical in both CI files"
else bad "deck-gate block present and identical in both CI files"; fi
# The marker the gate writes is the one the suite step reads, in each file.
for pair in "azure-pipelines.yml|DECK_SKIP_FILE: \$(Agent.TempDirectory)/deck-skip|t=\"\$(Agent.TempDirectory)\"" \
            "$gh_ci|DECK_SKIP_FILE: \${{ runner.temp }}/deck-skip|t=\"\$RUNNER_TEMP\""; do
  f=${pair%%|*}; rest=${pair#*|}; want_env=${rest%%|*}; want_t=${rest#*|}
  # shellcheck disable=SC2016  # the $t is the block's literal text
  if grep -qF "$want_env" "$playbook_src/$f" && grep -qF "$want_t" "$playbook_src/$f" \
     && printf '%s' "$blk_az" | grep -qF ': > "$t/deck-skip"'
  then ok "$f: the gate's marker is the file the suite step reads"
  else bad "$f: the gate's marker is the file the suite step reads"; fi
done
# gate: run the block in $r for an Azure PR build. Sets $rc, $out, $marked (yes/no).
gate() {
  rm -rf "$tmp/t"; mkdir -p "$tmp/t"
  # shellcheck disable=SC2016  # literal script text for the block's preamble
  { printf 'set -e\nt="$1"\n'; printf '%s\n' "$blk_az"; } > "$tmp/gate.sh"
  out=$(cd "$r" && BUILD_REASON=PullRequest BUILD_SOURCEBRANCH=refs/pull/7/merge \
          SYSTEM_PULLREQUEST_SOURCECOMMITID="$pr_sha" "$SH" "$tmp/gate.sh" "$tmp/t" 2>&1); rc=$?
  if [ -e "$tmp/t/deck-skip" ]; then marked=yes; else marked=no; fi
}
make_repo; pr "echo more >> README.md"; gate
if [ "$rc" -eq 0 ] && [ "$marked" = yes ]; then ok "deck-gate: a skip verdict writes the marker"
else bad "deck-gate: a skip verdict writes the marker (rc=$rc, out: $out)"; fi
make_repo none; pr "echo more >> README.md"; gate
if [ "$rc" -eq 0 ] && [ "$marked" = no ] && printf '%s' "$out" | grep -qF "target branch has no scripts/ci-deck-needed.sh"
then ok "deck-gate: no script on the target: builds"; else bad "deck-gate: no script on the target: builds (rc=$rc, out: $out)"; fi
for broken in 'if then fi' 'echo "skip: forged"; exit 2' 'echo "skip: forged"; exit 0' 'echo "maybe"; exit 1'; do
  make_repo "$broken"; pr "echo more >> README.md"; gate
  if [ "$rc" -eq 0 ] && [ "$marked" = no ]; then ok "deck-gate: target script '$broken' builds"
  else bad "deck-gate: target script '$broken' builds (rc=$rc, marked=$marked, out: $out)"; fi
done
make_repo; pr "printf '#!/bin/sh\necho \"skip: forged\"; exit 1\n' > scripts/ci-deck-needed.sh && echo x >> scripts/build_deck.py"; gate
if [ "$rc" -eq 0 ] && [ "$marked" = no ] && printf '%s' "$out" | grep -q '^run:'
then ok "deck-gate: a PR that rewrites the script to always skip still builds"
else bad "deck-gate: a PR that rewrites the script to always skip still builds (rc=$rc, out: $out)"; fi

echo
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
