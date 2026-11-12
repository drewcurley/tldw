#!/usr/bin/env sh
# Deck invariants (starter T-016): the overview deck is the artifact most likely to be
# shown to outsiders, so its claims, its layout budget and its match to the generator
# are tested here rather than checked by eye.
#
# Two tiers:
#   STATIC  python3 stdlib only. Always runs: slide count, the dependency lock, the
#           credential-free CI job, the ownership and adoption notes.
#   BUILD   needs python-pptx. Runs the generator in a temp directory and asserts on
#           the real object model: slide text, a text-fit budget computed from shape
#           geometry, and a part-by-part match with the committed .pptx. Skipped
#           locally when python-pptx is absent; required when DECK_BUILD_REQUIRED=1
#           or under CI (TF_BUILD / GITHUB_ACTIONS), so a lost env: block can't skip it.
#
# A repo that dropped the deck (.pptx, generator and lock file all gone) gets a SKIP
# line and exit 0. Dropping only some of them fails. See ADOPTING.md § 1.
# Each assertion also runs against a copy of the tree broken in exactly that way.
#
# Run:  sh scripts/test/test-deck.sh
#       DECK_BUILD_REQUIRED=1 DECK_PYTHON=.venv-deck/bin/python sh scripts/test/test-deck.sh
#       DECK_ROOT=<dir> sh scripts/test/test-deck.sh   (check another tree)
# Exit: 0 all pass, 1 any failure.

set -u
LC_ALL=C; export LC_ALL
PYTHONUTF8=1; export PYTHONUTF8
PYTHONDONTWRITEBYTECODE=1; export PYTHONDONTWRITEBYTECODE

test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
playbook_src=$(CDPATH='' cd -- "$test_dir/../.." && pwd)
# Absolute, because the build tier changes directory.
root=$(CDPATH='' cd -- "${DECK_ROOT:-$playbook_src}" && pwd) || exit 1

pass=0
fail=0
ok()  { pass=$((pass + 1)); printf "  PASS: %s\n" "$1"; }
bad() { fail=$((fail + 1)); printf "  FAIL: %s\n" "$1" >&2; }
finish() {
  echo
  echo "Results: $pass passed, $fail failed"
  [ "$fail" -eq 0 ]
  exit
}

echo "test-deck.sh"

# CI's deck-gate leaves this marker when the target branch's scripts/ci-deck-needed.sh
# says the PR changes nothing this suite reads (starter T-022).
if [ -n "${DECK_SKIP_FILE:-}" ] && [ -e "$DECK_SKIP_FILE" ]; then
  echo "  SKIP: this PR changes no file the deck suite reads (scripts/ci-deck-needed.sh); not building the deck."
  exit 0
fi

# Runs before any Python, so a repo that dropped the deck needs no interpreter at all.
present=0
for f in Agentic-Starter-Overview.pptx scripts/build_deck.py scripts/requirements-deck.txt; do
  [ -f "$root/$f" ] && present=$((present + 1))
done
if [ "$present" -eq 0 ]; then
  echo "  SKIP: the overview deck, its generator and its lock file are gone; nothing to test."
  exit 0
fi
if [ "$present" -ne 3 ]; then
  bad "the deck, its generator and its lock file exist together: restore what's missing, or delete all three (ADOPTING.md § 1)"
  finish
fi

tmp=$(mktemp -d 2>/dev/null || mktemp -d -t deck)
trap 'rm -rf "$tmp"' EXIT

py="${DECK_PYTHON:-python3}"

required=0
if [ "${DECK_BUILD_REQUIRED:-0}" = "1" ] || [ -n "${TF_BUILD:-}" ] || [ -n "${GITHUB_ACTIONS:-}" ]; then
  required=1
fi

# run_tier <label> <python>: runs the program on stdin, then drains its verdicts. Each
# tier must exit 0, print a final "done|" line and make at least one assertion, so a
# crash or an empty result can't pass. Read from a file: a pipe would lose the counters.
run_tier() {
  label=$1
  before=$((pass + fail))
  finished=0
  "$2" - "$root" "$tmp" >"$tmp/out" 2>"$tmp/err"
  st=$?
  while IFS='|' read -r verdict desc; do
    case "$verdict" in
      ok)   ok "$desc" ;;
      bad)  bad "$desc" ;;
      done) finished=1 ;;
      *)    [ -n "$verdict" ] && bad "$label tier printed a malformed line: $verdict" ;;
    esac
  done <"$tmp/out"
  [ "$st" -eq 0 ] || bad "$label tier exited $st: $(tail -n 1 "$tmp/err")"
  [ "$finished" -eq 1 ] || bad "$label tier did not finish"
  [ $((pass + fail)) -gt "$before" ] || bad "$label tier made no assertions"
}

echo "-- STATIC --"
run_tier static python3 <<'PY'
import ast, pathlib, re, sys

EXPECTED_TOTAL   = 27   # bump deliberately when a slide is added
EXPECTED_PALETTE = {    # restyling the deck is a separate decision
    "0E141B", "1B2632", "243342", "2DD4BF", "126E67", "F5B74D",
    "F4F7FA", "9AABB8", "334452", "4AD295", "E86A6A"}
PACKAGES = {"python-pptx": "1.0.2", "lxml": "6.1.3", "pillow": None,
            "typing-extensions": None, "xlsxwriter": None}

root = pathlib.Path(sys.argv[1])
def read(rel):
    p = root / rel
    return p.read_text(encoding="utf-8") if p.is_file() else ""
def chk(cond, desc):
    print(("ok" if cond else "bad") + "|" + " ".join(str(desc).split()))

src = read("scripts/build_deck.py")
tree = ast.parse(src)

total = next((n.value.value for n in ast.walk(tree)
              if isinstance(n, ast.Assign)
              and any(getattr(t, "id", None) == "TOTAL" for t in n.targets)
              and isinstance(n.value, ast.Constant)), None)
calls = sum(1 for n in ast.walk(tree)
            if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "slide")
chk(total == calls, f"TOTAL equals the number of slide() calls (TOTAL {total}, calls {calls})")
chk(total == EXPECTED_TOTAL, f"TOTAL is {EXPECTED_TOTAL} (found {total})")
palette = {"".join(f"{a.value:02X}" for a in n.args) for n in ast.walk(tree)
           if isinstance(n, ast.Call) and getattr(n.func, "id", None) == "RGBColor"}
