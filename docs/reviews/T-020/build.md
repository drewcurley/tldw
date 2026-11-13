# Review: feat/profanity-mute — build (T-020)

**Task:** T-020 (`tasks/T-020.md`). The plan and its binding amendments are "Plan (rev 5)" and rev 5.1 to 5.4. Rev 5.4 records this build review.
**Review scope:** a new feature that touches documented contracts. That means the **full cycle**, all 9 agents, the regression-safety check (`AGENTS.md` § 7 step 8), and the `/docs/*` unanimous-consensus gate (§ 5). The 7 lenses ran on the plan (`docs/reviews/T-020/plan.md`).
**State reviewed:** the uncommitted working tree on `feat/profanity-mute` (30 modified files and 5 new ones; `git status --short`), on top of `35b5941`.
**Status:** PASS WITH ITEMS

The Review Coordinator checked each condition against the files, not against the main session's report. Two doc conditions weren't met at first (B-1, B-2 below); both were then fixed, and DevOps and the Test Engineer re-confirmed them, so the status moved from BLOCK to PASS WITH ITEMS. Both are one-line text fixes, but § 6.0 makes a false claim in a doc a BLOCK, and the Coordinator can't edit those files. Everything else checks out.

## How the build was reviewed
As rev 5.4 and the main session report it, checked below:
- **Round 1 (Analysis):** Analyst, Architect and Data Engineer each PASS WITH ITEMS. The Analyst raised 2 blockers. All were fixed (5.4, Round 1).
- **Round 2 (Implementation):** Backend PASS WITH ITEMS (1 blocker), Frontend PASS WITH ITEMS, UX BLOCK (2 text blockers). All were fixed (5.4, "Build review Round 2").
- **Round 3 (Verification):** Test Engineer BLOCK (5 test gaps), DevOps PASS WITH ITEMS. All were fixed (5.4, "Build review Round 3").
- **Re-checks and docs consensus (all 8 domain agents).** On the build: Architect PASS. Test Engineer PASS WITH ITEMS. UX BLOCK on one item (`.coverstatus` contrast), since fixed. The others PASS WITH ITEMS. On the docs, every agent said NO until its named fixes were made, and said it would sign off once they were. The Coordinator verified those conditions (see "Docs consensus: conditions checked").

The rounds ran in order, and each round's blockers were fixed before the next round began.

## Blockers (must fix before merge)

### Open (found by the Review Coordinator)
- [x] **B-1. The runbook index claims something the server doesn't log** (`docs/runbooks/README.md:39`, the `whisper_missing` row). It says: "If it is installed but can't load, the server log's text is "Whisper is installed but could not be loaded. …"". The server doesn't log that text.
  - `_run_align`'s `fail()` prints only `align: <reason> <video_id>` (`src/youtube_tldw/server.py:1949-1952`).
  - `WHISPER_BROKEN_TEXT` goes only into the in-stream `error` field (`server.py:1987-1989`, `:2054-2056`).
  - The panel shows its own `MUTE_TEXT.whisperMissing` in its place (`extension/content.js:1909-1910`; rev 5.4, Backend W3).
  - So no operator ever sees that sentence in the log or the panel. This breaks § 6.0(2), no invented facts.
  - **Fix:** drop the "server log's text" clause, or say the text is only in the stream's `error` field. Keep "Log: `align: whisper_missing`". **DevOps** owns the runbooks and re-confirms.
- [x] **B-2. `testing.md` still says the Firefox copy list is unguarded** (`docs/testing.md:231`, FU-7): "the copy list in `extension/build-firefox.sh` are unguarded". This contradicts the mapping-table row at `docs/testing.md:199`, which now names `test_firefox_build_copies_every_file_the_extension_loads` and its negative test, and it contradicts the guard itself (`docs/testing.md:74`). The Test Engineer's condition ("no longer 'Unguarded'") is therefore only half met.
  - **Fix:** drop the copy list from FU-7, leaving the version equality and the coupling of `host_permissions` to port 8765. **Test Engineer** re-confirms.

**Resolved (2026-11-13):** the `whisper_missing` row now says the panel shows the same install text either way and the log shows only `align: whisper_missing <video_id>` (DevOps re-confirmed: docs YES). FU-7 now says the copy list is guarded (Test Engineer re-confirmed: docs YES, build PASS). A docs edit made after an agent's PASS needs that agent to re-confirm it (`docs/reviews/README.md`, "Sign-off is on the final content"). Once DevOps confirms B-1 and the Test Engineer confirms B-2, this artifact can move to PASS WITH ITEMS with no other change.

