# Review: chore/docs-tier2 — Round 1, Analysis (Tier 2 drafts)

**Status:** PASS WITH ITEMS. The Data Engineer blocked on two items. All three roles signed off after one fix pass, re-checked against the files and the code.

**How it was reviewed:** one fresh agent played the Analyst, Architect and Data Engineer (`AGENTS.md` § 12 Knob 1). Each role read its own brief and redlines and gave its own verdict.

## Blockers (all fixed)
- [x] **Data B1.** `domain.md:8` and § 7 item 1 still called the `segment_ratio` disagreement open, though this PR corrects `contracts.md`. Fixed: both now say it was corrected in this PR, citing `_run_cached` (`server.py:1350-1367`).
- [x] **Data B2.** `contracts.md`'s probe row cited the wrong handler (`server.py:1236-1242`, the stream's cache-hit branch). Fixed: it now cites `_run_cached`, validation and key at `:1350-1359` and the prefetch at `:1364-1367`. The `segmentRatio` row's blast radius also covers the prefetch after a fresh summary (`:1319-1323`).

## Warnings (all applied)
- **Analyst:**
  - stale line cites in `ux.md` (now `T-009.md:38`, `T-019.md:18`);
  - `glossary.md`'s `chosen_ratio` is marked "parsed but never shown";
  - `onboarding.md:47` (replace a *leaked* token) and the runbooks' "never delete the token file" are reconciled by cross-references.
- **Architect:**
  - both runbooks point to onboarding's "First 30 minutes" step 3 for replacing a leaked token;
  - ADR 0004 cites the deferred records at `T-017.md:23` and `:25`.

## Spot-checks
- **Analyst:** `T-001.10.md:24-26`; the runbooks list matches `ls`; T-019 against `manifest.json:3`, `manifest.firefox.json:3` and the guard; 723 = 717 + 6; `README.md:14` against T-001. Two ✘ were stale cites, since fixed.
- **Architect:** `uv.lock:573-575` (piper-tts 1.4.2); `audio.py:33`, `:205-206`; `architect.md:38`; `T-015.md:28`, `:43`, `:44`; `server.py:1959`; `ux.md:91` against `architecture.md:145`, `:157`, `:208`; `data-model.md:278`.
- **Data Engineer:** `server.py:748-770`, `:1239-1242`, `:196`, `:422-431`; `background.js:111`, `:152`; `data-model.md:285-291`.

## Sign-offs
- [x] Analyst — yes
- [x] Architect — yes
- [x] Data Engineer — yes (after B1 and B2)
