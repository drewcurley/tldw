#!/usr/bin/env sh
# The Azure PR-body step (starter T-021 and starter T-022): runs the "pr-body-check" block from
# azure-pipelines.yml with Azure's env vars set and a stub curl on PATH, so the skip,
# fail-closed, fetch-failure, parse-failure and missing-section paths are checked with
# no network. Each case runs under bash (what Azure uses), sh, and dash where present.
#
# Run:  sh scripts/test/test-pr-body-check.sh
#       PR_BODY_ROOT=<dir> sh scripts/test/test-pr-body-check.sh   (check another tree)
# Exit: 0 all pass, 1 any failure.
# shellcheck disable=SC2016  # file-wide: the $(...) strings are Azure macro text, never expanded

set -u
LC_ALL=C; export LC_ALL
# Inside Azure these are already set; every case sets the ones it needs.
unset TF_BUILD BUILD_REASON SYSTEM_PULLREQUEST_PULLREQUESTNUMBER BUILD_REPOSITORY_NAME GITHUB_TOKEN

test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
root="${PR_BODY_ROOT:-$(CDPATH='' cd -- "$test_dir/../.." && pwd)}"

tmp=$(mktemp -d 2>/dev/null || mktemp -d -t prb)
trap 'rm -rf "$tmp"' EXIT

pass=0
fail=0
ok()  { pass=$((pass + 1)); printf "  PASS: %s\n" "$1"; }
bad() { fail=$((fail + 1)); printf "  FAIL: %s\n" "$1" >&2; }

echo "test-pr-body-check.sh"

# The step needs jq, so the suite does too; a missing jq fails here, never skips.
if ! command -v jq >/dev/null 2>&1; then
  bad "jq is required to run these tests"
  echo; echo "Results: $pass passed, $fail failed"; exit 1
fi

yml="$root/azure-pipelines.yml"
n=$(grep -c '# BEGIN pr-body-check' "$yml")
blk=$(sed -n '/# BEGIN pr-body-check/,/# END pr-body-check/p' "$yml" | sed 's/^[[:space:]]*//')
if [ "$n" -eq 1 ] && printf '%s\n' "$blk" | grep -q '# END pr-body-check'; then ok "the pr-body-check block is marked once in azure-pipelines.yml"
else bad "the pr-body-check block is marked once in azure-pipelines.yml (found $n)"; echo; echo "Results: $pass passed, $fail failed"; exit 1; fi

# Azure expands $(name) macros in script text before it runs; the block reads env
# vars instead, so none may appear. A $(GITHUB_TOKEN) there would write the secret
# into the script file.
macros=$(printf '%s\n' "$blk" | grep -o '\$([A-Za-z_][A-Za-z0-9_.]*)' | sort -u | tr '\n' ' ')
if [ -z "$macros" ]; then ok "the block uses no Azure \$(name) macros"
else bad "the block uses no Azure \$(name) macros (found: $macros)"; fi
# Azure's other two expression forms, ${{ }} at compile time and $[ ] at run time.
if printf '%s\n' "$blk" | grep -qF -e '${{' -e '$['; then bad "the block uses no \${{ }} or \$[ ] expressions"
else ok "the block uses no \${{ }} or \$[ ] expressions"; fi

# The token reaches the step only through its env: mapping, right after the block.
# Without it the step would warn and skip forever, and stay green.
n_env=$(awk '/# END pr-body-check/ { f = 1; next }
  f && /^[[:space:]]*- (script|job):/ { f = 0 }
  f && /^[[:space:]]*GITHUB_TOKEN: \$\(GITHUB_TOKEN\)[[:space:]]*$/ { n++ }
  END { print n + 0 }' "$yml")
if [ "$n_env" -eq 1 ]; then ok "the step maps GITHUB_TOKEN through env: once"
else bad "the step maps GITHUB_TOKEN through env: once (found $n_env)"; fi

# The section list matches the GitHub workflow's (active, else parked).
gh_ci="$root/.github/workflows-disabled/checks.yml"
[ -f "$root/.github/workflows/checks.yml" ] && gh_ci="$root/.github/workflows/checks.yml"
sections() { grep 'for section in' "$1" | sed 's/.*for section in \(.*\); do.*/\1/'; }
n_az=$(grep -c 'for section in' "$yml"); n_gh=$(grep -c 'for section in' "$gh_ci")
if [ "$n_az" -eq 1 ] && [ "$n_gh" -eq 1 ] && [ -n "$(sections "$yml")" ] && [ "$(sections "$yml")" = "$(sections "$gh_ci")" ]
then ok "both CI files check the same template sections"
else bad "both CI files check the same template sections (azure: $n_az line(s), github: $n_gh)"; fi

# --- Running the block ------------------------------------------------------------
mkdir -p "$tmp/bin" "$tmp/nojq"
cat > "$tmp/bin/curl" <<'EOF'
#!/bin/sh
printf '%s\n' "$@" >> "$STUB_LOG"
# Read stdin only when told to (-H @-), so the stub can never hang.
for a in "$@"; do [ "$a" = "@-" ] && cat >> "$STUB_LOG.in"; done
case "${STUB_CURL:-ok}" in
  fail) echo "curl: (22) The requested URL returned error: 401" >&2; exit 22 ;;
  html) printf '<html>a proxy page</html>\n' ;;
  empty) : ;;
  *) cat "$STUB_JSON" ;;