### Resolved (as recorded in rev 5.4; each fix checked by the Coordinator where it's a text or test claim)
**Round 1**
- [x] **Analyst B2, the skipped-audio text.** `NO_DURATION_TEXT` became `AUDIO_SKIPPED_TEXT`, which names both causes and gives the fix for the ceiling. The wording was finalised in Round 2 (UX decision 1). Checked: `src/youtube_tldw/align.py:65`.
- [x] **Analyst B1, the rest of the 6′ test register.** Tests were added for a 413 with no `reason`, a pinned `off`, a cut timeout that fails one window while the next still runs, the "budget under one window" rule, prune once per request, client-gone at the Nth write, and a no-words log test for each of the nine server reasons (5.4, Round 1). The Data W4 and Architect W2 items were added to the same register.

**Round 2**
- [x] **Backend B1, the budget clock.** `started` is taken right after `_align_lock`, so the download, the permit wait and the model load all spend `ALIGN_BUDGET_S` (600 s). The server then ends within 630 s, inside the extension's 660 s timer. It is tested on a fake clock. Checked: `server.py:76-77`.
- [x] **UX B1.** `DOWNLOAD_TIMEOUT_TEXT` and `DOWNLOAD_FAILED_TEXT` now end "Turn this off and on again to retry." Checked: `align.py:83-86`.
- [x] **UX B2 (decision 3), the mid-session ad.** A mid-session ad announces `adPaused`. Any status that arrives during the ad is held and shown, unannounced, when the ad ends. Checked: `content.js:1874`, `:2075-2081`; the test is `tests/js/mute_wiring_checks.js:450-466`.

**Round 3**
- [x] **Test Engineer B1.** Importing the server computes no fingerprint. Tested in a fresh interpreter.
- [x] **Test Engineer B2.** `_load_model` without `local_only` passes no `local_files_only`. The old assertion proved nothing; this one does.
- [x] **Test Engineer B3.** A stored `start_ms` or `end_ms` of `False`, `5000.0` or `"0"` counts as a cache miss.
- [x] **Test Engineer B4.** An infinite or NaN timestamp is dropped, not raised.
- [x] **Test Engineer B5.** The panel adapter (`coverPanelUi`, `setupCoverToggle`) moved inside the wiring fence and got its own `panel` group. Checked: `tests/test_extension_render.py:114-117`, 10 groups with floors.

**Re-check**
- [x] **UX re-check, the `.coverstatus` contrast.** It is `#767676` in light mode and `#888` in dark mode (`content.js:249-250`). The Coordinator measured 4.54:1 on `#fff` and 4.64:1 on `#1e1f24` (`content.js:147`), so both pass AA (see W-2 on the stated figure).

