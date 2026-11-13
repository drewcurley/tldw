#!/usr/bin/env sh
# Fixture test for scripts/ci-tldw-tests.sh's report check (T-003).
#
# The check is what stops tldw's CI job passing on skipped tests (the node-driven
# extension tests skip when node is missing) or on a suite that lost test files. Each
# case writes a small pytest-style JUnit report and asserts the verdict.
# Deterministic: no network, no pytest run, POSIX sh + python3 only.
#
# Run:  sh scripts/test/test-ci-tldw-tests.sh
# Exit: 0 all pass, 1 any failure.

set -u

test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
root=$(CDPATH='' cd -- "$test_dir/../.." && pwd)
sut="$root/scripts/ci-tldw-tests.sh"
SH="${SH:-sh}"

tmp=$(mktemp -d 2>/dev/null || mktemp -d -t ctt)
trap 'rm -rf "$tmp"' EXIT

pass=0
fail=0
ok()  { pass=$((pass + 1)); printf "  PASS: %s\n" "$1"; }
bad() { fail=$((fail + 1)); printf "  FAIL: %s\n" "$1" >&2; }

# report <file> <tests> <failures> <errors> <skipped>: one pytest-style suite.
report() {
  printf '<?xml version="1.0" encoding="utf-8"?><testsuites name="pytest tests"><testsuite name="pytest" errors="%s" failures="%s" skipped="%s" tests="%s" time="1.0"></testsuite></testsuites>\n' \
    "$4" "$3" "$5" "$2" > "$1"
}

# expect <rc> <grep-pattern or ""> <description> -- args...
expect() {
  want_rc=$1 want_out=$2 desc=$3
  shift 4
  out=$("$SH" "$sut" "$@" 2>&1); rc=$?
  if [ "$rc" -ne "$want_rc" ]; then
    bad "$desc (rc=$rc, want $want_rc; out: $out)"
  elif [ -n "$want_out" ] && ! printf '%s\n' "$out" | grep -qE -- "$want_out"; then
    bad "$desc (output lacks /$want_out/: $out)"
  else
    ok "$desc"
  fi
}

echo "test-ci-tldw-tests.sh"

f="$tmp/junit.xml"

report "$f" 866 0 0 0
expect 0 'tests=866 failures=0 errors=0 skipped=0' "a full pass at the floor passes" -- check "$f"

report "$f" 900 0 0 0
expect 0 '' "more tests than the floor passes" -- check "$f"

report "$f" 865 0 0 0
expect 1 'only 865 tests ran, expected at least 866' "one test under the floor fails" -- check "$f"

report "$f" 866 0 0 1
expect 1 '1 skipped' "a single skipped test fails the job" -- check "$f"

report "$f" 727 0 0 4
expect 1 '4 skipped' "skips fail even when the total is over the floor" -- check "$f"

report "$f" 866 1 0 0
expect 1 '1 failures' "a failed test fails" -- check "$f"

report "$f" 866 0 2 0
expect 1 '2 errors' "an errored test fails" -- check "$f"

report "$f" 5 0 0 0
expect 0 '' "the floor can be lowered for a fixture" -- check "$f" 5

# Several suites: totals are summed, so a skip in any of them counts.
printf '<testsuites><testsuite tests="433" failures="0" errors="0" skipped="0"/><testsuite tests="433" failures="0" errors="0" skipped="0"/></testsuites>\n' > "$f"
expect 0 'tests=866' "two suites are summed to a pass" -- check "$f"
printf '<testsuites><testsuite tests="431" failures="0" errors="0" skipped="0"/><testsuite tests="433" failures="0" errors="0" skipped="1"/></testsuites>\n' > "$f"
expect 1 '1 skipped' "a skip in the second suite still fails" -- check "$f"

# A bare <testsuite> root (older pytest junit_family) is read too.
printf '<testsuite tests="866" failures="0" errors="0" skipped="0"/>\n' > "$f"
expect 0 'tests=866' "a bare testsuite root is read" -- check "$f"

# Fail closed: anything unreadable is a failure, never a pass.
expect 1 'no test report' "a missing report fails" -- check "$tmp/nope.xml"
printf '<testsuites><testsuite tests="866"' > "$f"
expect 1 "can't read the test report" "a truncated report fails" -- check "$f"
printf '<testsuites></testsuites>\n' > "$f"
expect 1 'no test suites' "a report with no suites fails" -- check "$f"
printf '<testsuites><testsuite tests="lots" failures="0" errors="0" skipped="0"/></testsuites>\n' > "$f"
expect 1 "isn't a number" "a non-numeric count fails" -- check "$f"
: > "$f"
expect 1 "can't read the test report" "an empty report fails" -- check "$f"

# Usage errors fail rather than doing nothing.
expect 1 'usage' "no mode fails" --
expect 1 'usage' "an unknown mode fails" -- frobnicate
expect 1 'usage' "check with no report fails" -- check
expect 1 'usage' "run with no workdir fails" -- run

# A report missing one of the four counts isn't trusted (pytest always writes all four).
printf '<testsuites><testsuite tests="866"/></testsuites>\n' > "$f"
expect 1 'has no failures= count' "a suite with no failure/error/skip counts fails" -- check "$f"
printf '<testsuites><testsuite tests="866" failures="0" errors="0"/></testsuites>\n' > "$f"
expect 1 'has no skipped= count' "a suite with no skipped= count fails" -- check "$f"

