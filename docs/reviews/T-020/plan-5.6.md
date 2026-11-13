# Review: feat/profanity-mute, plan rev 5.6 (T-020): settings apply live

**Task:** T-020 (`tasks/T-020.md`, "Plan rev 5.6" and its Round 1–3 amendments).
**Change:** `muteSound` and `muteWords` take effect while "Cover listed words" is on, not only at toggle-on (the owner: "settings should be hot reloaded whenever possible").
**Review scope:** a behaviour change to a documented contract, so the full cycle (3 rounds, the Coordinator) and the `/docs` consensus gate at build. The 7 lenses are skipped, since this isn't a major decision (Analyst, confirmed).
**Status:** PASS WITH ITEMS. **The owner approved on 2026-11-13 (step 4b):** "Approve, build it", including AC18.

## Rounds
| Round | Agent | Verdict | Blockers |
|---|---|---|---|
| 1 | Analyst | PASS WITH ITEMS | B1: owner approval before code (closed: approved 2026-11-13) |
| 1 | Architect | PASS WITH ITEMS | none |
| 1 | Data Engineer | PASS WITH ITEMS | none |
| 2 | Backend | PASS WITH ITEMS | none |
| 2 | Frontend | PASS WITH ITEMS | 3, resolved (`quietCur`; the rebuild resets timing state; a live end uses `stop`) |
| 2 | UX | PASS WITH ITEMS | none |
| 3 | Test Engineer | PASS WITH ITEMS | none |
| 3 | DevOps | PASS WITH ITEMS | none |
| — | Review Coordinator | PASS WITH ITEMS | none; 4 stale plan lines fixed |

Every item is written as a binding amendment in `tasks/T-020.md`: "Rev 5.6 Round 1", "Round 2", "Round 3", and "the Coordinator and the Analyst's scope ruling".

## Warnings carried to the build
- Retry a busy only before the download, and clear the timers on every end (DevOps).
- New CI floors (the `live` group, `MUTE_TEXT` 43 → 47, `MIN_TESTS`), and one `onChanged.addListener` (Test Engineer).
- Runbook lines in `server-busy.md` and `youtube-rate-limit.md` (DevOps).

## Agent sign-offs (plan)
- [x] Analyst  - [x] Architect  - [x] Data Engineer  - [x] Backend  - [x] Frontend  - [x] UX  - [x] Test Engineer  - [x] DevOps  - [x] Review Coordinator

## Lens sign-offs
Not run. This isn't a major decision (AGENTS.md; Analyst).
