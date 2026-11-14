# PLAN — the wrong video's title, channel and transcript after an in-page navigation

**Status: PASS WITH ITEMS. Plan review complete (rev 3); awaiting owner approval.**
- Backend, SDET, Ops, Data Engineer, Architect and Analyst have all signed off.
- The items left over are recorded below as follow-ups or as build-review checks.

- **Branch:** `fix/stale-page-metadata`.
- **Review scope:** a bug fix that touches the cache's shape. That needs Backend, SDET and Ops, plus the Data Engineer (cache schema). The Architect and the Analyst reviewed it too.
- **Gate:** the owner approves the plan before any code.
- **Origin:** this was found while planning auto-summarize (`PLAN-auto-summarize-channels.md`). The Analyst ruled that it ships first, as its own PR.

## The bug (measured 2026-11-13 in a live page, built-in browser, video muted)

YouTube is a single-page app. Two things the extension reads go stale when you click from one video to another.

1. **Metadata.** `pageMeta()` (`content.js:480`) reads `meta[name=title]`, `span[itemprop=author] link[itemprop=name]` and `meta[itemprop=duration]`. YouTube writes these **once per full page load**.
   - Three navigations after loading "Me at the zoo", they still said "Me at the zoo", `jawed` and `PT0M19S`.
   - `meta[itemprop=identifier]` still held the first video's ID. That is how the extension can detect staleness.
2. **Transcript rows.** With YouTube's transcript panel open on video A, clicking to B leaves A's rows in the DOM for about **2.3 s** (722 rows in the first measurement).
   - `collectPageTranscript()` (`content.js:517`) takes rows that are already present to mean "the panel is open", and reads them. So a click in that window sends A's transcript as B's.
3. **The sync moment.** Measured with a MutationObserver on 3 of 3 navigations: at the instant `ytd-watch-metadata[video-id]` changes to B, the title (`#title h1`) and the owner link inside it are already B's, and A's rows are **already gone** (0). So one attribute check is enough to know the page has synced.
4. **The server keeps what it's sent** (verified in code by the Data Engineer and Backend). `_page_transcript` (`server.py:831-833`) believes the page's fields, and the bad values persist in four places:
   - **The transcript cache:** title, channel and `duration_ms` (`txcache.py:61-90`).
   - **The summary cache:** the title, the channel and the summary text, which was written from a prompt carrying the wrong title (`server.py:1904-1921`).
   - **The segment cache** (`server.py:401-405`). A stale 19 s duration clamps every clip end to 19 s (`spans.py:100`) and caps the ratio at `0.4 × 19 s` (`core.py:146`).
   - **`usage.jsonl` `video_ms`.** That's history, and it is left alone.
5. **The cached copy wins.** `_run_stream` uses `_cached_transcript(url) or _page_transcript(...)` (`server.py:1274`), even on ↻. Without invalidation, the bad data keeps coming back for 14 days.
   - `tldw cache clean` deletes only *expired* entries (`cli.py:395-399`).
6. **On this machine today** (Ops, a read-only count):
   - transcripts: 51, all `captions` with no `gen`, 31 of them within their TTL. There are no `audio:*` entries.
   - summaries: 51 (31 within TTL).
   - segments: 31.
   - The one-time cost is at most about 31 summary calls and 31 selections, and only for videos that are opened again.

## The change

### Extension (`content.js`)

1. **`watchMetadataFor(videoId, doc)`.** It returns the `ytd-watch-metadata` element whose `video-id` **attribute equals** `videoId`, or `null`.
   - It compares attributes and never builds a selector from the ID.
   - Any `videoId` that isn't `^[A-Za-z0-9_-]{11}$` gives `null`.
   - It walks every `ytd-watch-metadata`, not just the first.
2. **`pageSync(videoId, doc)`.** Its result is `"synced"`, `"fresh-load"` or `"waiting"`:
   - `"synced"` if `watchMetadataFor(videoId)` exists.
   - `"fresh-load"` if *no* `ytd-watch-metadata[video-id]` exists anywhere, but `meta[itemprop=identifier]` equals `videoId` (Backend W1). On a layout without the element, a full page load is still readable as it is today, with no wait.
   - `"waiting"` otherwise. That covers a missing or invalid `videoId` (SDET B1): it never skips the check.