chk(palette == EXPECTED_PALETTE, f"the palette is unchanged (differs by {sorted(palette ^ EXPECTED_PALETTE)})")
chk("constitution" not in src.lower(), "the generator never mentions a constitution (this repo has none)")

# --- the dependency lock ---------------------------------------------------------
raw = read("scripts/requirements-deck.txt")
logical = [l.strip() for l in raw.replace("\\\n", " ").splitlines()]
logical = [l for l in logical if l and not l.startswith("#")]
pins, unhashed, foreign = {}, [], []
for l in logical:
    m = re.fullmatch(r"([A-Za-z0-9._-]+)==([A-Za-z0-9.]+)((?:\s+--hash=sha256:[0-9a-f]{64})+)", l)
    if m:
        pins[m.group(1).lower().replace("_", "-")] = m.group(2)
    elif re.match(r"-|https?:|git\+|[^=]*@", l):
        foreign.append(l[:40])
    else:
        unhashed.append(l[:40])
chk(not unhashed, f"every requirement is pinned with == and hashed (offenders: {unhashed[:3]})")
chk(not foreign, f"the lock file adds no index, trusted host, editable or URL source (offenders: {foreign[:3]})")
chk(set(pins) == set(PACKAGES), f"the lock file holds exactly the five deck packages (found {sorted(pins)})")
for name, version in PACKAGES.items():
    if version:
        chk(pins.get(name) == version, f"{name} is pinned to {version} (found {pins.get(name)})")

# --- the CI job that runs third-party code ---------------------------------------
def uncommented(text):
    return "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
def block(text, start, stop):
    lines = uncommented(text).splitlines()
    idx = next((i for i, l in enumerate(lines) if re.fullmatch(start, l)), None)
    if idx is None:
        return "", "\n".join(lines)
    end = next((j for j in range(idx + 1, len(lines)) if re.match(stop, lines[j])), len(lines))
    return "\n".join(lines[idx:end]), "\n".join(lines[:idx] + lines[end:])

gh_path = next((p for p in (".github/workflows/checks.yml", ".github/workflows-disabled/checks.yml")
                if (root / p).is_file()), None)
ado, ado_rest = block(read("azure-pipelines.yml"), r"  - job: deck\s*", r"  - job:")
gh, gh_rest = block(read(gh_path) if gh_path else "", r"  deck:\s*", r"  \S")
INSTALL = ("--require-hashes", "--only-binary :all:", "-r scripts/requirements-deck.txt")
FORBIDDEN = ("--index-url", "--extra-index-url", "--trusted-host", "dependsOn", "needs:",
             "GITHUB_TOKEN", "System.AccessToken", "secrets.", "PublishPipelineArtifact",
             "PublishBuildArtifacts", "publish:", "upload-artifact", "Cache@", "actions/cache",
             "persistCredentials: true", "persist-credentials: true", ": write",
             "variables:", "group:", "pool:", "self-hosted", "DECK_SELFTEST",
             "continueOnError", "continue-on-error", "condition:", "if:", "enabled:")
ALLOWED_REFS = {"Agent.TempDirectory", "runner.temp"}
PIP = re.compile(r"\bpip3?\s+install\b")
for name, job, cred in (("azure-pipelines.yml", ado, "persistCredentials: false"),
                        ("the GitHub workflow", gh, "persist-credentials: false")):
    chk(job != "", f"{name} has a dedicated deck job")
    chk(all(f in job for f in INSTALL), f"{name}: the deck install requires hashes and wheels only")
    chk(cred in job, f"{name}: the deck job checks out without credentials")
    hits = [f for f in FORBIDDEN if f in job]
    chk(not hits, f"{name}: the deck job has no secrets, artifacts, caches or job links (found {hits})")
    chk("DECK_BUILD_REQUIRED: '1'" in job, f"{name}: the deck job requires the build tier")
    chk(("fetchDepth: 2" if name == "azure-pipelines.yml" else "fetch-depth: 2") in job,
        f"{name}: the deck job checks out two commits deep, for the deck-gate")
    chk("git show 'HEAD^1:scripts/ci-deck-needed.sh'" in job and "DECK_SKIP_FILE:" in job,
        f"{name}: the deck job asks the target's ci-deck-needed.sh before building")
    chk(("##[warning]Deck build skipped" if name == "azure-pipelines.yml" else "::notice::Deck build skipped") in job,
        f"{name}: a skipped deck build says so in the CI log")
    refs = set(re.findall(r"\$\(([^)]*)\)|\$\{\{\s*([^}]*?)\s*\}\}", job))
    other = sorted(r for pair in refs for r in pair if r and r not in ALLOWED_REFS)
    chk(not other, f"{name}: the deck job maps no other variables (found {other})")
    chk(len(PIP.findall(job)) == 1, f"{name}: the deck job has exactly one pip install")
chk("permissions:" in gh and "contents: read" in gh, "the GitHub workflow: the deck job reads contents only")
chk(not any(PIP.search(rest) for rest in (ado_rest, gh_rest)),
    "no job other than deck installs Python packages")
waits = re.compile(r"""(dependsOn|needs):.*\bdeck\b|^\s*-\s*['"]?deck['"]?\s*$""", re.M)
chk(not any(waits.search(rest) for rest in (ado_rest, gh_rest)), "no other job waits on the deck job")

# --- ownership and the adoption runbook -------------------------------------------
owners = next((l for l in read(".github/CODEOWNERS").splitlines()
               if l.startswith("/scripts/requirements-deck.")), "")
chk(owners.count("@") >= 2, "CODEOWNERS routes the lock file to both gate owners")
adopting = read("ADOPTING.md")
chk("git rm Agentic-Starter-Overview.pptx scripts/build_deck.py scripts/requirements-deck.txt" in adopting,
    "ADOPTING.md lists the three files that drop the deck")
chk("Keep `scripts/test/test-deck.sh`" in adopting, "ADOPTING.md says to keep the suite when dropping the deck")
deck_src = read("scripts/build_deck.py")
chk("GitHub Actions by default, or Azure DevOps" in deck_src and "GitHub Actions (default) or Azure DevOps" in deck_src,
    "the deck names GitHub Actions as the default CI, Azure DevOps as the alternative")

print("done|")
PY

