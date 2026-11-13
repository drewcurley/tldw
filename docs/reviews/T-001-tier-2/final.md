# Review: chore/docs-tier2 — final (T-001 Tier 2, T-016, T-019, ADR 0004)

**Status:** PASS WITH ITEMS. All 9 agents and the Review Coordinator confirmed the final content. Every blocker from the plan review, the three draft rounds and the final confirmation is fixed and re-checked. The open items below are recorded facts or deferred follow-ups; none is blocking work.

## The content signed off
- **Content hash:** `012d5e1b09a6b0e3e989b17bb4de16e192978519`.
- **What it is:** the `git write-tree` hash of this PR's tree with only this file (`final.md`) removed. A file can't contain the hash of a tree that includes it, and rebase-merging changes commit SHAs, so the sign-off is on content.
- **How to reproduce it** on the PR's head:

  ```sh
  GIT_INDEX_FILE=<scratch>/idx git read-tree <commit>
  GIT_INDEX_FILE=<scratch>/idx git rm --cached -q docs/reviews/T-001-tier-2/final.md
  GIT_INDEX_FILE=<scratch>/idx git write-tree   # → 012d5e1…
  ```

- **A later commit that only sets `pr:` in the task files** is outside this hash.

How the hash got here:
- `c3925e8…` was the first final tree. Backend, Frontend, UX, Analyst, Architect and Data Engineer all confirmed it.
- The Review Coordinator then blocked on a ledger mismatch: T-001's frontmatter named only Tier 1's branch and artifact, while INDEX listed both.
- `012d5e1…` added Tier 2 to T-001's `branch`, `pr` and `review_artifact`, and nothing else. All 9 and the Coordinator confirmed it, each reproducing the hash.

## The cycle

| Stage | Artifact | Result |
|---|---|---|
| Plan (rev 1 → 4), three rounds plus the rename delta | [`plan.md`](plan.md) | PASS WITH ITEMS, all 9 |
| Round 1: Analysis | [`round-1-analysis.md`](round-1-analysis.md) | Data blocked twice, then fixed; all yes |
| Round 2: Implementation | [`round-2-implementation.md`](round-2-implementation.md) | No blockers; 9 warnings applied; all yes |
| Round 3: Verification | [`round-3-verification.md`](round-3-verification.md) | Test blocked by mutation (quote floor), then fixed; all yes |
| Final confirmation | this file | All 9 and the Coordinator confirmed `012d5e1` |

**How it was reviewed:** each round used one agent per round, playing that round's roles (`AGENTS.md` § 12 Knob 1). Each role worked against its own brief and gave its own verdict. **No agent abstained**, and the cross-role conflicts were resolved under `AGENTS.md` § 3:
- the Test Engineer won on coverage (two guard tests);
- the Analyst won on scope (T-019 created for the rename);
- the Architect won on security (ADR 0004 scoped to piper-tts only).

## What landed
- **New docs:**
  - `docs/domain.md` (T-001.3): 13 entities, with logical cardinality and enforcement N/A.
  - `docs/ux.md` (T-001.7): 2 personas, 7 journeys, WCAG 2.2 AA as the stated target, contrast `UNKNOWN`, English-only UI.
  - `docs/runbooks/` (T-001.10): 9 files; severity is defined by what the owner loses; on-call is N/A, solo.
  - `docs/glossary.md` (T-016).
- **Decisions:** `docs/decisions/0004-accept-gpl-tts-dependency.md`, the owner accepting piper-tts 1.4.2's GPL-3.0. It is accepted and traces to T-015.
- **The rename (T-019):** `TL;DW — Hackathon` in both manifests, with `contracts.md`'s `name` row updated.
- **Tier 1 docs updated:**
  - the "pending" Tier 2 references in `README.md`, `onboarding.md` and `ownership.md`;
  - `stack.md`'s license line;
  - `decisions/README.md`;
  - `testing.md` and `deployment.md` (test counts, and the new guards);
  - two `contracts.md` rows that wrongly tied `segment_ratio` to the summary cache key (found by the Data Engineer).
- **Tests:**
  - `tests/test_docs_contracts.py` has a runbook-message guard (Python via `ast` plus a JS tokenizer, a floor of 16 quotes, and a negative test) and a manifest-name guard.
  - `MIN_TESTS` goes from 717 to 723, with its fixtures updated.
- **Ledger:**
  - T-001.3, .7, .10, .16, T-016 and T-019 are completed;
  - **T-001 is completed**, Tier 1 and Tier 2;
  - deferred items were appended to T-004, T-009, T-012, T-015 and T-017;
  - INDEX is regenerated.

## Regression safety
- **Contracts touched:**
  - the manifest `name` (`extension/manifest.json:3`, `extension/manifest.firefox.json:3`). It was changed at the owner's request, reflected in `contracts.md` in this PR, and guarded by `test_manifest_names_match_contracts_md`.
  - Two `contracts.md` rows were corrected to match the code, `segment_ratio` versus the summary key (`server.py:748-770`, `:1236-1242`, `:1350-1367`). That is a doc correction, not a code change.
- **Reflected in docs:** yes.
- **Cross-repo consumers:** none; this is a single repo.
- **Compatibility:**
  - The rename is cosmetic. Chrome installs the two extensions side by side, and in Firefox the hackathon build replaces the normal one, because the `gecko.id` is the same. Port, auth, defaults and version are unchanged.
  - The server checks Origin, not the extension's name (`server.py:915-922`).

## Checks
- The full suite passes, 723 of 723, with 0 skips and 0 failures.
- `tests/test_docs_contracts.py` passes 63 of 63.
- All 16 `scripts/test/*.sh` suites pass, and `test-task-ledgers.sh` passes 54 of 54.
- shellcheck is clean.
- No template placeholders appear outside allowlisted files.
- The deterministic leak check over `docs/` passes.
- **The local cache audit, counts only:** 48 cached video IDs and 48 titles were checked against `docs/` and the task files, with 0 matches.

## Open items (recorded; not blocking)
- **No automated test covers:**
  - docstrings counting as "string constants" for the quote guard;
  - the backticked messages in the runbooks README's "Less common" table;
  - the CLI's `config set`/`unset ytdlp-cookies` mapping;
  - port-in-use exiting 1 (DevOps measured it in isolation).
- **The JS tokenizer's two documented limits**, string `+=` and a regex after `)` or `}`, don't occur in today's extension.
- **`testing.md:228`** ("How this was verified") records the Tier 1 run.
- **All follow-up tasks T-004 to T-018 (except T-016) are deferred** by the owner (2026-11-12: one known user, one local machine).

## Agent Sign-offs (final content `012d5e1`)
- [x] Analyst
- [x] Architect
- [x] Data Engineer
- [x] Backend
- [x] Frontend
- [x] UX
- [x] Test Engineer
- [x] DevOps
- [x] Review Coordinator

## Lens Sign-offs
- n/a. ADR 0004 records the owner's decision, the rename is the owner's cosmetic request, and the docs describe what exists.
