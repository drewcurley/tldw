# Review: feat/profanity-mute — plan (T-020, rev 5 to 5.3)

**Task:** T-020 (`tasks/T-020.md`)
**Review scope:** New feature, UI screens, architecture decision: **full cycle, all 9 agents and the 7 lenses** (`AGENTS.md` § 5). It also touches documented contracts (a route, an NDJSON type, port messages, storage keys, a cache store), so the build review adds the regression-safety check (§ 7 step 8) and the `/docs/*` unanimous-consensus gate.
**Status:** PASS WITH ITEMS

The plan is in `tasks/T-020.md`: "Plan (rev 5)", plus the binding amendments "Plan rev 5.1" (Round 1), "Plan rev 5.2" (Round 2) and "Plan rev 5.3" (Round 3). Rev 1 to 4 and their addenda are history only. Where the amendments differ, the later one wins.

## How the plan was reviewed
As the main session reports it, and as the T-020 activity log records it (`tasks/T-020.md:742-747`):
- **Round 1 (Analysis), on rev 5:** Analyst, Architect and Data Engineer each BLOCKed, on text only. Rev 5.1 was written. Re-check: Analyst PASS, Architect PASS WITH ITEMS, Data Engineer PASS.
- **Round 2 (Implementation), on rev 5.1:** Backend BLOCK, Frontend PASS WITH ITEMS (with two blockers), UX BLOCK. Rev 5.2 was written. Re-check: Backend PASS, Frontend PASS. UX raised one more blocker (focus on Stop from the pill), which was fixed as UX worded it; then UX PASS.
- **Round 3 (Verification), on rev 5.2:** Test Engineer BLOCK, DevOps BLOCK. Rev 5.3 was written. Re-check: Test Engineer PASS WITH ITEMS (one re-arm test, since adopted), DevOps PASS.
- **The 7 lenses** were run on rev 5 to 5.3 by the main session (`tasks/T-020.md:690-715`).
- **Review Coordinator:** the procedural check below. It also sent one narrow re-check to UX on the rate-limit text (see Blockers, Coordinator item).

Each round's blockers were fixed before the next round started.

## Blockers (must fix before merge)
All are resolved in the plan text. Each line gives who raised it, what it was, and where the fix is.

**Round 1**
- [x] Analyst B1: AC9 put "the words" in the cache key, but the list never shapes the cached result. Fixed: AC9 amended (`tasks/T-020.md:45`); 5.1 "Analyst B1"; key composition in § 3.5, with `lang=auto` added by Q3.
- [x] Analyst B2: several listed words in one caption line. Fixed: 5.1 "Analyst B2" (refine only when Whisper's matches reach the line's count); tests in 6′, AC2 row.
- [x] Analyst B3: no live-stream signal. Fixed: 5.1 "Analyst B3" (`duration === Infinity` is live; `NaN` is not); tests in 6′, AC1 row.
- [x] Architect B1: the ffmpeg cut could hang. Fixed: 5.1 "Architect B1" (`proc.run` with a 15 s timeout, `-i` asserted inside `workdir`). The ffmpeg presence check was later moved in-stream by 5.2 Backend B1.
- [x] Architect B2: no threat-model entry. Fixed: 5.1 "Architect B2" (STRIDE entry for `/align/stream`, an I-8 row for `windows`, the "server words never reach the DOM" invariant with a wiring test).
- [x] Data Engineer B1: "store 9" is already taken (`docs/data-model.md:22`, `:220`). Fixed: 5.1 "Data B1", now **store 13, "Alignment cache"**. Checked: the stores table ends at 12 (`docs/data-model.md:12-25`).
- [x] Data Engineer B2: window integers. Fixed: 5.1 "Data B2" (`cueEnd` with no +1000, clamping, join before ordering, a safe-integer test).
- [x] Data Engineer B3: the fingerprint was computed at import. Fixed: 5.1 "Data B3" (lazy and memoised; an unreadable version takes the 503 `whisper_missing` path).
- [x] Data Engineer B4: Options Save pinned the default list. Fixed: 5.1 "Data B4" (one shared `normaliseMuteList`; Save removes the key when the list equals the default); tests in 6′, AC6 row.

