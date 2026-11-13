# Review: feat/profanity-mute — build, rev 5.6 (T-020): settings apply live

**Task:** T-020 (`tasks/T-020.md`: "Plan rev 5.6", its Round 1–3 amendments, the Coordinator and Analyst ruling, and "Rev 5.6 build review"). The plan review is in `docs/reviews/T-020/plan-5.6.md`. The owner approved the plan on 2026-11-13, AC18 included.
**Change:** "Cover listed words" applies `muteSound` and `muteWords` while it is on (AC13–AC18). Options' Save text says so. `handleAlign` registers its disconnect listener before its first await. The `/docs` line refs are re-synced to the code.
**Review scope:** a behaviour change to a documented contract. So the full cycle runs (3 rounds, then the Coordinator) and the `/docs` unanimous-consensus gate applies. The 7 lenses are skipped, since this isn't a major decision (Analyst, confirmed).
**State reviewed:** the uncommitted working tree on `feat/profanity-mute`, on top of `fb02315`.
**Status:** PASS WITH ITEMS

## Blockers (must fix before merge)
- [x] **SDET: the rebuild-state check couldn't fail.** Mutation showed that `rebuild`'s four explicit resets had no effect: its `tick` → `arm` resets the timer, re-arms and edge, and `startAlign` resets `alignDone`. The resets were deleted. The test now sets non-default values first, and it fails if the rebuild's `tick` is removed.
- [x] **SDET (re-check): a test block made the "late message on the old port is dropped" check vacuous.** The block was moved. That check now fails if `onAlign`'s `sess.port !== port` guard is removed.
- [x] **Coordinator B-1: line refs drifted after the re-sync.** The deletion above moved every `content.js` line from `:2613` up by 2. All 113 refs past it were shifted, including the `muteWords`/`muteSound` reads, now `:2746`/`:2747`. Refs that were already stale at HEAD, `options.js:137(-138)`, `:137-141` and `:130-131`, now point at the Save write (`:176-177`), its status (`:176-186`) and the model fallback (`:168-169`).

## Warnings (fix before next review)
- [x] **UX W1:** with the panel closed, a live end now shows its text in the pill's live region, and the pill goes 1 s later, with no focus move. Tested.
- [x] **Backend W1:** heard-window reuse shares each window's words over every hit inside it, as `onAlign` does, and refines at the real playhead. Tested with two hits in one window. **Backend W2:** a live end clears a pending retry timer. Tested for both ends.
- [x] **Frontend W1/W2:** when a rebuild leaves the range under the playhead unchanged, the range carries on as it was. The user's own unmute still wins, and a playing bleep isn't restarted. Tested.
- [x] **Docs conditions (Analyst, Data, UX, Frontend, Backend, SDET):** each named fix is in the diff:
  - `plan-5.6.md` records the approval;
  - `:2475` is fixed, and the `voice` range is `:1035-1037`;
  - the `server-busy.md` wording;
  - `testing.md`: MIN_TESTS 866, 10/11 groups, and the listener file.
- [x] **DevOps:** the new `tests/js/settings_listener_checks.js`, `docs/reviews/T-020/plan-5.6.md` and this file are in the commit.

## Suggestions (consider for polish)
- The runbooks' "Read on `a7f104c`" provenance lines are left exactly as written. They record where each runbook was checked, and their `server.py` refs weren't re-verified here. Re-verify them in a later runbook pass.
- SDET: the `ev.node` check runs on moo only, and the `sound` groups' floors sit under their real counts. Neither is new.

## How the docs were checked
- **The four new status rows, the pill note, the `onChanged` paragraph and the storage rows:** checked against `MUTE_TEXT`, `buildMutePill.setMode`, the "settings listener" block (`content.js:50-70`) and `settingsChanged` (Data, Frontend, UX, Coordinator). `test_docs_contracts.py` checks every status string verbatim (50 found).
- **`ux.md:134`:** UX's W5 text, word for word. **Runbooks:** checked against `MUTE_RETRY_MS`, `sawProgress` and `server.py`'s busy points (Backend, DevOps).
- **The line-ref re-sync:**
  - Each ref was re-mapped from the commit that last wrote its doc line (`git blame`) to the working tree, by an exact line diff (841 refs), plus six by hand. Then the 113 refs past `:2613` were shifted.
  - Samples were taken by the main session (10), the Test Engineer (16) and the Coordinator.
  - `docs/reviews/T-001-*`, `docs/reviews/T-020/build.md`, `plan.md` and `docs/decisions/` are unchanged.

## Regression safety
The change is additive. There are no new storage keys, routes, messages or files in the extension's copy list. Only the timing of two existing keys changed, and an older stored value means the same thing. Rollback is a revert plus an extension reload; the server is unchanged.

## Agent Sign-offs
| Agent | Build | Docs |
|---|---|---|
| Analyst | PASS WITH ITEMS | YES (conditions met) |
| Architect | PASS | YES (refs match the markers; risk 13 stays open under T-004; re-confirmed after the token-read refs `options.js:65`, `:120` were fixed) |
| Data Engineer | PASS WITH ITEMS | YES (condition met) |
| Backend | PASS WITH ITEMS | YES (conditions met) |
| Frontend | PASS WITH ITEMS | YES (line refs fixed; re-confirmed after B-1) |
| UX | PASS WITH ITEMS | YES (condition met) |
| Test Engineer | BLOCK → PASS | YES |
| DevOps | PASS WITH ITEMS | YES |
| Review Coordinator | PASS WITH ITEMS (after B-1) | YES |

## Lens Sign-offs (major decisions only)
Not run: this isn't a major decision (AGENTS.md; Analyst, confirmed).

## Suite
866 passed, with 0 skips (`MIN_TESTS=866`). The CI script's self-tests pass 32/32, and the procedure checks pass.