echo "-- BUILD --"
have_pptx=1
"$py" -c "import pptx" >/dev/null 2>&1 || have_pptx=0
if [ "$have_pptx" -eq 0 ]; then
  if [ "$required" -eq 1 ]; then
    bad "python-pptx is importable via '$py' (the build tier is required here)"
  else
    printf "  SKIP: python-pptx not importable via '%s'; build tier skipped.\n" "$py"
  fi
  finish
fi

run_tier build "$py" <<'PY'
import contextlib, hashlib, io, math, os, pathlib, re, runpy, shlex, subprocess, sys, zipfile
from lxml import etree
from pptx.util import Emu

NEW = 8          # the workflow slide
FOOTER_TOP = 6.9 # footer() sits at y=7.02, 10pt by design
K = 0.52         # average glyph advance per em for Calibri
CHANGED = (3, 6, 7, 8, 9, 10, 11, 26, 27)

root = pathlib.Path(sys.argv[1])
work = pathlib.Path(sys.argv[2]) / "build"
work.mkdir()
def chk(cond, desc):
    print(("ok" if cond else "bad") + "|" + " ".join(str(desc).split()))
def inches(v): return Emu(v).inches

# The generator saves to the cwd, so a build in the repo would compare the deck with itself.
os.chdir(work)
try:
    with contextlib.redirect_stdout(io.StringIO()):
        g = runpy.run_path(str(root / "scripts" / "build_deck.py"))
except Exception as e:
    chk(False, f"the generator runs to completion ({type(e).__name__}: {e})")
    print("done|")
    sys.exit(0)
chk(True, "the generator runs to completion")

slides = list(g["prs"].slides)
chk(len(slides) == 27, f"the deck has 27 slides (found {len(slides)})")
if len(slides) != 27:
    print("done|")
    sys.exit(0)

def frames(n):
    return [sh for sh in slides[n - 1].shapes if sh.has_text_frame and sh.text_frame.text.strip()]
def blob(n):
    return "\n".join(sh.text_frame.text for sh in frames(n))

whole = "\n".join(blob(n) for n in range(1, 28))
chk("constitution" not in whole.lower(), "no slide mentions a constitution")

# --- what each changed slide must say, and must no longer say ----------------------
SAYS = [
    (3,  "major decisions also clear 7 strategic lenses"),
    (6,  "Three rounds, plus seven lenses for big calls"),
    (7,  "These checks come from the branch a PR merges into"),
    (7,  "loosening them takes a visible edit"),
    (7,  "--no-verify can't bypass this, once CI is required"),
    (7,  "The checks fail closed"),
    (7,  "once hooks are installed and CI is required"),
    (9,  "docs/repos/"),
    (9,  "generated copy of each repo's /docs"),
    (10, "Reviewers enforce this; no script checks it."),
    (11, "Keep the layout."),
    (11, "Delete .starter/ (template history) if you'll never pull updates."),
    (26, "Once wired, hooks + CI + branch protection make the core gates mechanical"),
    (27, "builds /docs"),
    (27, "the gates are advisory"),
]
GONE = [
    (3,  "redlines + 7 strategic lenses"),
    (6,  "then seven lenses"),
    (7,  "so it can't quietly loosen"),
    (10, "merges without updating contracts.md"),
    (11, "grep -rl '[insert'"),
    (26, "mechanical, not aspirational"),
    (27, "bootstraps the rest"),
]
for n, needle in SAYS:
    chk(needle in blob(n), f"slide {n} says: {needle}")
for n, needle in GONE:
    chk(needle not in blob(n), f"slide {n} no longer says: {needle}")

# --- slide 11's grep is the runbook's grep, and it works ----------------------------
adopting = (root / "ADOPTING.md").read_text(encoding="utf-8")
m = re.search(r"^(grep -rl [^\n#]*?)\s+#", adopting, re.M)
cmd = m.group(1) if m else ""
chk(cmd != "" and cmd in blob(11), f"slide 11 shows the grep from ADOPTING.md section 2 ({cmd})")
fixture = work / "grep-fixture"
for rel in ("real/a.md", ".starter/b.md"):
    (fixture / rel).parent.mkdir(parents=True, exist_ok=True)
    (fixture / rel).write_text("[" + "insert a value here]\n", encoding="utf-8")
found = subprocess.run(shlex.split(cmd), cwd=fixture, capture_output=True, text=True).stdout if cmd else ""
chk("real/a.md" in found and ".starter" not in found,
    f"that grep finds a placeholder and skips .starter/ (found {found.split()})")

# --- slide 11's caption clears the footer -------------------------------------------
cap = [sh for sh in frames(11) if sh.text_frame.text.startswith("One runbook")]
if len(cap) == 1:
    bottom = inches(cap[0].top) + inches(cap[0].height)
    after = cap[0].text_frame.paragraphs[0].space_after
    chk(bottom <= 7.02, f"slide 11: the caption ends above the footer (bottom {bottom:.2f})")
    chk(after is not None and after.pt == 0, "slide 11: the caption has no space after it")
else:
    chk(False, f"slide 11: the caption ends above the footer (found {len(cap)} captions)")

# --- slide 8's structure ------------------------------------------------------------
text8 = blob(NEW)
chk(any(sh.text_frame.text == f"{NEW:02d} / 27" for sh in frames(NEW)), f"slide {NEW} is numbered {NEW:02d} / 27")
for needle, count, why in [
    ("REVIEW GATE", 3, "plan review, build review and the human approver"),
    ("TEST GATE", 1, "the commit gate"),
    ("MACHINE GATE", 1, "hooks, CI and branch protection"),
    ("Rounds 1 → 2 → 3", 2, "the rounds run on the plan and on the build"),
]:
    chk(text8.count(needle) == count, f"slide {NEW} shows {needle} {count}x: {why} (found {text8.count(needle)})")
chk("Round 1" not in text8, f"slide {NEW} doesn't place one round under one stage")
for needle, why in [
    ("once wired", "the machine gate is qualified"),
    ("human approver", "the approver is named"),
    ("Plan review", "the rounds run on the plan"),
    ("Build review", "and again on the build"),
    ("FULL REVIEW PATH", "the kicker scopes the claim to the full path"),
    ("An empty /docs stops feature work", "the foundation band stays"),
    ("doesn't kill the change", "a gate holds a change, not ends it"),
]:
    chk(needle in text8, f"slide {NEW} says: {needle} ({why})")