esac
EOF
chmod +x "$tmp/bin/curl"
# A PATH with the stub curl and grep but no jq.
cp "$tmp/bin/curl" "$tmp/nojq/curl"
ln -s "$(command -v grep)" "$tmp/nojq/grep"

full='## Task\nT-1\n## Summary\n## Agent sign-offs\n## Human review\n## Risk\n## Redlines\n'
body_json() { printf '%b' "$1" | jq -Rs '{body: .}' > "$tmp/pr.json"; }
token='tok-SECRET-123'

shells="sh"
command -v bash >/dev/null 2>&1 && shells="bash $shells"
command -v dash >/dev/null 2>&1 && shells="$shells dash"

# run <shell> <reason> <token: unset|empty|literal|set> [curl mode] [path]. Sets rc, out.
# The PR number and repo come from $PRNUM and $REPO when set (else 42 and owner/repo).
printf '%s\n' "$blk" > "$tmp/step.sh"
run() {
  rm -f "$tmp/curl.log" "$tmp/curl.log.in"
  out=$(
    case "$3" in
      (unset) unset GITHUB_TOKEN ;;
      (empty) GITHUB_TOKEN=''; export GITHUB_TOKEN ;;
      (literal) GITHUB_TOKEN='$(GITHUB_TOKEN)'; export GITHUB_TOKEN ;;
      (set) GITHUB_TOKEN=$token; export GITHUB_TOKEN ;;
    esac
    TF_BUILD=True BUILD_REASON=$2 SYSTEM_PULLREQUEST_PULLREQUESTNUMBER="${PRNUM-42}" \
      BUILD_REPOSITORY_NAME="${REPO-owner/repo}" \
      STUB_LOG="$tmp/curl.log" STUB_JSON="$tmp/pr.json" STUB_CURL="${4:-ok}" \
      PATH="${5:-$tmp/bin:$PATH}" "$(command -v "$1")" "$tmp/step.sh" 2>&1 </dev/null
  ); rc=$?
  if printf '%s' "$out" | grep -qF "$token"; then bad "[$1] the token never appears in the output ($2/$3/${4:-ok})"; fi
}
has()  { printf '%s' "$out" | grep -qF -- "$1"; }
missing_lines() { printf '%s\n' "$out" | grep -c 'PR body missing section' || true; }
called() { [ -e "$tmp/curl.log" ]; }