**Round 2**
- [x] Backend B1: the order of checks in `_run_align`. Fixed: 5.2 "B1, the order of checks" (plain-JSON checks before the 200; model and ffmpeg checked before any download).
- [x] Backend B2: no machine-readable reason on errors. Fixed: 5.2 "B2, every align error carries `reason`" (ten server values, three added by `background.js`).
- [x] Frontend B1: `emptied`/`loadstart` tore down the session at mid-roll ads. Fixed: 5.2 Extension "B1" (tear down only on a new video id or `!video.isConnected`).
- [x] Frontend B2: document listeners in the bubble phase. Fixed: 5.2 Extension "B2" (`{capture: true}`; `m` with Ctrl, Meta or Alt ignored).
- [x] UX B1: failures without a next step. Fixed: 5.2 UX "B1" (server details passed through, three recorded verbatim).
- [x] UX B2: the Whisper-off wording named a label that doesn't exist. Fixed: 5.2 UX "B2".
- [x] UX B3: Restore deleted the key with no way back. Fixed: 5.2 UX "B3" (Restore writes nothing; Save decides).
- [x] UX B4: the two 429 texts. Fixed: 5.2 UX "B4" (both pass the server's text through; one constant, `ALIGN_RATE_LIMITED_TEXT`).
- [x] UX B5 (and Q5): a whole-range tone of up to 8 s. Fixed: 5.2 UX "B5 / Q5" (sound only in the first 1500 ms of a range, for bleep and moo); AC4 amended (`tasks/T-020.md:40`).
- [x] UX re-check blocker: focus and announcement when Stop is pressed on the pill. Fixed: 5.2 "Round 2 re-check amendments" (focus to `#movie_player`, Off text in the pill's live region, the pill removed after about 1000 ms; one live region at a time), with tests.

**Round 3**
- [x] Test Engineer B1: about 25 behaviours from 5.1 and 5.2 had no named test. Fixed: 5.3 "6′. The full test register".
- [x] Test Engineer B2: the permit-wait test couldn't run as written. Fixed: 5.3 "Permit wait (Test Engineer B2)" (a fake permit and fake clock; under 1 s of real time).
- [x] Test Engineer B3: the listener fake. Fixed: 6′, AC8 row (keyed on `(type, fn, capture)`, with a negative self-test).
- [x] Test Engineer B4: a JS group could pass with zero checks. Fixed: 5.3 "The JS group runner (Test Engineer B4)" (`checks=<n>`, floors written in the test).
- [x] Test Engineer B5: contract strings had no extractor. Fixed: 5.3 "Extractors and floors" (the new `verbatim_strings` and `align_reasons` extractors).
- [x] Test Engineer B6: the `ImportError` reason. Fixed: 5.3 server tests (maps to `whisper_missing`; no new value).
- [x] Test Engineer B7: page selectors. Fixed: 5.3 "Extractors and floors", `page_selectors` (`closest|matches`, `.ts` excluded, `player.matches(".ad-showing")`, floor 13 to 16).
- [x] DevOps B1: no runbooks for the new errors. Fixed: 5.3 "Ops", B1 (`server-busy.md`, `runbooks/README.md` index and less-common rows, `youtube-rate-limit.md`, `deployment.md` rollback).
- [x] DevOps B2: errors raised mid-stream didn't show in the logs. Fixed: 5.3 "B2" and "Logging (DevOps B2)" (`align: <reason> <video_id>` for every error; a test that no word is ever logged).

**Review Coordinator**
- [x] The text of `ALIGN_RATE_LIMITED_TEXT` differed from the wording UX suggested ("within ten minutes"). UX wins on user-facing text (`AGENTS.md` § 3), and an edit made after an agent's PASS must be re-confirmed by that agent (`docs/reviews/README.md`, "Sign-off is on the final content"). Resolved: the Coordinator sent the exact string to UX, and **UX PASSed it as final**: "the wait is at most ten" sets an upper limit rather than a deadline, and it holds for a fresh 429 and for one partway through the cooldown.

The activity log names every blocker above, and each has a matching amendment in the plan text. No blocker is deferred.

## Warnings (fix before next review)
**Procedural (Review Coordinator), to fix in the commit that adds this artifact:**
- [ ] **Task file frontmatter is out of step with the work.** `state: proposed` (`tasks/T-020.md:6`), but the plan is under review on its branch. `AGENTS.md` § 7 step 2 calls for `in-review`. `review_artifact: —` (`:10`) should become `docs/reviews/T-020/plan.md`. After the owner's approval (step 4b), the state becomes `in-progress` (§ 7 step 4). This artifact did not change the task file, as instructed.
- [ ] **The folder name.** `docs/reviews/README.md` names a review folder after the task only "when the task gives the folder a name". Today only the plan's Ops section names `docs/reviews/T-020/` (`tasks/T-020.md:688`). Setting `review_artifact` as above makes the name official. Otherwise the rule gives `docs/reviews/feat-profanity-mute/`.
- [ ] **Activity-log order.** The five entries for the rev 5 rounds (`tasks/T-020.md:742-747`) are in oldest-first order, above the "plan rev 5 drafted" entry (`:748`). The rest of the log is newest-first. Re-order them newest-first.
- [ ] **Spot-checks per agent weren't kept.** `docs/reviews/README.md` asks every sign-off to list the `path:line` places its agent read. The agents' reports weren't saved, so the sign-offs below cite the code references written into each agent's amendments, labelled as such. The build review must record each agent's own spot-checks.
- [ ] **The uncommitted task file.** `tasks/T-020.md` carries uncommitted rev 5.1 to 5.3 text. Commit it with this artifact (with `T-020` and `unanimous-consensus: T-020` in the message), so the plan that was reviewed is on the branch before the owner approves it.

**Carried into the build (flagged by reviewers "for the build"):**
- [ ] **The real-browser check (supports the review; doesn't gate it), on three test videos, muted at the tab.** It covers:
  - word-timing precision (§ 3.4: a word mid-sentence, a fast talker, music behind speech);
  - whether YouTube's volume icon follows the mute, and whether its saved volume carries a mute into the next video;
  - `muted` across ads and quality changes;
  - autoplay with the toggle as the gesture, in Chrome and Firefox;
  - moo loudness against the bleep, and the bleep no louder than the speech around it (lower it to 0.10 if it is louder).

  The measured level goes in `ux.md`, and all the results go in the build artifact.
- [ ] **Open `UNKNOWN`s to settle or keep honest in the docs:** faster-whisper precision (§ 3.4); the latency and cost estimates (§ 9); Firefox autoplay (§ 2); whether YouTube's telemetry reports the `muted` change (§ 8 of `architecture.md`, 5.1 warnings). Hidden-tab throttling is answered by the Q9 tests and the `ux.md` limit.
- [ ] **The owner's manual gate:** the owner reads `extension/mute_words.js` (mild, no slurs) at step 4b and again at the build review (AC6).
- [ ] **Profanity hygiene:** the default words live only in `extension/mute_words.js`. They never go in docs, task files, review artifacts, the PR body or commit messages, and tests never print the array (§ 4.4; 5.3).
- [ ] **Floors and `MIN_TESTS`:** set them to measured values, in every place 5.3 lists (`scripts/ci-tldw-tests.sh:27`, `scripts/test/test-ci-tldw-tests.sh` lines 51–87, `docs/testing.md` lines 12, 17 and 125).
- [ ] **Line numbers.** Re-check every `path:line` in the plan against the branch when building. Backend already corrected two (`txcache.py:25`, about `whisper_transcribe.py:205`).
- [ ] **The PM lens condition.** If the build slips, the named cut is caption-line covering only. **Invoking that cut changes AC2, AC3 and AC9.** It needs the Analyst and the owner, and a re-review of what changes; it is not shipped quietly.
- [ ] **Demo readiness** (DevOps, for the build-review checklist): ffmpeg on the demo server's PATH; a faster-whisper model already in the demo HOME; reload the server, then the extension card, then the tab; keep the Options textarea off a shared screen.
- [ ] **Commits and PR:** every commit carries `T-020`; any commit touching authored `docs/` carries `unanimous-consensus: T-020`; no `--no-verify`; the PR targets `hackathon/demo`, never `main`.
- [ ] **The docs changes in § 7 and 5.3** need unanimous 9-agent consensus at the build review, checked against the code, not the prose (`AGENTS.md` § 6.0(5)).

## Suggestions (consider for polish)
- UX: "within ten minutes" for the rate-limit text. Not taken; the pinned wording was confirmed by UX (see Blockers).
- UX: a link to Options from the empty-list status. Out of scope for v0 (5.2 UX warnings).
- Backend, DevOps: `--download-sections` for long videos. Not now (Q1); a candidate follow-up if long-video downloads prove slow in the browser check.
- Backend: joining a running alignment from a second tab. Not now (Q10).

## Answers to the open questions (Q1–Q10)
1. **Sections for long videos:** no spike. Full audio reuses the hardened `download_audio` at the same cost of one extraction, bounded by the duration ceiling and the 300 MB cap. Out of scope. (5.1)
2. **Cookies for the alignment download:** no. The cookie database is classed secret, and reading it prompts for a keychain entry. `data-model.md` and the runbook say the alignment download doesn't pass it. Out of scope. (5.1)
3. **Language:** the caption track's language is not sent, since a translated track isn't the audio's language. `language=None` stays, and `lang=auto` is an explicit key term. (5.1)
4. **Policy:** `off`, pinned or client-sent, blocks alignment. `ask` allows it with no second prompt, because turning the feature on is the consent. The Options Whisper hint says so, verbatim, in 5.2, and is recorded in `contracts.md`. (5.1, 5.2)
5. **Bleep on line ranges:** capped. Sound plays only in the first 1500 ms of any range, for bleep and moo; AC4 amended. (5.2 UX B5)
6. **`tldw cache` and store 13:** in scope. Areas are cleaned by name, and a test covers it. (5.1, 5.3)
7. **`initial_prompt`:** no. Caption text is uploader-controlled, would bias Whisper toward echoing `[ __ ]`, would have to join the key, and would break the body cap. Out of scope. (5.1)
8. **A marker hit where Whisper hears an unlisted word:** the line range is kept. A positional guess has no stated accuracy and can't be tested. (5.1)
9. **Hidden-tab timer throttling:** no five-minute hidden run. Tests show `timeupdate` alone enters and exits within one tick with the timer stubbed, and that the timer re-arms at most twice. `ux.md` records the limit at 1.25× and above. (5.2, 5.3)
10. **One alignment at a time:** accepted. The cache-first path runs before the lock, so a cached re-toggle in a second tab still works. Out of scope: joining a running alignment. (5.1)

## Regression safety (planned)
This updates the plan's § 8 for store 13 and the `reason` field. The build review re-states it against the real diff.

Regression-safety: contracts touched =
- **additive:**
  - the route `POST /align/stream`;
  - the NDJSON type `window`;
  - the error field `reason` on every align error, plain-JSON and in-stream, with the values `whisper_off`, `whisper_missing`, `model_missing`, `ffmpeg_missing`, `too_long`, `busy`, `rate_limited`, `download_failed`, `timeout` and `other`, plus `token` and `unreachable` from `background.js`. It is carried only on the new route's errors, so no existing route's error body changes;
  - five port message types: `align`, `alignWindow`, `alignProgress`, `alignDone`, and `alignError {status, reason, error, installHint}`;
  - the storage keys `muteWords` and `muteSound`;
  - **cache store 13, "Alignment cache"**, and its home path `~/.cache/youtube-tldw/align/`;
  - one more `tldw cache` area;
  - the align log line `align: <reason> <video_id>`;
  - the new limits and shared state (`MAX_ALIGN_WINDOWS`, `MAX_ALIGN_WINDOW_MS`, `ALIGN_DOWNLOAD_TIMEOUT`, `ALIGN_CUT_TIMEOUT_S`, `ALIGN_BUDGET_S`, the 20 s permit wait, `_align_lock`, `_align_backoff_until`);
  - the page selectors `#movie_player`, `.ytp-mute-button`, `.ad-showing` and `#tldw-mute-pill`;
  - the verbatim statuses, `ALIGN_RATE_LIMITED_TEXT`, the server details and the Options hint text;
- **changed, but backward-compatible:**
  - the `executeScript` files list and the Firefox copy list (one file added);
  - `download_audio` (a new optional `max_duration_s`, with argv byte-identical when it is absent, tested);
  - `transcribe` (`_load_model` factored out, behaviour identical, the existing tests unchanged; still one `WhisperModel(...)` site);
  - `txcache._prune` (an optional `cap`, default unchanged);
  - `tldw cache clean` (areas chosen by name instead of by position; the same three existing areas are cleaned);
  - the Options Save write (7 keys, plus `muteSound`, plus `muteWords` or its removal);
  - the `data-model.md` statement that no store carries a schema version (amended, because store 13 carries `ALIGN_SHAPE`).

Reflected in docs = yes, planned in the same PR (`contracts.md`, `api.md`, `data-model.md`, `architecture.md`, `domain.md`, `ux.md`, `glossary.md`, `testing.md`, `deployment.md`, `runbooks/README.md`, `runbooks/server-busy.md`, `runbooks/youtube-rate-limit.md`). Cross-repo consumers = none (single repo; the fork is thrown away, and `main` is untouched). Compatibility = backward-compatible. An old extension against the new server never calls the route. The new extension against an old server gets 404, and its hits stay line-timed with the "Older server" status. A rollback leaves `~/.cache/youtube-tldw/align/` behind, which the old `tldw cache` doesn't see; `deployment.md` says to delete it by hand.

## Agent Sign-offs
Each line gives the final verdict on the plan. The spot-checks are the code references written into that agent's amendments in `tasks/T-020.md`, as the main session reported them. They are not the agents' own lists (see Warnings).
- [x] Analyst — PASS (Round 1 re-check, rev 5.1). Spot-checks in its amendments: AC9 key clause; `content.js:480-499` (no caption-track language in `pageMeta`, Q3).
- [x] Architect — PASS WITH ITEMS (Round 1 re-check, rev 5.1); its items are the "Round 1 warnings, adopted" in 5.1. Spot-checks in its amendments: `server.py:955-960`, `:1102` (`_guarded`); `proc.py:66-103`; `docs/data-model.md:278` (cookies).
- [x] Data Engineer — PASS (Round 1 re-check, rev 5.1). Spot-checks in its amendments: `docs/data-model.md:23`, `:220` (store 9 taken), `:344` (schema-version statement), `:365`, `:71` (fingerprint gaps).
- [x] Backend — PASS (Round 2 re-check, rev 5.2). Spot-checks in its amendments: `txcache.py:25` (`TTL_SECONDS`); about `whisper_transcribe.py:205` (`cpu_threads`); the `handleSummarize` `gotTerminal` rule.
- [x] Frontend — PASS (Round 2 re-check, rev 5.2). Spot-checks in its amendments: `content.js:292` (`shieldKey`); `content.js:843-847` (`showError`).
- [x] UX — PASS (Round 2 re-check, rev 5.2, after the Stop-on-pill fix), and PASS on the final `ALIGN_RATE_LIMITED_TEXT` (Coordinator re-check). Spot-checks in its amendments: `cli.py:314-337` (the `tldw config set whisper-max-minutes` form).
- [x] Test Engineer — PASS WITH ITEMS (Round 3 re-check, rev 5.3); its one item, the re-arm test, is adopted (6′, Q9 row). Spot-checks in its amendments: `content.js:1250` (`.ts` is tldw's own markup); `scripts/test/test-ci-tldw-tests.sh` lines 51–87; `docs/testing.md` lines 12, 17, 125.
- [x] DevOps — PASS (Round 3 re-check, rev 5.3). Spot-checks in its amendments: the runbooks named in 5.3 Ops B1; `deployment.md` § 9/§ 10.
- [x] Review Coordinator — PASS WITH ITEMS. Rounds in order, every blocker matched to an amendment, none deferred, all 8 domain sign-offs present, the 7 lenses run with their conflicts surfaced, Q1–Q10 answered, and no unreplaced template placeholder in the task file (searched). The items are the procedural warnings above. Spot-checks: `tasks/T-020.md:6`, `:10` (frontmatter); `:742-748` (log order); `docs/data-model.md:12-25` (stores end at 12, so 13 is free); `extension/content.js:292` (`shieldKey`); `src/youtube_tldw/cli.py:321-337` (`whisper-max-minutes`).

## Content hash
n/a — not the final sign-off.

## Lens Sign-offs (major decisions only)
A major decision: a new feature with a new server route and cache store. The main session ran the lenses on rev 5 to 5.3 (`tasks/T-020.md:690-715`).
- [x] CEO: support. The risk is the default word list, mitigated by the owner's manual gate (AC6).
- [x] Purchasing: support. No new dependency, no sound asset or licence, no Claude usage, no cookie read.
- [x] PM: support, with a condition. About a day of agent work, built in layers. The named fallback is caption-line covering only (see Warnings: invoking it is a scope change).
- [x] Adopter: support. A word-timed bleep capped at 1.5 s; the per-tab friction is stated in `ux.md`.
- [x] Builder: support. Fenced pure functions, reused server patterns, `reason` on every error, a large test register.
- [x] Investor: neutral. No market claim; not an objection.
- [x] Marketing: support. A clear hook, and an honest answer to "does it catch everything?".

**Conflicts between lenses:** PM against Adopter, on build size against the word-timing payoff. Resolved by the layered build and the named fallback, and by the owner's explicit choice of the hybrid design. Investor (neutral) against Marketing (support) is not a conflict, since the fork makes no market claim. No lens opposes.

## Next step
**The plan awaits the owner's approval (step 4b). That approval has NOT happened.** No build starts until the owner approves the reviewed plan and the activity log records who approved it and when.