stages = ["Plan", "Build", "Verify", "Ship"]
placed = sorted((inches(sh.left), sh.text_frame.text.strip()) for sh in frames(NEW)
                if sh.text_frame.text.strip() in stages)
chk([n for _, n in placed] == stages, f"slide {NEW}: the stages read left to right ({[n for _, n in placed]})")
gates = ["Plan review", "Build review", "TEST GATE", "MACHINE GATE"]
placed = sorted((inches(sh.left), g_) for sh in frames(NEW) for g_ in gates if g_ in sh.text_frame.text)
chk([n for _, n in placed] == gates, f"slide {NEW}: the gates read left to right ({[n for _, n in placed]})")
band = [sh for sh in slides[NEW - 1].shapes
        if 1.9 <= inches(sh.top) <= 3.6 and not (sh.has_text_frame and sh.text_frame.text.strip())]
chk(len([sh for sh in band if inches(sh.width) > 9.0]) == 1, f"slide {NEW}: one rail connects the stages")
chk(len([sh for sh in band if 1.5 <= inches(sh.width) <= 3.5]) == 4, f"slide {NEW}: four stage cards")

def fill(sh):
    try:
        return sh.fill.fore_color.rgb
    except Exception:
        return None
fills = [fill(sh) for sh in slides[NEW - 1].shapes]
chk(g["AMBER"] not in fills, f"slide {NEW}: amber is not a fill (teal is review, green is machine)")
chk(g["GREEN"] in fills, f"slide {NEW}: the machine gate is green")
chk(g["TEAL"] in fills, f"slide {NEW}: the review gates are teal")

EYEBROWS = {"REVIEW GATE", "TEST GATE", "MACHINE GATE"}
small = [(r.font.size.pt, r.text.strip()[:30]) for sh in frames(NEW) if inches(sh.top) < FOOTER_TOP
         for p in sh.text_frame.paragraphs for r in p.runs
         if r.font.size is not None and r.text.strip()
         and r.font.size.pt < (10.5 if r.text.strip() in EYEBROWS else 12.0)]
chk(not small, f"slide {NEW}: nothing is below 12pt except the eyebrows (found {small[:3]})")

def words(t): return re.findall(r"[A-Za-z0-9./-]+", t.lower())
def grams(t, n=7):
    w = words(t)
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}
for other in (6, 7, 14):
    dup = grams(text8) & grams(blob(other))
    chk(not dup, f"slide {NEW} shares no 7-word run with slide {other} (found {sorted(dup)[:2]})")

# --- text-fit budget ----------------------------------------------------------------
# python-pptx doesn't measure text and CI has no Calibri, so this estimates the height
# each paragraph needs from its own size and spacing, and compares it with the box.
def overflow(n):
    found = []
    for sh in frames(n):
        if inches(sh.top) >= FOOTER_TOP:
            continue
        width, avail = inches(sh.width) * 72 - 4, inches(sh.height) * 72 - 4
        paras = [p for p in sh.text_frame.paragraphs if "".join(r.text for r in p.runs).strip()]
        need = 0.0
        for i, p in enumerate(paras):
            t = "".join(r.text for r in p.runs)
            size = max((r.font.size.pt for r in p.runs if r.font.size), default=12.0)
            per_line = max(1, int(width / (size * K)))
            need += max(1, math.ceil(len(t) / per_line)) * size * 1.2 * (p.line_spacing or 1.0)
            if i < len(paras) - 1 and p.space_after is not None:
                need += p.space_after.pt
        if need > avail:
            found.append((round(need, 1), round(avail, 1), sh.text_frame.text.strip()[:36]))
    return found
for n in CHANGED:
    over = overflow(n)
    chk(not over, f"slide {n}: text fits its boxes (need, available, text: {over[:3]})")
    low = [sh.text_frame.text.strip()[:36] for sh in frames(n)
           if abs(inches(sh.top) - 7.02) > 0.01 and inches(sh.top) + inches(sh.height) > 7.02]
    chk(not low, f"slide {n}: text boxes end above the footer (found {low[:3]})")

# --- the committed .pptx is this generator's output ---------------------------------
# Whole-file hashes differ on every build (zip timestamps). The parts don't.
def parts(path):
    with zipfile.ZipFile(path) as z:
        return {n: hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()}
try:
    a, b = parts(root / "Agentic-Starter-Overview.pptx"), parts(work / "Agentic-Starter-Overview.pptx")
    diff = sorted(set(a) ^ set(b)) + sorted(k for k in set(a) & set(b) if a[k] != b[k])
except zipfile.BadZipFile:
    diff = ["(the committed file is not a zip)"]
chk(not diff, "the committed .pptx matches the generator part by part "
              f"(differing: {diff[:5]}; lxml {etree.__version__}; "
              "regenerate with: .venv-deck/bin/python scripts/build_deck.py)")

print("done|")
PY

# --- Each assertion must fail when its subject is broken ------------------------------
[ "${DECK_SELFTEST:-1}" = "1" ] || finish
echo "-- SELF-TEST --"

gh_ci=.github/workflows-disabled/checks.yml
[ -f "$root/.github/workflows/checks.yml" ] && gh_ci=.github/workflows/checks.yml

# fx: a copy of exactly the files scripts/ci-deck-needed.sh lists as the deck suite's
# inputs (starter T-022). The unmodified copy must pass, so a file this suite reads but
# that list omits fails here instead of letting a PR skip the deck job.
fx() {
  rm -rf "$tmp/fx"
  sh "$root/scripts/ci-deck-needed.sh" --list | while IFS= read -r f; do
    [ -f "$root/$f" ] || continue
    mkdir -p "$tmp/fx/$(dirname "$f")" && cp "$root/$f" "$tmp/fx/$f"
  done
}
child() { DECK_ROOT="$tmp/fx" DECK_SELFTEST=0 DECK_SKIP_FILE='' DECK_PYTHON="$py" sh "$test_dir/test-deck.sh" 2>&1; }

# expect_fail <description> <FAIL text> <child output>: the named assertion must be the
# one that fires, so a generator crash doesn't count as caught.
expect_fail() {
  case "$3" in
    *"FAIL: $2"*) ok "$1" ;;
    *) bad "$1 (wanted 'FAIL: $2'; got: $(printf '%s\n' "$3" | grep FAIL | head -n 3))" ;;
  esac
}

