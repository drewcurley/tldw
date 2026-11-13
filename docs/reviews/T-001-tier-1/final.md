# Review: chore/docs-tier1 — final (T-001 Tier 1 docs bootstrap)

**Status:** PASS WITH ITEMS. All 9 agents confirmed the final content. Every blocker from the plan review, the three draft rounds and the final confirmation is fixed and re-checked. The open items below are optional suggestions or tracked follow-ups. None of them is deferred blocking work.

## The content signed off

- **Content hash:** `617f527681f70d80982f1bb5239294e3be70f4df`.
- **What it is:** the `git write-tree` hash of this PR's tree with only this file (`final.md`) removed. A file can't contain the hash of a tree that includes it, and rebase-merging changes commit SHAs. So the sign-off is on content, not on a commit.
- **How to reproduce it** on the merged commit or on the PR's head:

  ```sh
  git read-tree <commit>   # into a scratch index: GIT_INDEX_FILE=/tmp/idx
  git rm --cached -q docs/reviews/T-001-tier-1/final.md
  git write-tree           # → 617f527…
  ```

- **A later commit that only sets `pr:` in the task files** is outside this hash, by the plan (`tasks/T-001.md`, "Tasks and commit").

How the hash got here:
- `ed21ac9…` was the first final tree. The Frontend, UX, DevOps, Architect and Review Coordinator reviews found citation errors and UNKNOWNs with no follow-up task.
- `a3e514b…` fixed those: nine `pyproject.toml` cites, the `render_checks.js:84-114` range, a Round 1 cite, and three T-015 items.
- `617f527…` then added the Review Coordinator's B2: a fourth T-015 item, and ADR 0001's option 3.

All 9 confirmed `617f527…` after seeing the delta from the hash before it.

## The cycle

| Stage | Artifact | Result |
|---|---|---|
| Plan (rev 1 → 4), all three rounds | [`plan.md`](plan.md) | PASS WITH ITEMS, all 9 |
| Round 1, Analysis: Analyst, Architect, Data Engineer | [`round-1-analysis.md`](round-1-analysis.md) | 7 blockers fixed; both open items closed before Round 2 |
| Round 2, Implementation: Backend, Frontend, UX | [`round-2-implementation.md`](round-2-implementation.md) | 4 blockers fixed |
| Round 3, Verification: Test Engineer, DevOps | [`round-3-verification.md`](round-3-verification.md) | 5 blockers fixed |
| Final confirmation: all 9 | this file | Architect B1 (citations) and Review Coordinator B1/B2 (§ 6.0(2)) fixed; all 9 PASS on `617f527…` |

Every reviewer was a separate agent, given its own brief from `.claude/agents/`. Each re-checked the drafts against the source, not the prose, and listed its `path:line` spot-checks. None ran a state-changing git command in the worktree. The Test Engineer's mutation tests ran on scratch copies only.

## Blockers (must fix before merge)
- [x] None open. The blockers from every round are listed, fixed and re-checked in the round artifacts. The final-confirmation blockers were:
  - [x] **Architect B1.** Nine `pyproject.toml` cites were off by one: `stack.md:34`, `:45`, `:72`, `:81`, `:87`, `:90`, `:93`; `architecture.md:59`; ADR 0001 `:37`. Fixed, and the Round 3 artifact's wrong "not applied" note is corrected.
  - [x] **Review Coordinator B1 and B2** (§ 6.0(2)). Fifteen `UNKNOWN`s had no follow-up task. Added to [T-015](../../../tasks/T-015.md) as four items. The Coordinator then mapped every `UNKNOWN` line in `docs/` to a task.

## Warnings (fix before next review)
- [x] Frontend and UX: `testing.md:193` cited `render_checks.js:84-98` for the hotkey shield. It is now `:84-114`.
- [x] Review Coordinator W1: a stale Round 1 cite, now `tests/test_docs_contracts.py:857-858`.
- [ ] Carried as tracked follow-ups, not fixed here, because this PR changes no product code:
  - the stale `server.py:962-963` "no query" comment → [T-012](../../../tasks/T-012.md);
  - the wall-clock flake in `tests/test_claude_client.py` → [T-010](../../../tasks/T-010.md).

## Suggestions (consider for polish)
- Open and optional. Each is listed in its round artifact:
  - Backend: port-in-use in `api.md` § 11.
  - Frontend: the pill ids' Kind column; an `audio.DEFAULT_VOICE` row; the `settledRows` clause.
  - UX: S1 (the `.regen` wording); S3 (a troubleshooting pointer).
  - Test Engineer: the `FLOORS` failure message.
  - DevOps: the § 10 restart and seed commands, recorded at the next release.