body_json "$full"
for s in $shells; do
  for reason in IndividualCI Manual; do
    run "$s" "$reason" set
    if [ "$rc" -eq 0 ] && has "Not a PR build" && ! called; then ok "[$s] $reason build: skipped"
    else bad "[$s] $reason build: skipped (rc=$rc, out: $out)"; fi
  done

  for mode in unset empty literal; do
    run "$s" PullRequest "$mode"
    if [ "$rc" -eq 0 ] && has "##[warning]GITHUB_TOKEN not set" && ! called; then ok "[$s] token $mode: warns and skips without calling GitHub"
    else bad "[$s] token $mode: warns and skips without calling GitHub (rc=$rc, out: $out)"; fi
  done

  run "$s" PullRequest set fail
  if [ "$rc" -ne 0 ] && has "Couldn't read PR #42 from the GitHub API" && [ "$(missing_lines)" -eq 0 ]
  then ok "[$s] a failed API call fails with its own error, not six missing sections"
  else bad "[$s] a failed API call fails with its own error, not six missing sections (rc=$rc, out: $out)"; fi

  run "$s" PullRequest set html
  if [ "$rc" -ne 0 ] && has "Couldn't parse the GitHub API's reply for PR #42" && [ "$(missing_lines)" -eq 0 ]
  then ok "[$s] a non-JSON reply fails with a parse error"
  else bad "[$s] a non-JSON reply fails with a parse error (rc=$rc, out: $out)"; fi

  run "$s" PullRequest set empty
  if [ "$rc" -ne 0 ] && has "Couldn't parse the GitHub API's reply for PR #42" && [ "$(missing_lines)" -eq 0 ]
  then ok "[$s] an empty reply fails with a parse error"
  else bad "[$s] an empty reply fails with a parse error (rc=$rc, out: $out)"; fi

  run "$s" PullRequest set ok "$tmp/nojq"
  if [ "$rc" -ne 0 ] && has "jq is not installed" && ! called; then ok "[$s] no jq on the agent fails clearly"
  else bad "[$s] no jq on the agent fails clearly (rc=$rc, out: $out)"; fi

  run "$s" PullRequest set
  if [ "$rc" -eq 0 ] && [ "$(missing_lines)" -eq 0 ]; then ok "[$s] a complete body passes"
  else bad "[$s] a complete body passes (rc=$rc, out: $out)"; fi
  if grep -qx 'https://api.github.com/repos/owner/repo/pulls/42' "$tmp/curl.log"
  then ok "[$s] it asks GitHub for this PR"
  else bad "[$s] it asks GitHub for this PR (curl got: $(tr '\n' ' ' < "$tmp/curl.log"))"; fi
  # The token goes in on stdin as the whole header, never in curl's arguments.
  if awk 'prev == "-H" && $0 == "@-" { f = 1 } { prev = $0 } END { exit !f }' "$tmp/curl.log" \
     && ! grep -q 'Bearer' "$tmp/curl.log" \
     && [ "$(cat "$tmp/curl.log.in" 2>/dev/null)" = "Authorization: Bearer $token" ]
  then ok "[$s] the token header reaches curl on stdin, not in its arguments"
  else bad "[$s] the token header reaches curl on stdin, not in its arguments (args: $(tr '\n' ' ' < "$tmp/curl.log"))"; fi
done

# A PR build without a usable PR number or repo name fails before the token is used.
body_json "$full"
for s in $shells; do
  for bad_pr in '' 'abc' '42;x'; do
    PRNUM=$bad_pr; run "$s" PullRequest set; unset PRNUM
    if [ "$rc" -ne 0 ] && has "without a usable PR number" && ! called; then ok "[$s] PR number '$bad_pr' fails closed before calling GitHub"
    else bad "[$s] PR number '$bad_pr' fails closed before calling GitHub (rc=$rc, out: $out)"; fi
  done
  for bad_repo in '' 'owner' 'owner/repo/x' 'own er/repo'; do
    REPO=$bad_repo; run "$s" PullRequest set; unset REPO
    if [ "$rc" -ne 0 ] && has "without a usable owner/repo name" && ! called; then ok "[$s] repo '$bad_repo' fails closed before calling GitHub"
    else bad "[$s] repo '$bad_repo' fails closed before calling GitHub (rc=$rc, out: $out)"; fi
  done
done

for s in $shells; do
  body_json '## Task\n## Summary\n## Agent sign-offs\n## Human review\n## Redlines\n'
  run "$s" PullRequest set
  if [ "$rc" -ne 0 ] && has "missing section: ## Risk" && [ "$(missing_lines)" -eq 1 ]; then ok "[$s] a missing section is named, alone"
  else bad "[$s] a missing section is named, alone (rc=$rc, out: $out)"; fi

  body_json '## Task\n## Summary\n## Agent sign-offs\n## Human review\n'
  run "$s" PullRequest set
  if [ "$rc" -ne 0 ] && has "missing section: ## Risk" && has "missing section: ## Redlines" && [ "$(missing_lines)" -eq 2 ]
  then ok "[$s] two missing sections are both named"
  else bad "[$s] two missing sections are both named (rc=$rc, out: $out)"; fi

  printf '{"body": null}\n' > "$tmp/pr.json"
  run "$s" PullRequest set
  if [ "$rc" -ne 0 ] && [ "$(missing_lines)" -eq 6 ]; then ok "[$s] an empty (null) body misses all six"
  else bad "[$s] an empty (null) body misses all six (rc=$rc, out: $out)"; fi
done

echo
echo "Results: $pass passed, $fail failed"
[ "$fail" -eq 0 ]