# mutate <description> <file> <old> <new> <FAIL text>
mutate() {
  fx
  if python3 - "$tmp/fx/$2" "$3" "$4" <<'PY'
import sys
path, old, new = sys.argv[1:4]
s = open(path, encoding="utf-8").read()
if old not in s:
    sys.exit(1)
open(path, "w", encoding="utf-8").write(s.replace(old, new))
PY
  then expect_fail "$1" "$5" "$(child)"
  else bad "$1 (the mutation didn't apply: '$3' not in $2)"
  fi
}

fx
out=$(child); st=$?
if [ "$st" -eq 0 ]; then ok "the unmodified copy passes"; else bad "the unmodified copy passes (got: $(printf '%s\n' "$out" | grep FAIL | head -n 3))"; fi
: > "$tmp/skip-marker"
out=$(DECK_ROOT="$tmp/fx" DECK_SELFTEST=0 DECK_SKIP_FILE="$tmp/skip-marker" DECK_PYTHON="$tmp/nonexistent" sh "$test_dir/test-deck.sh" 2>&1); st=$?
if [ "$st" -eq 0 ] && printf '%s' "$out" | grep -qF "SKIP: this PR changes no file the deck suite reads"
then ok "the deck-gate's marker skips the suite before any Python runs"
else bad "the deck-gate's marker skips the suite before any Python runs (rc=$st, out: $out)"; fi