## Docs consensus: conditions checked
Each condition was checked in the file. A tick means it was met; B-1 and B-2 above are the two that weren't.
- **Frontend:** [x] "10 groups" for the wiring harness (`docs/testing.md:71`, `docs/contracts.md:1267`, matching `MUTE_WIRING_FLOORS`). [x] The `muteWords`, `muteSound` and `muteCover` rows (`contracts.md:988`, `:989`, `:1006`): `options.js:180-181`, `content.js:2508`, `:2518-2519` and `:2379` are exact. The `createMuteCover` range is in W-1. [x] The port and page-selector refs: `content.js:2096-2097`, `:2251`, `:2285-2304`, `:2330-2331` and `:2443-2444`, and `background.js:61`, `:81` and `:130-136`, are exact.
- **Data:** [x] `data-model.md` `cli.py:390-392` (the six areas) and `server.py:1959-1963` (`send_window`), both exact. [x] `domain.md` § 2.15 sits after § 2.14 (`:154`, `:162`) and states the 2 s rule (`MUTE_WORD_MAX_MS`; `content.js:1488`, `:1654`).
- **Backend:** [x] The `alignError` refs `background.js:94-96`, `:98`, `:112`, `:140`, `:144` and `:147-148`, all exact. [x] The `do_POST` chain is at `server.py:995-1036`, with `/align/stream` at `:1030-1034` (exact). [x] "Routes that take no slot" (`contracts.md:377`) has the post-T-020 refs; `_run_preview` is in W-1. [x] `api.md` § 7.1 (`:216`) and the `_run_align` docstring (`server.py:1879-1882`) both say "every refusal after the body is validated", and name the `400`s, the `401`, the `411`/`415` and the `413` as carrying none.
- **UX:** [x] `ux.md:225` lists `.coverstatus`. [x] The glossary has "at least 0.3 s" (`glossary.md:189`). [x] The read time is grounded (`ux.md:125`, "up to about 13 s"). [x] W1 is fixed in `say()`, with a test (above).
- **Architect:** [x] `architecture.md` no longer says "not which word" (grep). [x] Store 13 holds the words around each hit in I-19 (`:195`), the STRIDE block (`:165`) and the store-13 class (`data-model.md:372`). [x] § 3.3 lists `~/.cache/youtube-tldw/align` (`:102`). [x] The STRIDE block covers B3: untrusted windows, the open shadow root and `video.muted` (`:168`). [x] "each refusal that has a `reason` logs…" (`:164`). [x] I-8 cites `tests/test_align.py:32-49`, which are the two `parse_windows` tests. [x] The 30 s per-window deadline is in the DoS bullet (`:166`; `ALIGN_WINDOW_DEADLINE_S = 30.0`, `server.py:77`).
- **DevOps:** [x] `RUNBOOK_QUOTE_FLOOR = 19` (`tests/test_docs_contracts.py:984`), and `testing.md:92` says 19. [ ] The runbook README's log-hint cells: four are accurate, but the `whisper_missing` cell isn't (**B-1**).
- **Analyst:** [x] the 10-groups fix and the 2 s rule (as above). [x] The sound invariant has a `ratechange` clause (`contracts.md:959`; `content.js:2223`).
- **Test Engineer:**
  - [x] The counts are exact against the JUnit report: `test_extension_render.py` 33, `test_hygiene.py` 21, `test_align.py` 102.
  - [x] The skipif paragraph is correct (`testing.md:138`): 10 `node` markers and 2 `bash` markers, and there are no other skips in `tests/*.py`.
  - [x] The mapping table gains `/align/stream` (`:185`, `:190`), the align port flow (`:191`), store 13 (`:196`) and `options_mute_checks.js` (`:197`).
  - [x] The Firefox copy list is guarded in the table (`:199`). [ ] It is still listed as unguarded at `:231` (**B-2**).
  - [x] `contracts.md` has a row for the `wiring (BEGIN)` marker (`:1268`).
  - [x] On the build: the all-cached test holds `_align_lock` and the permit (`tests/test_align.py:461-473`), and the `windows` floor is 13 (`tests/test_extension_render.py:73`).

## Verification run by the Coordinator
- `.venv/bin/python -m pytest -q -p no:cacheprovider`: **863 tests, 0 failures, 0 errors, 0 skips** (from the JUnit report). `MIN_TESTS=863` (`scripts/ci-tldw-tests.sh:27`).
- `sh scripts/test/test-ci-tldw-tests.sh`: **32 passed, 0 failed**, including "the CI floor is at least 863".
- **The default word list (hygiene).** A script parsed the entries of `TLDW_MUTE_DEFAULTS` and searched every tracked and untracked, non-ignored file outside `extension/mute_words.js` for whole-word, case-insensitive matches. Result: **0 hits in 217 files**. No word was printed.
- **The history scan** (counts only) over the 10 commits in `hackathon/demo..HEAD`: a single default-list entry was added to `tasks/T-020.md` in `e5d3980` and kept in `d9cc46b`. It is present in that file in **every committed tree from `e5d3980` through HEAD `35b5941`**, `cd478c3` included. It is reworded only in the working tree, and none of these commits is on `hackathon/demo` yet. See W-3.
- No unreplaced starter placeholder (the bracketed "insert" form, AGENTS.md § 10) in `docs/`, `src/`, `extension/`, `tests/` or `tasks/T-020.md`.

## Regression-safety (AGENTS.md § 7 step 8)
Regression-safety: contracts touched =
- **additive:**
  - the route `POST /align/stream` and its NDJSON `window`, `result` and `error` events, with `reason` on every refusal after the body is validated;
  - the five port messages `align`, `alignProgress`, `alignWindow`, `alignDone` and `alignError`;
  - the storage keys `muteWords` and `muteSound`;
  - cache store 13 and `~/.cache/youtube-tldw/align`;
  - the `tldw cache` area "word timings";
  - the align log lines;
  - the `ALIGN_*` and `MAX_ALIGN_*` limits, plus `_align_lock` and `_align_backoff_until`;
  - the page selectors `#movie_player`, `.ad-showing`, `.ytp-mute-button` and `#tldw-mute-pill`;
  - the injected `mute_words.js`, and the content-script markers;
  - every verbatim status and `align.py` text.
- **changed, backward-compatible:**
  - `download_audio(max_duration_s=)` (its argv is pinned byte for byte to the pre-T-020 list when the argument is absent);
  - `_load_model(local_only=)`, now the single construction site;
  - `txcache._prune(cap=)` (the default is unchanged);
  - `tldw cache clean`, which now chooses areas by name;
  - the `executeScript` files list and the Firefox copy list;
  - Options Save.

