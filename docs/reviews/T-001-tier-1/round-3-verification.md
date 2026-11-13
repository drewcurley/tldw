# Review: chore/docs-tier1 — Round 3, Verification (T-001 Tier 1 drafts)

**Status:** PASS WITH ITEMS. The Test Engineer blocked on four items and DevOps on one. Both passed after one fix pass, re-checked against the code. Each reviewer was a fresh agent working from its own brief, and ran no state-changing git commands. The Test Engineer ran its mutation experiments on scratch copies only.

## Blockers (all fixed)
- [x] **Test Engineer B1.** The env-var extractor in `tests/test_docs_contracts.py` missed `os.getenv(...)` and `"K" in os.environ`, though the plan's category names `getenv`. This was proven by mutation m7: two new reads went unseen. Fixed: the extractor matches both forms, and `not in` too. The re-run of m7 fails and names all three reads. The real tree still has 10 env vars.
- [x] **Test Engineer B2.** testing.md's regression-safety table didn't cover every `contracts.md` section, and one "—" cell overstated a guard (chunked → `400` and `411` are untested). Fixed:
  - rows added for request and response headers with the server-side invariants, extension → server HTTP calls, the keyboard and accessibility baseline, public module entry points, and cross-cutting data invariants;
  - the unguarded parts are named, as FU-10 → T-010.
- [x] **Test Engineer B3.** testing.md said a missing source marker always breaks the harness. Only `grab()` does (`tests/js/render_checks.js:7-15`). Fixed: the bare `indexOf` slices and the `PANEL_*_MS` replacements are named as the exceptions (FU-11 → T-010).
- [x] **Test Engineer B4.** testing.md said flakes are removed "by construction", but a known wall-clock flake exists. Fixed with a "Known flake" note (FU-12 → T-010). **The Test Engineer's ruling: the flake does not block this PR.** It is a pre-existing product test that this PR does not touch, and it is tracked.
- [x] **DevOps B1.** deployment.md gave the CI floor as `MIN_TESTS=660`. This PR raises it to 717 (`scripts/ci-tldw-tests.sh:27`). Fixed.

## Warnings
- [x] **Test Engineer:**
  - T-010's security item wrongly called 413 and the token-file mode untested (`tests/test_server.py:151`, `:771-790`, POSIX only). Corrected.
  - FU-1's `501` citation pointed at the 404 sites. It now says the stdlib sends it. The same wording is mirrored in T-010 (the Test Engineer's optional suggestion).
- [x] **DevOps:**
  - ADR 0002 stated `MIN_TESTS=660` as a current fact. It now gives 660 under T-003 and 717 after T-001.
  - deployment.md's log row said the request log carries no query string. The stdlib logs the raw request line, which includes one. Reworded: never the token, and the extension sends no query strings.
  - The Firefox Permissions step from `extension/README.md:29-32` was missing. Added.

## Suggestions
- **Applied (DevOps):**
  - the `pyproject.toml:13-15` extras cites (deployment.md twice, stack.md once) and the `pytest` cite at stack.md:90;
  - onboarding.md now lists `main` and `master` among the CI push triggers;
  - deployment.md notes that a local `ci-pr-checks.sh` run uses your own copy of the runner.
- **Applied after a wrong first reading:** DevOps's re-check proposed changing stack.md:93's `pyproject.toml:16-17` to `:17-18`. The coordinator first declined it, misreading the file. In the final confirmation, DevOps and the Architect showed that line 17 is `[project.scripts]` and line 18 is the script. Fixed, together with eight more off-by-one `pyproject.toml` cites the Architect found (stack.md:34, :45, :72, :81, :87, :90; architecture.md:59; ADR 0001:37).
- **Open (DevOps, accepted as open):** record the real restart and seed commands in deployment.md § 10's rollback record at the next release. The footer already says § 10 is re-run then.
- **Open (Test Engineer, optional):** the wording of the `FLOORS` failure message.

## Regression safety
- Contracts touched = none. No product code changed.
- Test infrastructure changed: `tests/test_docs_contracts.py` (new), and `MIN_TESTS` raised 660 → 717 with its fixtures. `scripts/ci-tldw-tests.sh` is a CODEOWNERS gate file.
- Reflected in docs = yes (`testing.md`, `deployment.md`, ADR 0002).
- Cross-repo consumers = none (single repo).
- Compatibility = backward-compatible.