## Regression safety
- **Contracts touched:** none. No product code changed: nothing under `src/` or `extension/`.
- **Test infrastructure:**
  - `tests/test_docs_contracts.py` is new: 57 tests, checking every inventory category in both directions, with count floors, one negative test and the leak check;
  - `MIN_TESTS` is raised from 660 to 717 in `scripts/ci-tldw-tests.sh`, with its fixtures in `scripts/test/test-ci-tldw-tests.sh`. **`scripts/ci-tldw-tests.sh` is a CODEOWNERS gate file.**
- **Reflected in docs:** yes (`testing.md`, `deployment.md`, ADR 0002).
- **Cross-repo consumers:** none, since this is a single repo.
- **Compatibility:** backward-compatible.

## Checks on the final tree
- **The full suite:** `.venv/bin/python -m pytest -p no:cacheprovider` passed 717 with 0 skips, run by the coordinator, the Test Engineer (JUnit: `tests=717 failures=0 errors=0 skipped=0`) and the Review Coordinator. One earlier run hit the known flake (T-010).
- **The scripts:** all 16 `scripts/test/test-*.sh` suites pass. `test-task-ledgers.sh` passes 54/54.
- **shellcheck,** as CI runs it over `.githooks/pre-commit`, `.githooks/commit-msg`, `.githooks/pre-push`, `scripts/*.sh` and `scripts/test/*.sh`: clean (local 0.11.0).
- **The deterministic leak and placeholder check** (committed in `tests/test_docs_contracts.py`) runs over `docs/` and `tasks/T-004`…`T-018`. It covers:
  - the home paths `/[U]sers/`, `/[h]ome/`, `C:\[U]sers`, `-[U]sers-`;
  - the secret patterns read from `scripts/ci-checks.sh`;
  - the placeholder marker, `\[[i]nsert [^]]+ here\]`.

  It found 0 hits.
- **The local cache-ID audit (counts only):** run against the hackathon demo home's caches. Per the owner's standing rule, the owner's own caches were not read.
  - 26 cache files gave 1 video ID and 1 title, checked against 49 doc and task files.
  - IDs: 0 hits.
  - Titles: 2 hits, both in pre-playbook legacy review files that this PR does not change. They name the same well-known public test video already tracked as FU-8 → [T-011](../../../tasks/T-011.md).
  - 0 hits in any file this PR adds or edits.

## Agent Sign-offs
Each line gives the agent's final verdict on `617f527…` and the spot-checks of its final confirmation. The spot-checks from each round are in that round's artifact.
- [x] **Analyst — PASS.**
  - All 13 Tier 1 subtasks' ticked boxes check out against the cited doc sections.
  - Scope: 54 files, `docs/`, `tasks/`, the new test file and the two `MIN_TESTS` scripts only. No product code, Tier 2 docs or `AGENTS.md` changed.
  - INDEX matches the frontmatter, and every cited `T-0NN` resolves.
  - Its condition, that this file lands in the same commit as the ledger, is met.
  - Spot-checks: `architecture.md:122`; `README.md:23`, `:56`; `data-model.md:33`, `:280`, `:317`, `:342`, `:403`; `api.md:63`, `:86`, `:185`, `:208`; `stack.md:47-68`, `:95`; `testing.md:87`, `:90`, `:118`, `:133`, `:145`, `:164`; `deployment.md:7`, `:69`, `:170`, `:256`; ADR 0003 `:3`, `:70`; `ownership.md:12`, `:64`, `:77`; `onboarding.md:66`, `:85`, `:104`, `:167`; `contracts.md:18`, `:1177`, `:1272`; `tasks/T-015.md:33-35`, `:44`.
- [x] **Architect — PASS.**
  - No security invariant is weakened, and no redline is crossed beyond ADR 0003, which the owner accepted on 2026-11-12.
  - Spot-checks: `contracts.md:50`, `:59`, `:936`, `:1284` against `claude_client.py:333`, `:599`, `:635`, `proc.py:53-54`, `server.py:1020-1035`, `:1153`, `:1306-1309`, `:1762`, `background.js:71`; `deployment.md:37`, `:248-252`; ADR 0002 `:46`, `:99`; `testing.md:175-176`; `architecture.md:152-154`, `:166`, `:171`, `:182`; ADR 0003 `:23`, `:63-65`; and all nine `pyproject.toml` cites.