Reflected in docs = **yes**: `contracts.md`, `api.md`, `data-model.md`, `architecture.md`, `domain.md`, `ux.md`, `glossary.md`, `testing.md`, `deployment.md` and three runbook files, all in this PR. Each new constant and route was checked against `contracts.md`, and the verbatim-string and inventory extractors pass. Two text inaccuracies remain (B-1, B-2).

Cross-repo consumers = none (single repo; no `workspace.manifest.md`). Compatibility = backward-compatible. An old extension never calls the route. A new extension against an old server gets 404 and the "Older server" status. A rollback leaves `~/.cache/youtube-tldw/align/` behind, which `deployment.md` says to delete by hand.

## Warnings (fix before next review)
- [ ] **W-1. Line ranges that run past their target** (cosmetic; each still contains the right code):
  - `contracts.md:1006` gives `createMuteCover` as `content.js:2069-2466`, but it ends at `:2464`.
  - `contracts.md:377` gives `_run_preview` at `server.py:2098`; it is at `:2099`.
  - `buildMutePill` is cited `content.js:2020-2067`, but it ends at `:2062`.
  - `handleAlign` is cited `background.js:93-153`, but it ends at `:152`.
  - The marker row at `contracts.md:1267` cites `mute_wiring_checks.js:12-26`; the markers are at `:13-15`.
- [x] **W-2 (fixed: now "about 4.6:1").** `ux.md:225` gave `#888` on `#1e1f24` as "about 4.7:1". It measures 4.64:1, which is still AA. Say "about 4.6:1".
- [ ] **W-3. The default-list entry in history.** It is in `tasks/T-020.md` from `e5d3980` to `35b5941`, not only in `cd478c3`. These commits are on this branch only today, but **a rebase-merge into `hackathon/demo` copies them into that branch's history.** The owner should choose knowingly: squash, rewrite the branch before merging, or accept it on the throwaway `hackathon/demo` (it must never reach `main`). The PR body must say which.
- [ ] **W-4. The task file**, to update in the work PR (§ 7 step 9):
  - `review_artifact` still names `plan.md` (`tasks/T-020.md:10`);
  - `state` moves to `completed` only after B-1 and B-2 are cleared and the owner's gates below are met;
  - fill in `pr`;
  - append the build-review entry to the activity log;
  - regenerate `tasks/INDEX.md`.
- [ ] **W-5. Spot-checks per agent.** This artifact records the Coordinator's own `path:line` checks. Each domain agent's spot-checks appear only as the code references in its 5.4 amendments; the agents' own reports weren't saved. This was raised on the plan too.
- [ ] **W-6. Commits and PR:**
  - every commit carries `T-020`;
  - every commit that touches `docs/` carries `unanimous-consensus: T-020`;
  - no `--no-verify`;
  - the PR targets `hackathon/demo`, never `main`;
  - at least one human approver per `.github/CODEOWNERS`.

### Still open before the task is complete (owner and real browser)
- [ ] **The owner's AC6 read at the build stage** of `extension/mute_words.js` (mild, no slurs). The plan-stage read is logged (`tasks/T-020.md:812`, commit `35b5941`). The build-stage read is still open.
- [ ] **The real-browser check** (from 5.4 and `ux.md`), on three test videos, with the tab muted. The results go in `ux.md` and in this folder:
  - word-timing precision: a word mid-sentence, a fast talker, music behind speech (`ux.md:139`);
  - whether YouTube's own mute state follows `video.muted` when its mute button or `m` is used inside a range, and whether YouTube saves a mute set mid-range into its stored volume (`ux.md:142`);
  - whether Stop clicks in Firefox (`AudioParam.value` under automation), and whether autoplay works with the toggle as the gesture in Chrome and Firefox;
  - how loud the moo is next to the bleep, and the bleep no louder than the speech around it;
  - whether the mute pill (`bottom: 136px`) overlaps `#tldw-pill` (84 px);
  - hidden-tab timers (the `ux.md` limit at 1.25× and above).
- [ ] **Demo readiness** (DevOps, carried from the plan):
  - ffmpeg on the demo server's PATH;
  - a faster-whisper model already in the demo HOME;
  - reload the server, then the extension card, then the tab;
  - keep the Options textarea off a shared screen.

## Suggestions (consider for polish)
- B-1 shows that nothing in the server log tells "not installed" from "installed but can't load". A DevOps follow-up could log the second case as its own `align: model load failed …` style line. That would be a contract change, so it is not part of this PR.