## Spot-checks (by reviewer, against the code)
- **Test Engineer (first pass; 4 ✘, since fixed):**
  - the per-file counts in `testing.md:26-74`, against the JUnit report;
  - `pyproject.toml:13`, `:23-32`; `server.py:66`, `:980`, `:1017-1019`, `:1924`; `claude_client.py:294-296`;
  - `conftest.py:1-135` (11 sites); `test_isolation.py:18`, `:30`, `:36-46`, `:54`; 27 lines of `test_server.py`, from `:34` to `:2921`; `test_extension_render.py` (8 lines);
  - `render_checks.js:7-15`, `:325-327`, `:422-432`; `probe_checks.js:6-8`, `:61`, `:176`, `:191-197`; `page_transcript_checks.js:105-196`; `test_claude_client.py:284`, `:376-421`;
  - `ci-tldw-tests.sh:27`, `:58-62`, `:76-85`; `checks.yml:30`, `:46`, `:108`, `:115-129`; `ci-checks.sh:129`; `pre-commit:89`; `content.js:930`; `options.js:124`;
  - FU-2…FU-7, each confirmed unguarded by grep; only four `skipif`s exist.
- **Test Engineer (re-check, all ✔):** `tests/test_docs_contracts.py:428-450` (with m7 re-run); `testing.md:69`, `:141`, `:175-177`, `:192-195`, `:210`, `:223-225`; `test_server.py:111`, `:151`, `:186-192`, `:771-797`; `render_checks.js:84-98`, `:137`, `:155`, `:165`, `:402`; `page_transcript_checks.js:96-98`; `test_claude_client.py:443`; `T-010.md:28`.
- **Mutations (Test Engineer, on scratch copies):**

  | Mutation | Result |
  |---|---|
  | m1: a route unbackticked in `contracts.md` | ✔ fails and names it |
  | m2: a new `os.environ.get` | ✔ fails |
  | m3: a gone env var left in the doc | ✔ reverse check fails |
  | m4: a new `DEFAULTS` key | ✔ fails |
  | m5: `do_GET` renamed | ✔ fails |
  | m6: a home path in a doc | ✔ leak check fails |
  | m7: `getenv` / `in os.environ` | ✘ at first; ✔ after B1 |
- **DevOps (first pass; 1 ✘ plus 1 warning ✘, since fixed):**
  - `gh api` on ruleset 27155529: target, enforcement, bypass, 0 approvals, rebase-only, strict, the four contexts, non-fast-forward and deletion all match the doc;
  - `main`: no ruleset, and branch protection answers 404;
  - `checks.yml:21-31`, `:116`, `:132`, `:187`, `:236-251`; `ci-pr-checks.sh:1-13`; `ci-checks.sh:128-138`; `azure-pipelines.yml:20-29`;
  - `.githooks/pre-commit:19-23`, `pre-push:10-27`; `.claude/settings.json:21-29`;
  - the version in four places (`pyproject.toml:7`, `__init__.py:3`, both manifests at `:4`), plus `uv.lock`, the release commits and the annotated tags;
  - `server.py:654-656`, `:689-708`, `:917-940`, `:974-976`, `:1924-1971`; `cli.py:299-311`, `:596-609`; the facts at `v1.3.0`; both manifests' `host_permissions`; `README.md:32-62`, `:437-446`; `build-firefox.sh:7-12`;
  - no owner-specific setup in the docs, and a clean secret and real-data scan.
- **DevOps (re-check, all ✔):** `deployment.md:12`, `:31`, `:37`, `:51`, `:109`, `:252`; ADR 0002 `:46`, `:99`; `stack.md:72`; `onboarding.md:140`.

## Checks
- **The full suite:** 717 passed with 0 skips (the Test Engineer's run, and the coordinator's after the B1 fix). `sh scripts/test/test-ci-tldw-tests.sh`: 32 passed.
- **All 16 `scripts/test/test-*.sh` suites:** passed.

## Round 3 sign-offs
- [x] Test Engineer — yes (PASS, after one fix pass)
- [x] DevOps — yes (PASS, after one fix pass)
