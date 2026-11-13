#!/usr/bin/env sh
# ci-tldw-tests.sh — run tldw's own test suite in CI, and fail on anything but a
# full pass (T-003).
#
# WHY this exists: the starter's CI checks the procedure (hooks, placeholders, task
# IDs); it doesn't run the product's tests. AGENTS.md § 8 says every test runs in CI.
# Both CI files call this one script, so the two can't drift apart.
#
# A green pytest exit isn't enough on its own: the extension's escaping tests
# `skipif` node is missing, and a skipped test passes. So the JUnit report must show
# no failures, errors or skips, and at least MIN_TESTS tests (a dropped test file
# would otherwise pass silently too).
#
# Usage: sh scripts/ci-tldw-tests.sh run <workdir>
#          hash-locked install into <workdir>/venv, run pytest, then check the report.
#        sh scripts/ci-tldw-tests.sh check <junit.xml> [<min-tests>]
#          check a pytest JUnit report only (scripts/test/test-ci-tldw-tests.sh).
# Exit:  0 a full pass; 1 anything else, including a report that can't be read.
#
# Supply chain: every package comes from scripts/requirements-ci.txt, hash-locked and
# binary-only; tldw itself installs with --no-deps --no-build-isolation, so nothing
# else is fetched. No credentials or secrets are needed or used.

set -eu
LC_ALL=C; export LC_ALL

MIN_TESTS=723

die() { printf 'ci-tldw-tests: %s\n' "$*" >&2; exit 1; }

check() {
  report=$1 min=$2
  [ -f "$report" ] || die "no test report at $report: pytest didn't write one."
  python3 - "$report" "$min" <<'PY'
import sys
import xml.etree.ElementTree as ET

path, minimum = sys.argv[1], int(sys.argv[2])
try:
    root = ET.parse(path).getroot()
except (ET.ParseError, OSError) as exc:
    sys.exit(f"ci-tldw-tests: can't read the test report {path}: {exc}")
suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
if not suites:
    sys.exit(f"ci-tldw-tests: the test report {path} has no test suites")
totals = {"tests": 0, "failures": 0, "errors": 0, "skipped": 0}
for suite in suites:
    for key in totals:
        # pytest always writes all four; a missing one is a report we can't trust.
        value = suite.get(key)
        if value is None:
            sys.exit(f"ci-tldw-tests: a test suite in the report has no {key}= count")
        try:
            totals[key] += int(value)
        except ValueError:
            sys.exit(f"ci-tldw-tests: the test report's {key}= isn't a number")
print("tests={tests} failures={failures} errors={errors} skipped={skipped}".format(**totals))
problems = [f"{totals[k]} {k}" for k in ("failures", "errors", "skipped") if totals[k]]
if totals["tests"] < minimum:
    problems.append(f"only {totals['tests']} tests ran, expected at least {minimum}")
if problems:
    sys.exit("ci-tldw-tests: not a full pass: " + "; ".join(problems))
PY
}

case "${1:-}" in
  check)
    [ "$#" -ge 2 ] || die "usage: ci-tldw-tests.sh check <junit.xml> [<min-tests>]"
    check "$2" "${3:-$MIN_TESTS}"
    ;;
  run)
    [ "$#" -eq 2 ] || die "usage: ci-tldw-tests.sh run <workdir>"
    work=$2
    mkdir -p "$work"
    python3 --version
    node --version || die "node is required: the extension tests run in it."
    python3 -m venv "$work/venv"
    py="$work/venv/bin/python"
    "$py" -m pip install --disable-pip-version-check --no-input \
      --require-hashes --only-binary :all: -r scripts/requirements-ci.txt
    "$py" -m pip install --disable-pip-version-check --no-input \
      --no-deps --no-build-isolation -e .
    "$py" -m pip freeze
    rc=0
    "$py" -m pytest -p no:cacheprovider -rs --junitxml="$work/junit.xml" || rc=$?
    # Check the report even when pytest failed, so the summary line is always shown.
    check "$work/junit.xml" "$MIN_TESTS" || rc=1
    exit "$rc"
    ;;
  *)
    die "usage: ci-tldw-tests.sh run <workdir> | check <junit.xml> [<min-tests>]"
    ;;
esac