## Agent Sign-offs
**Build** is each agent's final verdict on the build. **Docs** is its consensus vote, which was conditional; the Coordinator checked each condition.
- [x] **Analyst.** Build: PASS WITH ITEMS. Docs: conditional, **verified** (the 10 groups, the 2 s rule, `ratechange`).
- [x] **Architect.** Build: PASS. Docs: conditional, **verified** (all eight conditions, `architecture.md:102`, `:163-168`, `:183`, `:195`).
- [x] **Data Engineer.** Build: PASS WITH ITEMS. Docs: conditional, **verified** (`data-model.md:279`, `:333`, `:372`; `domain.md` § 2.15).
- [x] **Backend.** Build: PASS WITH ITEMS. Docs: conditional, **verified** (the `alignError` refs exact; `_run_preview` off by one line, W-1).
- [x] **Frontend.** Build: PASS WITH ITEMS. Docs: conditional, **verified** (the `createMuteCover` range end, W-1).
- [x] **UX.** Build: the re-check BLOCK is fixed and was measured by the Coordinator (4.54:1 and 4.64:1). UX must re-confirm the final `.coverstatus` values. Docs: conditional, **verified** (W-2 on the stated ratio).
- [ ] **Test Engineer.** Build: PASS WITH ITEMS. Docs: conditional, **not met**: B-2 (`testing.md:231`). Re-confirm after the fix.
- [ ] **DevOps.** Build: PASS WITH ITEMS. Docs: conditional, **not met**: B-1 (`runbooks/README.md:39`). Re-confirm after the fix.
- [ ] **Review Coordinator.** **BLOCK**, on the process rule that no doc is signed off while it breaks § 6.0 (B-1, B-2). Otherwise the rounds ran in order, every round's blockers are matched to a fix in 5.4, none is deferred, the tests and the floor are verified, regression-safety is recorded, and the hygiene scan is clean. Spot-checks:
  - `server.py:76-77`, `:995`, `:1030-1034`, `:1879-1882`, `:1949-1952`, `:1959-1963`, `:1987-1989`, `:2054-2056`, `:2099`;
  - `align.py:35`, `:65`, `:81-86`;
  - `cli.py:390-399`;
  - `background.js:61`, `:81`, `:93-148`;
  - `content.js:147`, `:249-250`, `:1488`, `:1654`, `:1874`, `:1909-1910`, `:2069`, `:2075-2081`, `:2096-2097`, `:2223`, `:2285-2304`, `:2330-2331`, `:2464`, `:2508`, `:2518-2519`;
  - `options.js:175-181`;
  - `tests/test_align.py:32-49`, `:461-473`;
  - `tests/test_extension_render.py:73`, `:114-117`;
  - `tests/test_docs_contracts.py:984`;
  - `docs/testing.md:70-74`, `:92`, `:138`, `:185-199`, `:231`;
  - `docs/runbooks/README.md:37-43`.

## Lens Sign-offs (major decisions only)
The 7 lenses ran on the plan (rev 5 to 5.3, `docs/reviews/T-020/plan.md`; `tasks/T-020.md:690-715`). **The build changed nothing a lens should revisit:**
- no new dependency, asset, cost or Claude usage (Purchasing);
- the PM fallback, caption-line covering only, was not invoked, so AC2, AC3 and AC9 stand (PM);
- the build-stage changes are status wording, `aria-disabled` on the toggle, the `ratechange` re-plan and the ad hold, which all reduce friction (Adopter);
- the store-13 privacy statement became more precise ("the words around each hit") without any change in what is stored (CEO, Builder).

The one lens-relevant item is W-3. The reputational risk the CEO lens named, the default list, now extends to the git history, because a default-list entry is still in branch history. The owner decides at merge time.
- [x] CEO  - [x] Purchasing  - [x] PM  - [x] Adopter  - [x] Builder  - [x] Investor  - [x] Marketing (carried from the plan; not re-run)

## Content hash
n/a: not the final sign-off (BLOCK).

## Re-confirmations after the Coordinator's check (2026-11-13)
- **DevOps:** docs sign-off **YES**: the quote floor is 19 and documented, and every runbook log-hint cell, `whisper_missing` included, matches `server.py`.
- **Test Engineer:** docs sign-off **YES**; build verdict **PASS**. The suite was re-run: 863 tests, 0 failures, 0 errors, 0 skips.
- With these, every domain agent's docs sign-off is **YES**, so the `/docs` unanimous-consensus gate is met for this PR. The commit carries `unanimous-consensus: T-020`.