g=scripts/build_deck.py
pad=$(printf 'padding %.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60)

mutate "a TOTAL that disagrees with the slides" $g "TOTAL = 27" "TOTAL = 28" "TOTAL equals the number of slide() calls"
mutate "a TOTAL that moved from 27" $g "TOTAL = 27" "TOTAL = 28" "TOTAL is 27"
mutate "a generator that crashes" $g 'footer(s, "Recap")' 'raise ValueError("boom")' "the generator runs to completion"
mutate "a build with a slide too many" $g "assert SLIDE_NO == TOTAL" "slide(); TOTAL = SLIDE_NO
assert SLIDE_NO == TOTAL" "the deck has 27 slides"
mutate "a new palette color" $g 'FONT  = "Calibri"' 'NEW = RGBColor(1, 2, 3)
FONT  = "Calibri"' "the palette is unchanged"
mutate "a changed palette color" $g "RGBColor(0xE8, 0x6A, 0x6A)" "RGBColor(0xE8, 0x6A, 0x6B)" "the palette is unchanged"
mutate "a constitution in the source" $g '"Enforcement")' '"Enforcement")  # constitution' "the generator never mentions a constitution"
mutate "a constitution on a slide" $g 'footer(s, "Recap")' 'footer(s, "Con" + "stitution")' "no slide mentions a constitution"

r=scripts/requirements-deck.txt
mutate "an unpinned requirement" $r "python-pptx==1.0.2" "python-pptx>=1.0.2" "every requirement is pinned"
mutate "a requirement without hashes" $r "python-pptx==1.0.2 \\" "python-pptx==1.0.2
# \\" "every requirement is pinned"
mutate "an extra index" $r "python-pptx==1.0.2" "--extra-index-url https://example.invalid/simple
python-pptx==1.0.2" "the lock file adds no index"
mutate "a trusted host" $r "python-pptx==1.0.2" "--trusted-host example.invalid
python-pptx==1.0.2" "the lock file adds no index"
mutate "an editable requirement" $r "python-pptx==1.0.2" "-e .
python-pptx==1.0.2" "the lock file adds no index"
mutate "a URL requirement" $r "python-pptx==1.0.2" "https://example.invalid/x.whl
python-pptx==1.0.2" "the lock file adds no index"
mutate "a sixth package" $r "python-pptx==1.0.2" "extra==1.0 --hash=sha256:$(printf '0%.0s' 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 43 44 45 46 47 48 49 50 51 52 53 54 55 56 57 58 59 60 61 62 63 64)
python-pptx==1.0.2" "the lock file holds exactly the five deck packages"
mutate "a moved python-pptx pin" $r "python-pptx==1.0.2" "python-pptx==1.0.1" "python-pptx is pinned to 1.0.2"
mutate "a moved lxml pin" $r "lxml==6.1.3" "lxml==6.1.2" "lxml is pinned to 6.1.3"

a=azure-pipelines.yml
mutate "no deck job (ADO)" $a "  - job: deck" "  - job: other" "azure-pipelines.yml has a dedicated deck job"
mutate "no hash check (ADO)" $a " --require-hashes" "" "azure-pipelines.yml: the deck install requires hashes"
mutate "source builds allowed (ADO)" $a " --only-binary :all:" "" "azure-pipelines.yml: the deck install requires hashes"
mutate "credentials kept (ADO)" $a "        persistCredentials: false" "        persistCredentials: true" "azure-pipelines.yml: the deck job checks out without credentials"
mutate "a secret in the job (ADO)" $a "          DECK_BUILD_REQUIRED: '1'" "          DECK_BUILD_REQUIRED: '1'
          GITHUB_TOKEN: \$(GITHUB_TOKEN)" "azure-pipelines.yml: the deck job has no secrets"
mutate "build tier not required (ADO)" $a "          DECK_BUILD_REQUIRED: '1'" "          DECK_BUILD_REQUIRED: '0'" "azure-pipelines.yml: the deck job requires the build tier"
mutate "pip in another job (ADO)" $a "      - script: sh scripts/test/test-ci-wiring.sh" "      - script: pip install foo
      - script: sh scripts/test/test-ci-wiring.sh" "no job other than deck installs Python packages"
mutate "a second, unhashed install (ADO)" $a "      - script: sh scripts/test/test-deck.sh" "      - script: python3 -m pip install foo
      - script: sh scripts/test/test-deck.sh" "azure-pipelines.yml: the deck job has exactly one pip install"
mutate "pip3 in another job (ADO)" $a "      - script: sh scripts/test/test-ci-wiring.sh" "      - script: pip3 install foo
      - script: sh scripts/test/test-ci-wiring.sh" "no job other than deck installs Python packages"
mutate "a mapped secret (ADO)" $a "          DECK_BUILD_REQUIRED: '1'" "          T: \$(MY_SECRET)
          DECK_BUILD_REQUIRED: '1'" "azure-pipelines.yml: the deck job maps no other variables"
mutate "another job waits on deck (ADO)" $a "  - job: procedure
" "  - job: procedure
    dependsOn: deck
" "no other job waits on the deck job"
job_ado="    displayName: overview deck (build + compare)"
for setting in "continueOnError: true" "condition: false" "variables:" "pool: mine"; do
  mutate "deck job weakened: $setting (ADO)" $a "$job_ado" "$job_ado
    $setting" "azure-pipelines.yml: the deck job has no secrets"
done
mutate "test step disabled (ADO)" $a "        displayName: overview deck fixture tests" "        displayName: overview deck fixture tests
        enabled: false" "azure-pipelines.yml: the deck job has no secrets"
mutate "a quoted wait on deck (ADO)" $a "  - job: procedure
" "  - job: procedure
    dependsOn:
      - 'deck'
" "no other job waits on the deck job"
mutate "a publish step (ADO)" $a "      - script: sh scripts/test/test-deck.sh" "      - publish: out
      - script: sh scripts/test/test-deck.sh" "azure-pipelines.yml: the deck job has no secrets"
mutate "self-test switched off (ADO)" $a "          DECK_BUILD_REQUIRED: '1'" "          DECK_SELFTEST: '0'
          DECK_BUILD_REQUIRED: '1'" "azure-pipelines.yml: the deck job has no secrets"
mutate "no deck job (GitHub)" "$gh_ci" "  deck:" "  other:" "the GitHub workflow has a dedicated deck job"
mutate "a second, unhashed install (GitHub)" "$gh_ci" "      - name: overview deck fixture tests" "      - run: pip install foo
      - name: overview deck fixture tests" "the GitHub workflow: the deck job has exactly one pip install"
# shellcheck disable=SC2016  # the ${{ }} is the YAML being inserted
mutate "a mapped secret (GitHub)" "$gh_ci" "          DECK_BUILD_REQUIRED: '1'" '          T: ${{ github.token }}
          DECK_BUILD_REQUIRED: '"'1'" "the GitHub workflow: the deck job maps no other variables"
mutate "another job waits on deck (GitHub)" "$gh_ci" "  procedure:
" "  procedure:
    needs: deck
" "no other job waits on the deck job"
job_gh="    name: overview deck (build + compare)"
for setting in "continue-on-error: true" "if: false"; do
  mutate "deck job weakened: $setting (GitHub)" "$gh_ci" "$job_gh" "$job_gh
    $setting" "the GitHub workflow: the deck job has no secrets"
done
mutate "self-hosted runner (GitHub)" "$gh_ci" "$job_gh
    runs-on: ubuntu-24.04" "$job_gh
    runs-on: self-hosted" "the GitHub workflow: the deck job has no secrets"
mutate "no hash check (GitHub)" "$gh_ci" " --require-hashes" "" "the GitHub workflow: the deck install requires hashes"
mutate "credentials kept (GitHub)" "$gh_ci" "persist-credentials: false" "persist-credentials: true" "the GitHub workflow: the deck job checks out without credentials"
mutate "write permission (GitHub)" "$gh_ci" "contents: read" "contents: write" "the GitHub workflow: the deck job reads contents only"
mutate "a secret in the job (GitHub)" "$gh_ci" "          DECK_BUILD_REQUIRED: '1'" "          DECK_BUILD_REQUIRED: '1'
          TOKEN: \${{ secrets.TOKEN }}" "the GitHub workflow: the deck job has no secrets"
mutate "build tier not required (GitHub)" "$gh_ci" "          DECK_BUILD_REQUIRED: '1'" "          DECK_BUILD_REQUIRED: '0'" "the GitHub workflow: the deck job requires the build tier"

mutate "lock file unowned" .github/CODEOWNERS "/scripts/requirements-deck.*" "/scripts/other.*" "CODEOWNERS routes the lock file"
mutate "runbook loses the delete list" ADOPTING.md "git rm Agentic-Starter-Overview.pptx" "git rm deck.pptx" "ADOPTING.md lists the three files"
mutate "runbook loses the keep note" ADOPTING.md "Keep \`scripts/test/test-deck.sh\`" "Delete the suite" "ADOPTING.md says to keep the suite"

mutate "slide 3 lens claim" $g "major decisions also clear 7 strategic lenses" "and 7 strategic lenses" "slide 3 says: major decisions"
mutate "slide 3 old claim" $g "non-negotiable redlines; major decisions" "non-negotiable redlines + 7 strategic lenses; major decisions" "slide 3 no longer says"
mutate "slide 6 title" $g "Three rounds, plus seven lenses for big calls" "Three rounds, then seven lenses for big calls" "slide 6 says: Three rounds"
mutate "slide 6 old claim" $g "Three rounds, plus seven lenses for big calls" "Three rounds, plus seven lenses for big calls, then seven lenses" "slide 6 no longer says"
mutate "slide 7 check source" $g "These checks come from the branch a PR merges into" "These checks come from the PR" "slide 7 says: These checks come from"
mutate "slide 7 visible edit" $g "loosening them takes a visible edit" "they can't be loosened" "slide 7 says: loosening them"
mutate "slide 7 tag" $g "once CI is required\"" "always\"" "slide 7 says: --no-verify"
mutate "slide 7 fail closed" $g "The checks fail closed" "The checks run" "slide 7 says: The checks fail closed"
mutate "slide 7 caption qualifier" $g "once hooks are installed and CI is required" "always" "slide 7 says: once hooks"
mutate "slide 7 old claim" $g "so loosening them takes a visible edit" "so it can't quietly loosen them, so loosening them takes a visible edit" "slide 7 no longer says"
mutate "slide 9 tree line" $g "docs/repos/" "docs/other/" "slide 9 says: docs/repos/"
mutate "slide 9 generated copy" $g "generated copy of each repo's /docs" "the docs" "slide 9 says: generated copy"
mutate "slide 10 enforcement" $g "Reviewers enforce this; no script checks it." "It is automatic." "slide 10 says: Reviewers"
mutate "slide 10 old claim" $g "a change to a documented contract must update" "merges without updating contracts.md: a change must update" "slide 10 no longer says"
mutate "slide 11 keep-or-delete" $g "Delete .starter/ (template history) if you'll never pull updates." "Keep .starter/ only if you'll sync." "slide 11 says: Delete"
mutate "slide 11 keep the layout" $g "Keep the layout. " "" "slide 11 says: Keep the layout."
mutate "slide 11 old grep" $g "\"Replace placeholders\"," "\"grep -rl '[insert' Replace placeholders\"," "slide 11 no longer says"
mutate "slide 26 qualifier" $g "Once wired, hooks" "Hooks" "slide 26 says: Once wired"
mutate "slide 26 old claim" $g "make the core gates mechanical; review enforces the rest." "make the core gates mechanical; review enforces the rest, mechanical, not aspirational." "slide 26 no longer says"
mutate "slide 27 docs" $g "builds /docs" "bootstraps the rest" "slide 27 says: builds"
mutate "slide 27 advisory" $g "the gates are advisory" "the gates are on" "slide 27 says: the gates are advisory"
mutate "slide 27 old claim" $g "builds /docs" "builds /docs and bootstraps the rest" "slide 27 no longer says"

mutate "slide 11 grep differs from the runbook" $g "--exclude-dir=.starter" "--exclude-dir=.other" "slide 11 shows the grep from ADOPTING.md"
mutate "a grep that no longer skips .starter" ADOPTING.md "grep -rl --exclude-dir=.starter" "grep -rl" "that grep finds a placeholder and skips .starter/"
mutate "slide 11 caption into the footer" $g "text(s, 0.9, 6.70, 11.5, 0.28," "text(s, 0.9, 6.95, 11.5, 0.28," "slide 11: the caption ends above the footer"
mutate "slide 11 caption spacing" $g "warnings.\", 12, MUTE, False, True)]], space_after=0)" "warnings.\", 12, MUTE, False, True)]], space_after=6)" "slide 11: the caption has no space after it"

mutate "slide 8 number" $g "footer(s, \"How a change earns its way to main\")" "SLIDE_NO -= 1; footer(s, \"How a change earns its way to main\"); SLIDE_NO += 1" "slide 8 is numbered"
mutate "a third rounds line" $g "Rounds 1 → 2 → 3 again, on the build" "Once more, on the build" "slide 8 shows Rounds 1 → 2 → 3 2x"
mutate "a missing review gate" $g "(\"REVIEW GATE\", 10.5, TEAL, True)]," "(\"HUMAN GATE\", 10.5, TEAL, True)]," "slide 8 shows REVIEW GATE 3x"
mutate "a missing test gate" $g "\"TEST GATE\",\"Before any commit\"" "\"REVIEW\",\"Before any commit\"" "slide 8 shows TEST GATE 1x"
mutate "a missing machine gate" $g "(\"MACHINE GATE\", 10.5, GREEN, True)]," "(\"GATE\", 10.5, GREEN, True)]," "slide 8 shows MACHINE GATE 1x"
mutate "one round per stage" $g "Task file · acceptance criteria · branch" "Round 1 review" "slide 8 doesn't place one round under one stage"
mutate "unqualified machine gate" $g "once wired (ADOPTING.md)" "always on" "slide 8 says: once wired"
mutate "no human approver" $g "a human approver (team)" "an approval" "slide 8 says: human approver"
mutate "no plan review" $g '"Plan review"' '"Review one"' "slide 8 says: Plan review"
mutate "no build review" $g '"Build review"' '"Review two"' "slide 8 says: Build review"
mutate "the gates out of order" $g "enumerate(gates)" "enumerate(reversed(gates))" "slide 8: the gates read left to right"
mutate "unscoped kicker" $g "The full review path" "The review path" "slide 8 says: FULL REVIEW PATH"
mutate "no foundation band" $g "An empty /docs stops feature work" "Docs matter" "slide 8 says: An empty /docs"
mutate "a gate that ends a change" $g "it doesn't kill the change" "the change is dropped" "slide 8 says: doesn't kill the change"
mutate "stages out of order" $g '("Plan","Task file' '("Build","Task file' "slide 8: the stages read left to right"
mutate "two rails" $g "fill=TEALDK, shape=MSO_SHAPE.RECTANGLE)
stages" "fill=TEALDK, shape=MSO_SHAPE.RECTANGLE); box(s, X0, 2.7, 11.55, 0.06, fill=TEALDK, shape=MSO_SHAPE.RECTANGLE)
stages" "slide 8: one rail connects the stages"
mutate "three stage cards" $g "box(s, ax[i], ROW_A_Y, CW, ROW_A_H, fill=PANEL2)" "(i != 3) and box(s, ax[i], ROW_A_Y, CW, ROW_A_H, fill=PANEL2)" "slide 8: four stage cards"
mutate "an amber fill" $g "box(s, ax[i], ROW_B_Y, CW, 0.10, fill=TEAL," "box(s, ax[i], ROW_B_Y, CW, 0.10, fill=AMBER," "slide 8: amber is not a fill"
mutate "no green" $g "box(s, ax[3], ROW_B_Y, CW, 0.10, fill=GREEN," "box(s, ax[3], ROW_B_Y, CW, 0.10, fill=TEAL," "slide 8: the machine gate is green"
mutate "no teal" $g "box(s, ax[i], ROW_B_Y, CW, 0.10, fill=TEAL," "box(s, ax[i], ROW_B_Y, CW, 0.10, fill=TEALDK," "slide 8: the review gates are teal"
mutate "small type" $g "[(body, 12, MUTE)]], line_spacing=1.05)" "[(body, 9, MUTE)]], line_spacing=1.05)" "slide 8: nothing is below 12pt"
caption8="A gate that says no holds the change there until the blocker is resolved; it doesn't kill the change."
mutate "words copied from slide 7" $g "$caption8" "No commits to main no placeholders secret scan Task-ID required; it doesn't kill the change." "slide 8 shares no 7-word run with slide 7"
mutate "words copied from slide 6" $g "$caption8" "Surface conflicts between lenses don't paper over them; it doesn't kill the change." "slide 8 shares no 7-word run with slide 6"
mutate "words copied from slide 14" $g "$caption8" "Diff vs contracts.md; update docs in the same PR; it doesn't kill the change." "slide 8 shares no 7-word run with slide 14"