# --- run mode -----------------------------------------------------------------
# `run` is where the job's exit code is decided, so it's tested too, with stubs and no
# network: a python3 that fakes `-m venv` (installing a fake venv python) and hands
# the report check (`python3 -`) to the real python3; a venv python that logs pip
# calls and fakes pytest (writes $STUB_REPORT to --junitxml, exits $STUB_PYTEST_RC);
# a node that prints a version. PATH holds only these stubs and mkdir, so a missing
# node is really missing.
real_py=$(command -v python3)
# The stubs run under a PATH of only themselves, so they call real tools by absolute path.
mkdir_bin=$(command -v mkdir); cp_bin=$(command -v cp); chmod_bin=$(command -v chmod); cat_bin=$(command -v cat)
sh_bin=$(command -v "$SH")
stub="$tmp/stub"; bin="$tmp/bin"; bin_nonode="$tmp/bin-nonode"
mkdir -p "$stub" "$bin" "$bin_nonode"
cat > "$stub/venv-python" <<EOF
#!/bin/sh
case "\$1 \$2" in
  "-m pip") echo "pip \$*" >> "\$STUB_LOG"; exit 0 ;;
  "-m pytest")
    echo "pytest \$*" >> "\$STUB_LOG"
    for a in "\$@"; do case "\$a" in --junitxml=*) out=\${a#--junitxml=} ;; esac; done
    if [ -n "\${STUB_REPORT:-}" ]; then "$cat_bin" "\$STUB_REPORT" > "\$out"; fi
    exit "\${STUB_PYTEST_RC:-0}" ;;
esac
echo "stub venv python: unexpected: \$*" >&2; exit 97
EOF
cat > "$stub/python3" <<EOF
#!/bin/sh
case "\$1" in
  --version) echo "Python 3.12.0 (stub)"; exit 0 ;;
  -) exec "$real_py" "\$@" ;;
  -m) if [ "\$2" = venv ]; then "$mkdir_bin" -p "\$3/bin" && "$cp_bin" "$stub/venv-python" "\$3/bin/python" && "$chmod_bin" +x "\$3/bin/python"; exit 0; fi ;;
esac
echo "stub python3: unexpected: \$*" >&2; exit 97
EOF
printf '#!/bin/sh\necho v20.0.0\n' > "$stub/node"
chmod +x "$stub/python3" "$stub/venv-python" "$stub/node"
for d in "$bin" "$bin_nonode"; do
  ln -s "$stub/python3" "$d/python3"; ln -s "$(command -v mkdir)" "$d/mkdir"
done
ln -s "$stub/node" "$bin/node"

# expect_run <rc: 0|nonzero|1> <pattern or ""> <description> <path> <pytest-rc> <report or "">
expect_run() {
  want=$1 pat=$2 desc=$3 path=$4 prc=$5 rep=$6
  work="$tmp/work.$$.$pass.$fail"; log="$tmp/log.$pass.$fail"; : > "$log"
  out=$( cd "$root" && PATH="$path" STUB_LOG="$log" STUB_PYTEST_RC="$prc" STUB_REPORT="$rep" \
         "$sh_bin" "$sut" run "$work" 2>&1 ); rc=$?
  case "$want" in
    nonzero) [ "$rc" -ne 0 ]; good=$? ;;
    *) [ "$rc" -eq "$want" ]; good=$? ;;
  esac
  if [ "$good" -ne 0 ]; then bad "$desc (rc=$rc, want $want; out: $out)"
  elif [ -n "$pat" ] && ! printf '%s\n' "$out" | grep -qE -- "$pat"; then bad "$desc (output lacks /$pat/: $out)"
  else ok "$desc"; fi
}

clean="$tmp/clean.xml"; report "$clean" 866 0 0 0
skips="$tmp/skips.xml"; report "$skips" 866 0 0 4
short="$tmp/short.xml"; report "$short" 12 0 0 0

expect_run 0 'tests=866 failures=0 errors=0 skipped=0' "run: pytest passes with a clean report -> 0" "$bin" 0 "$clean"
# The pip calls are the hash-locked install, then tldw with nothing else fetched.
if grep -q -- '--require-hashes --only-binary :all: -r scripts/requirements-ci.txt' "$log" \
   && grep -q -- '--no-deps --no-build-isolation -e .' "$log"; then
  ok "run: installs hash-locked, binary-only, then tldw with --no-deps --no-build-isolation"
else bad "run: installs hash-locked, binary-only, then tldw with --no-deps --no-build-isolation ($(cat "$log"))"; fi
if grep -q -- '-m pytest -p no:cacheprovider -rs --junitxml=' "$log"; then ok "run: pytest writes a JUnit report"
else bad "run: pytest writes a JUnit report ($(cat "$log"))"; fi
expect_run 1 '4 skipped' "run: pytest exits 0 but tests were skipped -> 1" "$bin" 0 "$skips"
expect_run 1 'only 12 tests ran' "run: pytest exits 0 but too few tests ran -> 1" "$bin" 0 "$short"
expect_run nonzero 'tests=866' "run: pytest fails with a clean-looking report -> nonzero, summary shown" "$bin" 1 "$clean"
expect_run 1 'no test report' "run: pytest fails and writes no report -> 1" "$bin" 1 ""
expect_run 1 'no test report' "run: pytest exits 0 but writes no report -> 1" "$bin" 0 ""
expect_run 1 'node is required' "run: no node on PATH -> 1 before anything installs" "$bin_nonode" 0 "$clean"

# The floor the CI job uses is today's suite size or more, never lowered quietly.
floor=$(sed -n 's/^MIN_TESTS=//p' "$sut")
if [ -n "$floor" ] && [ "$floor" -ge 866 ]; then ok "the CI floor is at least 866 ($floor)"; else bad "the CI floor is '$floor', below 866"; fi

printf '\n%d passed, %d failed\n' "$pass" "$fail"
[ "$fail" -eq 0 ]