3. **`collectPageTranscript(videoId, isCurrent)`.**
   - Before **anything** else, including `pageMeta`, the click and the "already open" check, it polls `pageSync` every `PANEL_POLL_MS` (200 ms) for up to `PAGE_SYNC_MS = 5000`.
   - The first check happens before any `sleep`, so a synced page waits 0 ms (Backend W2).
   - On each poll, and again just before the click, it stops if `isCurrent()` is false (Backend S1), so a cancelled run never opens YouTube's panel.
   - If the page never syncs, it returns `{ status: "page-not-synced" }`, with no cues and no click. If it's no longer current, it returns `{ status: "cancelled" }`.
   - Once synced, it does today's work: `pageMeta(doc, videoId)`, then the existing-rows check (safe now: at sync, the old rows are gone), then the click and read.
   - The status includes how long the sync took, for example `"ok-panel (722 cues, synced 1400ms)"` (Ops S3), and the server already logs it (`server.py:1247-1248`).
4. **`pageMeta(doc, videoId)`.**
   - **The tags are fresh** (`meta[itemprop=identifier]` `=== videoId`): read the tags as today, duration included.
   - **Otherwise, from `watchMetadataFor(videoId)`:** take the title from its `#title h1`, as text, and the channel from its `#owner ytd-channel-name a`, as text. Send **no** `duration_ms`. Every lookup is inside that element.
   - **Otherwise:** `{}`.
5. **Every page object** the fixed extension sends carries **`page_v: 2`**. That includes the failure results (`page-not-synced`, `cancelled`, `no-transcript-button`, `panel-empty`, `error-*`).
   - The field is a version number, not a `synced` flag. Backend wanted the marker on every object, so that a current extension's failure isn't logged as "predates". Data didn't want a flag named `synced` on objects that never synced. A version number says only "this extension is new enough", which satisfies both.
   - The failure objects carry no cues, so nothing from them reaches a cache.
6. **`startSummarize`** passes its own `videoId` and an `isCurrent` closure (`requestActive && port === myPort`) into `collectPageTranscript` (`content.js:580`).

### Server

1. **`txcache.META_GENERATION = 2`.**
   - `put()` writes `"gen": META_GENERATION`.
   - `get()` reads `gen` **only for a `captions` entry**, including one whose `source` is missing (Backend S2). It's a hit only when `isinstance(gen, int) and not isinstance(gen, bool) and gen >= META_GENERATION` (Data W2).
   - `audio:*` entries are kept whatever their `gen` is, even a corrupt one, so a corrupt field can never cost a re-transcription.
2. **A gen miss is logged** (Ops W3).
   - `get()` and a new `get_status()` are both built on one internal `_read(video_id) -> (status, entry)`, so the two parsers can't drift (Backend item 4).
   - The status is `"hit" | "missing" | "expired" | "stale-gen" | "unreadable"`.
   - After a miss, `_cache_get` logs `cached transcript for <vid> invalidated (gen < 2)` once per video per process.
   - The set that remembers logged videos sits under `_cache_lock`, and it's reset per test in `tests/conftest.py` `_fresh_server_state`.
3. **Old extensions' page data isn't trusted** (Ops B1, Data W1).
   - `_page_transcript` returns `None` unless `page.get("page_v")` is an int (not a bool) `>= 2`.
   - An extension older than this fix, talking to the new server, therefore gets the server's own fetch: today's fallback, the same as a page-read failure. Its stale metadata can never be written as gen 2.
   - It logs `page transcript ignored: extension predates the sync check` **on every request**, not once per process (Ops S2), because each one is a yt-dlp call that can draw a 429.
   - **It applies only to `/summarize/stream` requests whose `page` is a dict without a valid `page_v`** (Ops, rev 3). A request with no page object at all keeps today's behaviour and logging, and AC7 asserts that.
   - It also emits a `progress` event that old extensions already show: "Your TL;DW extension is out of date. Reload it and this tab." (Ops S1). So the reason is visible in the panel, not only in the log.
   - **The VTT `captions` branch is removed** (`server.py:811-818`, Backend item 2). Only extensions older than the panel change sent `captions`, and those never send `page_v`, so the gate leaves the branch unreachable. It goes, along with its tests (`test_server.py:1144`, `:1148`, `:1227`, `:1233`).
   - The cost: until the extension is reloaded, summaries go through yt-dlp, which risks YouTube's 429.
   - This includes the hackathon fork's extension if it's ever pointed at this server. **Flagged to the owner.**
4. **`_summary_key` and `_seg_key`** add `txcache.META_GENERATION` to what they hash. For segments, the on-disk name `_seg_file` changes with it.
5. **Not changed:**
   - in-memory caches, which start empty in the new process;
   - `usage.jsonl`;
   - field validation;
   - the CLI's behaviour. The README notes that `tldw cache` counts superseded files until their TTL runs out (Data S3).