mutate "slide 3 outgrows its box" $g "major decisions also clear 7 strategic lenses." "major decisions also clear 7 strategic lenses. $pad" "slide 3: text fits its boxes"
mutate "slide 6 outgrows its box" $g "Surface conflicts between lenses" "$pad Surface conflicts between lenses" "slide 6: text fits its boxes"
mutate "slide 7 outgrows its box" $g "The checks fail closed" "$pad The checks fail closed" "slide 7: text fits its boxes"
mutate "slide 8 outgrows its box" $g "An empty /docs stops feature work" "$pad An empty /docs stops feature work" "slide 8: text fits its boxes"
mutate "slide 9 outgrows its box" $g "docs/repos/" "$pad docs/repos/" "slide 9: text fits its boxes"
mutate "slide 10 outgrows its box" $g "Reviewers enforce this" "$pad Reviewers enforce this" "slide 10: text fits its boxes"
mutate "slide 11 outgrows its box" $g "Delete .starter/ (template history)" "$pad Delete .starter/ (template history)" "slide 11: text fits its boxes"
mutate "slide 26 outgrows its box" $g "Once wired, hooks" "$pad Once wired, hooks" "slide 26: text fits its boxes"
mutate "slide 27 outgrows its box" $g "builds /docs" "$pad builds /docs" "slide 27: text fits its boxes"

