# PLAN — tag automatic summaries in the usage stats

**Status: PASS.** Plan rev 5 was approved by the owner on 2026-11-14 and has been built and reviewed; see "Build review".

- **Branch:** `feat/auto-run-stats`.
- **Issue:** drewcurley/tldw#20. A follow-up to #19 that the owner approved, coming from the Data Engineer's dissent on Q5.
- **Review scope:** this changes the usage data (schema), adds a server route and touches the extension, so it gets the full cycle: all 8 agents and the 7 lenses. The owner approves the plan before any code.

## The problem

`usage.note_video` (`usage.py:192`) writes one `video` row for each summarized video, and `stats()` (`usage.py:208`) counts every one of them:
- they make up the "You've TL;DW'd N videos — X hours you didn't have to watch" line;
- the same count is the 25-video threshold for the sponsor prompt (`content.js` `maybeThank`).

Automatic summaries (#19) are written the same way, so they inflate both counts **even when nobody reads them**. A row with no tag can never be sorted out later.

## The change

1. **The tag, end to end.**
   - **The page:** for an automatic run (`run.auto`), `content.js` puts `auto: true` on both `probeCache` and `summarize`. Today only `summarize` carries it, for the budget. The budget is unchanged and still counts `summarize` only.
   - **The worker:** `handleProbe` and `handleSummarize` add `trigger: "auto"` to the body they send the server **only when `msg.auto === true`**.
   - **The server:** `_trigger(body)` returns `"auto"` exactly when `body.get("trigger") == "auto"`, and `""` for anything else. It's an allowlist, so no free text reaches the log.
2. **Recording.**
   - In `_run_stream`, both `usage.interaction("summarize", vid, trigger=…)` blocks (`server.py:1204`, `:1297`) and `note_video(…, trigger=…)` carry the trigger.
   - The `video` row gets `"trigger": "auto"` only for automatic runs. Rows for clicks stay exactly as they are today.
   - The model-call rows from `record()` already copy `inter.trigger`, so token and cost accounting are tagged too.
3. **"Served": evidence that someone read an automatic summary.** It's a new row, `{"kind": "summary_served", "video_id", "how", "ts"}`, written by `usage.note_summary_served(vid, how)`. Where and when it's written is in **R3, R4 and R6** below.
   - Every summary someone opens from a cache writes one, manual ones included. That's harmless for the count, and it's the same kind of slow log growth `segments_served` already causes. It's expected.
4. **`POST /usage/served`:** see **R5**. An older server answers 404, and the worker ignores that.
5. **`stats()`:** see **R1**. Rows written before this change have no tag, so they keep counting.
6. **Not changed:** the budget and the badge. `tldw usage` changes only as in **R2**: automatic runs stay in its cost and token figures, because they're real spending.
7. **Docs.**
   - README: how `tldw usage` and the "videos TL;DW'd" count treat automatic summaries.
   - `extension/README.md`: one line in the automatic-summaries section.

## Rev 2: Round 1's decisions (these take precedence over "The change" above where they differ)

- **R1. Counting** (Data B2). A video counts if **any** of its `video` rows has no tag, or **any** `summary_served` row exists for it.
  - `watch_ms` and `read_ms` come from its latest `video` row.
  - It's one pass over the rows, with no timestamp comparisons. A clock change can't flip it, and a later automatic run can't make a video someone read stop counting.
  - Being served before a run still counts, because it shows the video was read.
- **R2. `tldw usage`** (Data B1). Today it keeps every row whose kind isn't `video` or `segments_served` as a model call (`cli.py:489`). That would turn `summary_served` rows into $0 "calls".
  - `usage.py` gets one `NOT_MODEL_CALLS = {"video", "segments_served", "summary_served"}`, and the CLI filters with it.
  - A test checks that a `summary_served` row never shows up as a call, a step, or a per-video entry.
  - The plan's "Not changed: the CLI" is **struck**.
- **R3. Order** (Data 3). On a cache hit, the served row is written **before** `usage.stats()` builds the payload, so the count returned includes it.
- **R4. Served, by every route that shows a summary** (Analyst 2).
  - **The page** keeps `autoResults`, a page-lifetime `Set` of videos whose **automatic run produced a result by any route** (a summarize or a probe hit). `showResult(p)` for a video in it sends `{ type: "usage:served", video_id: p.video_id }` and removes it. That's once per video per page, by any route: the notice, the toolbar, the badge, or re-attaching.
  - **The worker** POSTs `/usage/served` whenever it answers a **non-automatic** request from **its own** cache (`handleProbe` at `background.js:113`, `handleSummarize` at `:150`), since that request never reaches the server. It's fire-and-forget.
  - **The server** writes `how: "cache"` or `"probe"` for non-automatic cache hits, as in rev 1.
- **R5. The route** (Architect 1, 2, 4).
  - It's wired as `self._guarded(self._run_served, slots=None)` beside `/summarize/cached`. That gives the token check, the 16 KB JSON-only body limit, and no slot.
  - It needs a JSON object, and `canonical_video_id(str(body["url"]))`.
  - Any log line names only the video ID, never the raw body.
  - **Correction to rev 1:** the server doesn't reject requests by Origin; Origin only decides the CORS headers. The route is protected by the bearer token plus CORS preflight, like every other route.
- **R6. The worker's `usage:served` handler** (Architect 5):
  - It sits in the existing `onMessage` listener;
  - it needs `sender.id === runtime.id`, a top frame (`frameId === 0`), and `video_id` matching `^[A-Za-z0-9_-]{11}$`. The ID comes from the message, never from `sender.tab.url`, which moves during in-page navigation;
  - it returns `undefined` and doesn't reply;
  - a 404 from an older server, or a network error, is swallowed.
- **R7.** `_run_buffered` (`/summarize`) stays untagged: the extension never calls it (Analyst 6).
- **R8. Schema note** (Data 6). `trigger` means different things per kind: `prefetch` or `request` on `segments` rows, `auto` on `summarize` rows. Any later breakdown has to group by kind and trigger together.
- **R9. Known lag** (Data 4). An automatic result's `stats` snapshot leaves its own video out, so the sponsor prompt's count lags by one until the next result. That's accepted.
- **Q1 answered:** yes, a summary opened later counts. That's R4.
- **Q2 answered:** a new route is right. Riding on an existing route would either make a read path write rows or take a slot.

## Rev 4: Round 2's decisions (these take precedence where they differ)

- **B1. One block to tag** (Backend 1). `_run_stream` has a single `usage.interaction("summarize")`, in its `_summarize` closure (`server.py:1297`, with `note_video` at `:1302`). It's called from both `:1316` and `:1336`, so tagging it once covers both. `server.py:1204` is `_run_buffered`, which R7 leaves untagged.
- **B2. `_trigger(body)`** is `"auto"` only when `isinstance(body, dict) and body.get("trigger") == "auto"`.
- **B3. What "tagged" covers** (Backend 3). It covers the summarize step only. The key-moment prefetch that follows runs under its own `trigger="prefetch"`. Rev 1's "token and cost accounting are tagged too" is narrowed to that.
- **B4. Routing.** `/usage/served` is dispatched before the 404. It reads `body.get("url")`, so a missing URL is a 400, not a 500. The worker sends `{ url: <id> }` with no `model`.
- **B5. The worker's cache hits.** `handleProbe` and `handleSummarize` take `auto` from `msg`. After `safePost`, they call an un-awaited `noteServed(id).catch(() => {})`, because `getSettings` is async.
- **F1. The page's served signal follows what went out, not the run's current state** (Frontend BLOCK).
  - The bug: a run that was automatic when its probe or summarize went out, and was then made manual by a toolbar click, was still logged as `auto` by the server. But it would never be counted as read, because its result skips `autoWording()`.
  - **The fix:**
    - `run.sentAuto = true` whenever a probe or summarize goes out with `auto`.
    - In the result branch, **before** the backgrounded/shown split (which also covers a probe hit that falls through), add `videoId` to `autoResults` when `run.sentAuto` is set.
    - A new AC covers it: re-attach after the send, then a result shown in the panel, sends `usage:served`.
- **F2. Where it lives.**
  - `servedShown(p)` sits next to `badgeShown` (in the toasts region the tests slice), and `showResult` calls `badgeShown(p); servedShown(p);`.
  - The sends go through a generalized, wrapped `sendRuntime(obj)`. `sendBadge` becomes a thin call to it.
  - `autoResults: new Set()` goes into the `probe_checks.js` and `auto_run_checks.js` sandboxes.
  - The `showResult` stub and the source check are updated.
- **F3.** The probe message is `{ type: "probeCache", url, videoId, ...(run.auto ? { auto: true } : {}) }`.
- **UX copy** (Q3 answered: one line is enough):
  - **README, "Token usage and cost":** "Automatic summaries are included in the cost and token figures — they're real spending — but a video only counts toward "videos TL;DW'd" and hours saved once you open its summary."
  - **`extension/README.md`**, appended to the "Each one uses your Claude plan" bullet: "It counts toward "videos TL;DW'd" only once you open it."

## Rev 5: Round 3's decisions

- **S1. `_trigger`'s non-dict guard** can't be reached over HTTP, since `_validate` rejects those bodies first. It gets direct unit tests: `None`, `[]`, `"auto"`, `"AUTO"`, `1`, `True`.
- **S2. The worker's cache-hit ID is `msg.videoId`,** checked against `^[A-Za-z0-9_-]{11}$`. It isn't `msg.url`, which is a full watch URL; AC7 is corrected.
  - A probe hit that seeds the cache with a bad `videoId` sends no served fetch.
  - The tests use realistic messages: a full URL plus an 11-character `videoId`.
- **S3. Testing "after `safePost`, not awaited":**
  - One ordered log records both port posts and fetches.
  - A served fetch that never resolves must still let the cached answer go out and the handler return.
- **S4.** `probe_checks.js` gets an `unhandledRejection` hook. Every changed node script raises its minimum.
- **D1. Version 1.6.0** in both manifests. The existing check already confirms they match. `pyproject.toml` stays as it is, as it did for the last two features.
- **D2. Observability:**
  - The server logs one line per served row: `served <id> (<how>)`, with the ID and `how` only.
  - The `summarized '…'` line gains ` [auto]` for automatic runs.
  - The worker stays silent on failure.
- **D3. Rollout:** order doesn't matter (the skew table holds both ways).
  1. Restart `tldw serve`.
  2. Reload the extension.
  3. Refresh YouTube tabs.
- **D4. Rollback, documented in the PR.** Nothing crashes, but older code:
  - counts `summary_served` rows as $0 model calls in `tldw usage`, with a `?` step and an "(unrecorded)" model bucket;
  - ignores the tag, so the "videos TL;DW'd" count goes back up.
- **D5. Accepted:** automatic rows written between #19 and this change are untagged, and keep counting.
- **Mutations** (SDET, Round 3), each of which must fail a test:
  - **Server:**
    - `_trigger`: `.lower()`, a truthiness test, `in ("auto", True)`, the `isinstance` check dropped.
    - The `video` row of a manual run gets a `trigger` key.
    - The trigger is omitted from `interaction`.
    - A served row is written for an automatic cache hit, or on a fresh summarize, or **after** `stats()`.
    - The served row's `how` is wrong.
    - The route's auth, `isinstance` and `.get` checks; the 413/415 limits; `slots=None`; dispatch after the 404.
  - **`stats()`:** "latest row untagged" instead of "any"; served rows ignored; served-only videos counted; `watch_ms` from the first row.
  - **CLI:** `summary_served` dropped from `NOT_MODEL_CALLS`; a local copy of the set instead of the import.
  - **Worker:**
    - `auto` checked for truthiness, in the probe and in the summarize;
    - the trigger sent on the probe only;
    - `noteServed` called for automatic hits, or awaited;
    - the ID check dropped;
    - the `usage:served` handler's `sender.id`, `frameId` or regex dropped;
    - it returns `true`;
    - a missing `.catch`.
  - **Page:**
    - `sentAuto` not set on the probe, or set for manual runs;
    - `autoResults` filled after the split;
    - no `delete`, so it sends twice;
    - it matches the run's `videoId` instead of `p.video_id`;
    - `sendRuntime` loses its `try`;
    - the probe sends `auto` unconditionally, or from `auto` instead of `run.auto`.

## Version skew

| Server | Extension | What happens |
|---|---|---|
| new | old | No trigger is sent, so everything counts as it does today. |
| old | new | The server ignores the extra `trigger`. `/usage/served` returns 404, which is ignored. Everything counts as it does today. |

Nothing breaks either way, and the count only improves once both are new.

## Acceptance criteria

1. **The page.** An automatic run's `probeCache` and `summarize` carry `auto: true`. A manual run's don't, and neither does a run made manual by a toolbar click *before* its `summarize` goes out. Both carry `auto` only when `run.auto` holds.
2. **The worker.**
   - `trigger: "auto"` is in the probe and summarize bodies exactly when `msg.auto === true`. `"true"` and `1` don't count.
   - The budget still counts `summarize` only. The probe never records.
3. **The server.**
   - `_trigger` is `"auto"` only for exactly `"auto"`; `"AUTO"`, `1`, `true`, a list and a missing field all give `""`.
   - An automatic stream writes a `video` row with `trigger: "auto"`, and its summarize-step rows carry it too. The prefetch keeps `"prefetch"` (B3).
   - A manual stream's `video` row has no `trigger` key at all.
4. **Served.**
   - A non-automatic cache hit writes `summary_served`: `how: "cache"` on the stream and `"probe"` on `/summarize/cached`.
   - An automatic cache hit writes nothing.
   - `POST /usage/served` writes `how: "viewed"`.
5. **The route.**
   - No token or a wrong one gives 401.
   - A bad URL, or a body that isn't an object, gives 400.
   - A body that's too large gives 413, and the wrong content type 415 (`_read_body`).
   - It's 200 when valid.
   - It never takes a slot: it works while the claude pool is full.
6. **`stats()` (R1).**
   - An automatic row with nothing served isn't counted.
   - With any served row for the video, at any time, it's counted.
   - An untagged row plus a later automatic row still counts.
   - Old untagged rows count.
   - Re-summarizing still counts a video once.
   - `watch_ms` and `read_ms` come from the latest row.
   - The sponsor prompt's input follows.
   - Served rows are written before the payload's `stats` (R3).
   - A `summary_served` row for a video with no `video` row doesn't make it a video (Data, rev 2).
   - `NOT_MODEL_CALLS` is one `frozenset` in `usage.py`, imported by the CLI (Data, rev 2).
   - **`tldw usage` (R2):** a `summary_served` row isn't a call, a step or a per-video entry.
7. **The served signal (R4, R6).**
   - **The page:**
     - A video enters `autoResults` when a result arrives for a run whose probe or summarize went out with `auto` (`run.sentAuto`, F1). That includes a run made manual afterwards: a click after the probe went `auto` but before the summarize sends the summarize without `auto`, and the video still enters `autoResults`.
     - `showResult` for it sends `{ type: "usage:served", video_id }` once, by any route.
     - Nothing is sent for another video, for a manual-only video, or a second time.
     - A throwing or rejecting send never escapes.
   - **The worker:**
     - It POSTs `/usage/served` with the token for a valid message from our own extension's top frame.
     - A foreign sender, a sub-frame, or a bad ID does nothing.
     - A non-automatic answer from its own cache POSTs it too; an automatic one doesn't.
     - That POST goes out **after** `safePost`, isn't awaited, and is wrapped in `try`/`catch`, so a hung server never delays a cached answer.
     - It first checks `msg.videoId` against the 11-character ID rule (S2). It isn't `msg.url`.
     - A 404 or a network failure is swallowed.
8. **Coverage.**
   - Every guard is mutation-checked.
   - The node checks (`worker_checks`, `auto_run_checks`, `probe_checks`) and the server and usage tests are extended, with the minimums raised.
   - The full suite passes locally; the repo has no CI.
9. **Docs** as in item 7 of "The change".

## Out of scope

- A per-trigger breakdown in `tldw usage`.
- Rewriting history: old rows stay untagged.
- Skipping the key-moment prefetch for automatic runs, which is the second model call.
- Tagging Listen, key moments or questions, which are never automatic.

## Seven lenses

1. **CEO:** a correctness fix to a number the product shows the user, and it stops asking for sponsorship too early. Low risk.
2. **Purchasing:** one local route, with no new data leaving the machine.
3. **Product/PM:** small, and the owner approved it. It was the Data Engineer's first-follow-up ask.
4. **Daily user:** invisible, except that the count is honest. No homework.
5. **Developer:** "served" is the user's own evidence of reading, kept locally, and never leaves the machine. It isn't surveillance: it only corrects a count shown to the same user.
6. **Investor:** honest usage numbers, if they're ever quoted.
7. **Marketing:** "N videos you didn't have to watch" stays true.

**Conflicts:** none.

## Open questions for reviewers

- **Q1:** answered. Yes (R4).
- **Q2:** answered. A new route (R5).
- **Q3:** answered. One line is enough, plus the extension README line (rev 4).

## Review log

How it's reviewed: one fresh agent per round, playing that round's roles. Each role has its own brief and gives its own verdict, and the rounds run in order.

- **Round 1 (rev 1):**
  - **Analyst: PASS WITH ITEMS.**
    - Two paths would never count as served: the worker's cache and an automatic probe hit.
    - The CLI has to change.
    - AC7's message shape; AC5's status codes.
    - `_run_buffered`.
  - **Architect: PASS WITH ITEMS.**
    - Wire the route through `_guarded` with `slots=None`.
    - The plan's wording on Origin was wrong.
    - No new abuse path.
    - The worker takes the video ID from the message, and needs `frameId` 0.
    - Q2: a new route.
  - **Data Engineer: BLOCK.**
    - B1: `tldw usage` would count the new rows as model calls.
    - B2: the counting rule could make the count go down, and depended on clock order.
    - Write the served row before `stats`.
    - A known lag, and the schema note.
  - **All addressed in rev 2.**
- **Round 1 confirmation (rev 2):** all three roles PASS.
  - Analyst: fold the R items into the main sections; mark Q1 and Q2 answered.
  - Architect: the worker POST mustn't be awaited; check `msg.url`.
  - Data Engineer: a served row with no `video` row isn't a video; note the log growth; make `NOT_MODEL_CALLS` a `frozenset`.
  - **Applied in rev 3.**
- **Round 2 (rev 3):**
  - **Backend: PASS WITH ITEMS.** The single `_run_stream` block, `_trigger` with no body, what "tagged" covers, routing and `body.get`, the worker's un-awaited POST, the CLI.
  - **Frontend: BLOCK.** A run re-attached after its send would never count as read. Fixed with `run.sentAuto`. Also: the helper's placement, `sendRuntime`, and the sandboxes.
  - **UX: PASS WITH ITEMS.** The copy for both READMEs; Q3.
  - **All addressed in rev 4.**
- **Round 2 confirmation (rev 4):** all three roles PASS. The Frontend BLOCK is lifted. The AC wording fixes they suggested (AC3, AC7) are applied.
- **Round 3 (rev 4):**
  - **SDET: PASS WITH ITEMS.** S1–S4 and the mutation list.
  - **DevOps: PASS WITH ITEMS.** D1–D5.
  - **Applied in rev 5. Plan review complete.**

## Sign-offs (plan)
- [x] Analyst
- [x] Architect
- [x] Data Engineer
- [x] Backend
- [x] Frontend
- [x] UX
- [x] SDET
- [x] DevOps
- [x] Seven lenses: no conflicts
- [x] **Owner approval:** approved by the owner (Drew Curley) on 2026-11-14.

## Build review (2026-11-14)

**Status: PASS.** All 8 roles reviewed the built code over three rounds, run in order with one agent per round, and every item was applied.

- **Round 1:**
  - **Analyst: PASS.**
  - **Architect: PASS.** Optional, not taken: a timeout on the served POST. Nothing waits on it.
  - **Data Engineer: PASS.** Noted: `autoResults` is never pruned, which the 40-a-day budget bounds.
- **Round 2:**
  - **Backend: PASS.**
  - **Frontend: PASS.**
  - **UX: PASS.** Applied: the README sentence moved after the paragraph describing the report.
- **Round 3:**
  - **SDET: PASS WITH ITEMS.**
    - It checked that all 41 mutations are faithful.
    - It added six more (x1 to x6) from its own run. x4, `noteServed` without the token check, survived; it's killed by a new no-token worker check.
  - **DevOps: PASS.** The rollback note goes in the PR.

**Mutations:** 47 of 47 killed, with 0 pattern errors, from a passing baseline. Each mutation ran the tests for its file.

**Suite:** 744 passed, 0 failed, 0 skipped, run locally; the repo has no CI. In one earlier run, the known flaky `test_a_silent_claude_is_killed_at_its_deadline` failed under load and passed 3 times out of 3 on its own.

### Sign-offs (build)
- [x] Analyst
- [x] Architect
- [x] Data Engineer
- [x] Backend
- [x] Frontend
- [x] UX
- [x] SDET
- [x] DevOps