6. **Known and out of scope, recorded as a follow-up (Backend W5):**
   - an `audio:*` label can be applied to page cues when the configured Whisper model changed and a stale audio file is on disk (`server.py:1311`);
   - it's rare and older than this fix.

### Deploy, rollback, docs (Ops B1, W1)

- **Order:**
  1. `git pull`.
  2. Reload the extension in `chrome://extensions`.
  3. Reload every open YouTube tab.
  4. Restart `tldw serve`.
- **The other way round is safe** but slower (item 3 above): page reads are ignored until the extension is reloaded.
- **Correction to rev 1:** the "reload after pulling" note at `extension/README.md:8` mentions the extension and the tab, not the server. The new note covers all three.
- **Rollback** (Ops, rev 2: stop first, so the restarted old server can't serve a pre-fix file in the gap):
  1. Stop `tldw serve`.
  2. Revert.
  3. **Delete `~/.cache/youtube-tldw/summaries/` and `segments/`.**
  4. Start `tldw serve`.
  - Old code would otherwise serve the pre-fix files again: they are still on disk and within their TTL.
  - Transcripts need nothing, because they're overwritten in place by video ID.
- **README:** a "Fixed" entry with the deploy order, the one-time re-summarize, the rollback step and the temporarily high `tldw cache` count.

## Acceptance criteria

1. **`watchMetadataFor` and `pageMeta`**, in `page_transcript_checks.js`:
   - The fixture has **two** `ytd-watch-metadata` elements, a stale one first (SDET W5). Fresh tags give today's output, duration included.
   - Stale tags give the matching element's title and channel and no `duration_ms`. This is the "Me at the zoo" case, read from the second element.
   - With neither, the result is `{}`.
   - Markup in a channel name comes back as text.
   - An invalid ID with a matching attribute gives `null`.
2. **`pageSync` and `collectPageTranscript`:**
   - `page()` gains a `videoId` option that builds a synced page, and every existing case passes its ID and still passes (SDET B1).
   - **Delayed sync:** the old rows are present and the element is on the old ID. A real `setTimeout` flips the attribute and clears the rows. The result has the **new** title (proving `pageMeta` runs after the wait, SDET W4) and only the new rows.
   - **Never syncs:** the result is `page-not-synced`, `opened === 0`, no cues.
   - **Fresh-load:** no element, a matching identifier, and today's read with no wait.
   - **A missing or invalid `videoId`:** the result is `page-not-synced`.
   - **Already synced:** zero sleeps before the click, counted with a `sleep` wrapper and not by elapsed time (SDET W6).
   - **`isCurrent()` false** during the wait gives `cancelled` with no click.
   - **`isCurrent()` true for the first call, then false** (on a synced page) gives `cancelled` and `opened === 0`. That tests the check just before the click (SDET, rev 2).
   - **Back to the first video after a full load** (SDET, rev 2). The page has `identifier` = A, the element on B's ID, and B's rows. Asking for A must **wait** (not `fresh-load`, because an element exists), then read A's rows once the element flips.
   - **In a layout without the element,** going A→B→A still reads B's rows as A's, as it does today. That's a recorded limit (Backend, rev 2).
   - The harness shrinks `PAGE_SYNC_MS` and asserts the shrink matched (SDET W7).
   - Every page object carries `page_v: 2`, the failure results included, and the status includes the sync time.
3. **`startSummarize`** (`probe_checks.js`), in the superseded order: start A, start B, deliver A's probe miss. The recorded read must be A's ID with an `isCurrent` that is false (SDET W8). The existing cancel and supersede tests (`probe_checks.js:126-150`) still pass.
4. **`txcache`** (`tests/test_server.py`, next to `test_expired_disk_entries_are_ignored`):
   - These are misses:
     - `captions` with no `gen`;
     - `gen: 1`;
     - no `source` and no `gen`;
     - `gen: "x"`;
     - `gen: True` under a monkeypatched `META_GENERATION = 1`, where only the bool guard catches it (SDET B3).
   - These are hits:
     - `gen: 3` (SDET B3);
     - `audio:*` with no `gen`, with `gen: 1`, and with `gen: "x"`.
   - A fresh `put()` round-trips with `gen: 2`.
   - `get_status` returns each of its five values.
   - For every fixture, `get()` returns an entry exactly when `get_status()` is `"hit"`.
   - Looking up the same stale video twice logs the gen-miss line once.
   - The existing tests at `test_server.py:1244` and `:1270`, which use legacy pre-provenance files, are **rewritten deliberately**, with the reason in their docstrings (Backend W4).
5. **The keys:** `_summary_key`, `_seg_key` and `_seg_file(key)` change when `META_GENERATION` is monkeypatched to 3, and stay the same otherwise.
6. **End to end** (copying `test_the_page_can_supply_the_transcript` at `test_server.py:1115` and the `_post_json` pattern at `:2128-2200`):
   - A summary is written with `put_summary` under the **real old key**, rebuilt from the old `"|".join` shape with no generation part.
   - A legacy transcript is written as a raw JSON file on disk, with no `source` or `gen`, and the in-memory cache is left empty (SDET B2).
   - **Then:** `/summarize/cached` misses. `/summarize/stream` with a `page_v: 2` page returns the page's title. `fetch_transcript` is never called. The file on disk now has `gen: 2` and the right title.
   - The `invalidated (gen < 2)` log line appears.
7. **Old extension** (in `test_server.py`, next to `test_the_page_can_supply_the_transcript`):
   - A page with no `page_v`, with `page_v: "2"`, with `page_v: True` or with `page_v: 1` makes `_page_transcript` return `None`.
   - The server then fetches. The "predates" line is logged on each request, and the out-of-date `progress` event is emitted.
   - A page with `page_v: 2` is used.
   - A request with **no** `page` object gets neither the out-of-date event nor the "predates" line.
   - A `page_v: 2` page carrying only `captions` and no `cues` returns `None`, which confirms the VTT branch is gone (SDET).
   - **The existing tests that post page objects without the marker are updated deliberately to send `page_v: 2`** (Backend and SDET, rev 2): `test_server.py:1127, 1164, 1170, 1196, 1202, 1212, 1222, 2464` and `_preferred_transcript`'s test at `:1856`. The VTT tests go with their branch.
8. **Mutations.** Each of the 22 mutations below must fail a test. They're run on a scratch copy and recorded in the PR.
   - **The SDET's list:**
     1. `watchMetadataFor` takes the first element.
     2. The ID check is removed.
     3. The identifier check is removed.
     4. Live text is read from `document`.
     5. The duration is kept on the live path.
     6. The sync wait is removed.
     7. The click happens before the sync.
     8. `pageMeta` runs before the wait.
     9. `startSummarize` passes `currentVideoId` or nothing.
     10. `put()` doesn't write `gen`.
     11. `get()` drops the `captions` check.
     12. `get()` drops the `gen` check.
     13. `>=` becomes `==`.
     14. The generation is left out of `_summary_key`.
     15. The generation is left out of `_seg_key`.
     16. The sync loop is moved outside the `try`.
   - **Added in rev 3** (SDET, rev 2):
     17. A page without `page_v` is trusted.
     18. `page_v` is checked for truthiness instead of an int `>= 2`.
     19. The `isCurrent` check inside the wait loop is removed.
     20. The `isCurrent` check just before the click is removed.
     21. `fresh-load` ignores whether an element exists.
     22. The gen-miss line is logged on every lookup instead of once.
   - The full suite passes, run locally because the repo has no CI. The result is recorded in the PR.
9. **README:** as in "Deploy, rollback, docs".

## Answered questions

- **F1 (5 s):** keep it. It's about twice the measured lag, and a timeout fails towards a server fetch, not towards stale data. Logging the sync time lets it be tuned later (Backend, Ops, Architect, Data).
- **F2 (`audio:*` kept):** agreed. Their metadata came from yt-dlp. Backend W5 is the one exception, and it's a follow-up.
- **F3 (no live duration):** agreed. During an ad, the player shows the ad's length.

## Recorded follow-ups (not in this PR)

- **Backend W5:** the `audio:*` mislabel described above.
- **`_preferred_transcript`** (`server.py:837`) is dead in production. Only one test calls it.

## For the owner

- **Old extensions get no page reads.** Any extension older than this fix, including the hackathon fork's if it's ever pointed at this server, falls back to the server's own fetch, which risks YouTube's 429, until it's updated.
- **A one-time re-summarize.** About 31 cached videos get re-summarized, once each, and only if they're opened again.

## Review log

- **Rev 1:**
  - **Backend: PASS WITH ITEMS.** W1: a layout without the element. W2: zero latency when already synced. W3: re-measure the sync moment (done, 3 of 3). W4: rewrite the legacy tests. W5: an audio mislabel, which is a follow-up. S1: `isCurrent`. S2: `gen` only for `captions`.
  - **SDET: BLOCK.** B1: fixtures and a missing ID. B2: the real old key and a legacy file. B3: four `gen` cases. W4–W10. The mutation list.
  - **Ops: BLOCK.** B1: version skew between server and extension, closed in code by the `synced` marker plus the documented order. W1: the rollback step. W3: a log line for gen misses. W4: assert the log line. S1: the marker. S3: log the sync time.
  - **Data Engineer: PASS WITH ITEMS.** W1: the marker. W2: strict `gen` validation. S3: the CLI count note. S4: reloading the extension.
  - **Architect: PASS.**
  - **Analyst:** the server change is in scope for this fix.
- **Rev 2:** all of the above addressed. It goes back to Backend, SDET, Ops and Data to confirm.
- **Rev 2 confirmations:**
  - **Backend: PASS WITH ITEMS.**
    1. 12 tests post page objects without the marker.
    2. The VTT branch becomes dead.
    3. The marker must be on every object, and `synced` is the wrong name.
    4. One parser for `get` and `get_status`.
    5. No other route takes page data.
  - **SDET: PASS WITH ITEMS.**
    1. The back-navigation `fresh-load` case.
    2. The `isCurrent` check before the click.
    3. AC7's location, the truthy cases, and the existing tests.
    4. The log appears once, the logged set is reset per test, and `get` and `get_status` agree.
    - The mutation list is now 22.
  - **Ops: PASS WITH ITEMS.** The rollback order (stop first). S1: the out-of-date `progress` event. S2: log "predates" every time.
  - **Data: PASS.** S1: don't call the field `synced` on objects that didn't sync.
  - **Conflict,** Backend (the marker on every object) against Data (no `synced` on failures): resolved with `page_v: 2`, a version, not a sync claim. That satisfies both. Data wins on schema, and Backend's concern about the log is met.
- **Rev 3:** all addressed. Every reviewer has passed, so the plan review is complete and awaits the owner.
- **Ops on rev 3: PASS.** Nit: emit the out-of-date event only for a page object without a valid `page_v`, and not for requests that have no page. Applied.

## Sign-offs (plan)

- [x] Backend: PASS (rev 3)
- [x] SDET: PASS (rev 3)
- [x] Ops: PASS (rev 3)
- [x] Data Engineer: PASS (rev 2, with items applied in rev 3)
- [x] Architect: PASS (rev 1)
- [x] Analyst: in scope (rev 1, confirmed on rev 2)
- [ ] Frontend, UX: not required for a bug fix (`CLAUDE.md` review scope)
- [x] **Owner approval:** approved by the owner (Drew Curley) on 2026-11-14, as "Approve both", alongside the feature plan.

## Build review (2026-11-14)

**Status: PASS.** Backend, SDET, Ops and the Data Engineer all signed off on the built code.

- **Backend:**
  - It blocked once: a cache file that isn't valid UTF-8 raised instead of being a miss (`UnicodeDecodeError` is a `ValueError`, not an `OSError`). Fixed in `txcache._read`, with `test_a_file_that_is_not_utf8_is_a_miss_not_an_error`. Re-checked: PASS.
  - It confirmed that deleting `parse_captions` and `parse_json3` was right. They became unreachable once the VTT branch went, which is a direct result of the approved plan and not new scope.
  - It confirmed that the two timing tests which failed once under load can't be affected by this change (`test_a_silent_claude_*`). They're existing flakes.
- **SDET: PASS WITH ITEMS, all applied:**
  - fresh-load asserts there's no wait;
  - the end-to-end probe must be exactly `{"type": "miss"}`;
  - a current extension is never told it's out of date;
  - `probe_checks.js` reports `ok <n> checks`, with a floor of 41 (`page_transcript_checks.js` has a floor of 68);
  - the mutation script stops if the baseline fails, or if the scratch copy isn't what gets imported.
- **Ops: PASS.** It suggested adding a reload step to the README's rollback section, which is applied. Three suggestions about log wording (the "ignored" line when the cache served the request, the progress event being overwritten, the sync time on failures) are left as follow-ups; Ops judged them acceptable as they are.
- **Data Engineer: PASS.** It suggested recording that mutation 14 fails the end-to-end test by itself; the SDET verified this on its own scratch copy.

**Mutations: 23 of 23 killed,** against a scratch copy whose baseline passes and which is the copy actually imported. That's the plan's 22 plus 9b ("passes nothing").
- Mutation 9 (`currentVideoId`) survived the first run. That run exposed a missing test for the order "B starts before A's probe answer arrives". The test was added and the mutation is now killed.

**Suite:** 695 passed, 0 failed, 0 skipped (it was 675 before this change). The repo has no CI, so the suite was run locally.

### Sign-offs (build)
- [x] Backend
- [x] SDET
- [x] Ops
- [x] Data Engineer
- [ ] Frontend, UX: not required for a bug fix