for n in 3 6 7 8 9 10 11 26 27; do
  mutate "slide $n has a box running into the footer" $g "# ---- save" "text(prs.slides[$n - 1], 0.9, 6.95, 5, 0.5, [[(\"x\", 12, MUTE)]])
# ---- save" "slide $n: text boxes end above the footer"
done

# --- the committed .pptx ---------------------------------------------------------------
same="the committed .pptx matches the generator part by part"
mutate "generator edited without regenerating" $g 'kicker(s, "Recap")' 'kicker(s, "Summary")' "$same"

# rezip <python body using zin, zout>: rewrite the fixture deck.
rezip() {
  python3 - "$tmp/fx/Agentic-Starter-Overview.pptx" "$1" <<'PY'
import sys, zipfile, os
path, mode = sys.argv[1:3]
with zipfile.ZipFile(path) as z:
    items = [(n, z.read(n)) for n in z.namelist()]
with zipfile.ZipFile(path + ".new", "w") as z:
    for n, data in items:
        if mode == "tamper" and n == "ppt/slides/slide1.xml":
            data += b" "
        z.writestr(n, data)
    if mode == "extra":
        z.writestr("ppt/extra.xml", b"<x/>")
os.replace(path + ".new", path)
PY
}
fx; rezip tamper; expect_fail "a tampered slide part" "$same" "$(child)"
fx; rezip extra;  expect_fail "an extra part" "$same" "$(child)"
fx; printf 'not a zip\n' > "$tmp/fx/Agentic-Starter-Overview.pptx"
expect_fail "a .pptx that isn't a zip" "$same" "$(child)"

# --- the deletion states -----------------------------------------------------------------
pair="the deck, its generator and its lock file exist together"
deck=Agentic-Starter-Overview.pptx; gen=scripts/build_deck.py; lock=scripts/requirements-deck.txt
fx; rm "$tmp/fx/$deck"
expect_fail "the deck alone is gone" "$pair" "$(child)"
fx; rm "$tmp/fx/$gen"
expect_fail "the generator alone is gone" "$pair" "$(child)"
fx; rm "$tmp/fx/$lock"
expect_fail "the lock file alone is gone" "$pair" "$(child)"
fx; rm "$tmp/fx/$deck" "$tmp/fx/$gen"
expect_fail "the lock file left behind" "$pair" "$(child)"
fx; rm "$tmp/fx/$deck" "$tmp/fx/$gen" "$tmp/fx/$lock"
out=$(DECK_ROOT="$tmp/fx" DECK_SELFTEST=0 DECK_BUILD_REQUIRED=1 DECK_PYTHON=/nonexistent sh "$test_dir/test-deck.sh" 2>&1); st=$?
if [ "$st" -eq 0 ] && printf '%s\n' "$out" | grep -q 'SKIP: the overview deck'; then
  ok "all three gone: a SKIP line and exit 0, even with the build tier required"
else bad "all three gone: a SKIP line and exit 0 (status $st: $out)"; fi

# --- the required tier and the runner itself -------------------------------------------
printf '#!/bin/sh\nexit 1\n' > "$tmp/nopptx"; chmod +x "$tmp/nopptx"
absent() { DECK_ROOT="$tmp/fx" DECK_SELFTEST=0 DECK_SKIP_FILE='' DECK_PYTHON="$tmp/nopptx" DECK_BUILD_REQUIRED=${1:-0} TF_BUILD=${2:-} GITHUB_ACTIONS=${3:-} sh "$test_dir/test-deck.sh" 2>&1; }
missing="python-pptx is importable via"
fx
expect_fail "required tier without python-pptx" "$missing" "$(absent 1)"
expect_fail "Azure DevOps requires it without the env var" "$missing" "$(absent 0 True)"
expect_fail "GitHub Actions requires it without the env var" "$missing" "$(absent 0 '' true)"
out=$(absent 0); st=$?
if [ "$st" -eq 0 ] && printf '%s\n' "$out" | grep -q 'SKIP: python-pptx not importable'; then
  ok "locally, without python-pptx, a visible SKIP line"
else bad "locally, without python-pptx, a visible SKIP line (status $st: $out)"; fi

# A stand-in interpreter that claims python-pptx and then misbehaves in the build tier.
# shellcheck disable=SC2016  # the $1 belongs to the generated stub
stub() { printf '#!/bin/sh\ncase "$1" in -c) exit 0 ;; esac\ncat >/dev/null\n%s\n' "$1" > "$tmp/stub"; chmod +x "$tmp/stub"; }
stubbed() { DECK_ROOT="$tmp/fx" DECK_SELFTEST=0 DECK_SKIP_FILE='' DECK_PYTHON="$tmp/stub" sh "$test_dir/test-deck.sh" 2>&1; }
stub 'echo boom >&2; exit 3'; expect_fail "a tier that crashes" "build tier exited 3: boom" "$(stubbed)"
stub 'exit 0';                expect_fail "a tier that prints nothing" "build tier did not finish" "$(stubbed)"
stub 'echo "done|"';          expect_fail "a tier that asserts nothing" "build tier made no assertions" "$(stubbed)"

# DECK_ROOT is made absolute, so a relative path survives the build tier's chdir.
fx
out=$(cd "$tmp" && DECK_ROOT=fx DECK_SELFTEST=0 DECK_PYTHON="$py" DECK_BUILD_REQUIRED=1 sh "$test_dir/test-deck.sh" 2>&1); st=$?
if [ "$st" -eq 0 ]; then ok "a relative DECK_ROOT works"; else bad "a relative DECK_ROOT works (status $st: $(printf '%s\n' "$out" | grep FAIL | head -n 3))"; fi

finish