- [x] **Data Engineer — PASS.**
  - The request-log and `usage.jsonl` facts agree across four docs.
  - Spot-checks: `data-model.md:5`, `:323`, `:336`, `:378`; `server.py:707`, `:962-963`, `:973`, `:983`, `:1959`; `usage.py:26`, `:184-189`, `:218-219`, `:227`; `cli.py:481-483`, `:488`; `config.py:44-56`; `claude_client.py:149`; `txcache.py:21-33`, `:84-86`, `:124`, `:158-161`; `hygiene.py:107`; `metadata.py:48-55`; `contracts.md:1190`, `:1196`, `:1362-1369`; `tasks/T-014.md:22-35`.
- [x] **Backend — PASS.**
  - Spot-checks: `testing.md:176-177` against `tests/test_server.py:111`, `:151`, `:159`, `:186-192`, `:771-797` and `probe_checks.js:153-162`; `deployment.md:252` against `server.py:962-963`, `:1942-1963`; `T-010.md:28`; its Round 2 fixes still in place at `api.md:99`, `:150`, `:183`, `:221-222`, `:229`, `:281` and `contracts.md:50`, `:55`, `:59`, `:223`, `:495`, `:1284`; and the `pyproject.toml` cites.
- [x] **Frontend — PASS.**
  - Spot-checks: `deployment.md:37` against `extension/README.md:29-32` and `build-firefox.sh:7-12`; `testing.md:69`, `:177`, `:192`, `:193` against `render_checks.js:7-15`, `:84-114`, `:137`, `:155`, `:165`, `:402`, `page_transcript_checks.js:96-98` and `probe_checks.js:152-187`; `contracts.md:595`, `:709`, `:714`, `:936`, `:1092-1093`, `:1475`, `:1477`.
- [x] **UX Designer — PASS.**
  - Spot-checks: `onboarding.md:13`, `:140` against `checks.yml:21-25`; `deployment.md:37`; `testing.md:193` against `render_checks.js:84-114`, `:401-402`; `contracts.md:818`, `:832`, `:861`; `README.md:7`.
- [x] **Test Engineer — PASS.**
  - Suite: 717/0/0/0. `test-ci-tldw-tests.sh`: 32/32.
  - Mutations m1–m7 on scratch copies all fail as they should, m7 after the extractor fix.
  - Spot-checks: `scripts/ci-tldw-tests.sh:27`; `tests/test_docs_contracts.py:429`, `:446`; `testing.md:141`, `:175-177`, `:192-195`, `:210`, `:225`; `render_checks.js:99-114`; `pyproject.toml:13`, `:30-32`; `T-010.md:21`.
- [x] **DevOps — PASS.**
  - The live ruleset 27155529 and `main`'s lack of protection match the docs.
  - Spot-checks: `deployment.md:12`, `:31`, `:37`, `:51`, `:109`, `:252`; ADR 0002 `:46`, `:99`; `stack.md:34`, `:45`, `:72`, `:81`, `:87`, `:90`, `:93`; ADR 0001 `:37`; `architecture.md:59`; `tasks/T-001.9.md:7`, `:28`.
- [x] **Review Coordinator — PASS.**
  - Rounds ran 1 → 2 → 3 in order, every blocker was re-checked by the agent that raised it, and no agent was skipped.
  - Every Tier 1 doc has a verification section and an update-when footer.
  - Every `UNKNOWN` maps to T-014, T-015 or T-017.
  - The ledger is consistent.
  - Spot-checks: `scripts/ci-tldw-tests.sh:27`; `scripts/test/test-ci-tldw-tests.sh:51-58`; `server.py:1935-1938`; `summarize.py:144`; `claude_client.py:333-343`; `content.js:50-55`, `:670`; `background.js:144-145`; `cli.py:319-320`; `decisions/README.md:39-43`; ADR 0001 `:29-31`; ADR 0003 `:23`, `:65`; `architecture.md:61`, `:124`, `:195`, `:209`, `:219`; `data-model.md:28-29`, `:216-237`, `:277-278`, `:330`, `:336`, `:380`, `:401`; `deployment.md:67`, `:125`, `:242`, `:250-251`; `contracts.md:52`, `:69`, `:72`, `:83`, `:913`, `:947`, `:1195-1196`, `:1216-1217`, `:1468`, `:1491`; `stack.md:72`, `:109-121`.

## Lens Sign-offs (major decisions only)
- n/a. Nothing new is decided. ADR 0001 records an existing choice, ADR 0002 records departures the owner already made, and ADR 0003 records an existing design, which the owner accepted against the redline on 2026-11-12 (`plan.md`, "Lens Sign-offs").

## Human review
Solo repo: ruleset 27155529 requires 0 approvals (`AGENTS.md` § 8, solo-repo exception). The PR and the four required checks still gate the merge.
