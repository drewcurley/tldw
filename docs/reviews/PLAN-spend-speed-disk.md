# PLAN — spend less, wait less, keep less

**Status: PLAN — reviewed; both reviewers' blockers cleared. Building.**

Brief: "do whatever you think is best to improve speed, reduce token spend, and
reduce disk clutter." Scope is those three things. Correctness findings from the
2026-10-26 audit that don't serve them are deliberately left out, including the
PR #8 defects; they need their own pass.

Stacked on `feat/transcribe-captionless-videos` (PR #8), because that is the code
the daily server runs and this touches the same files.

## Measured first

Everything below was measured on this machine before it was planned. Where a
lever was measured and rejected, that's recorded too, since "we tried it" is
worth as much as "we did it".

**Spend (usage log, Sep 11 – Oct 25, real calls only):** about $30 across about
53 videos. Segment selection is 54%, summaries 44%, Q&A 2%. 95% of input tokens
are billed as cache writes and only 5% are ever read back.

**Segment selection, live, real 32-min transcript, exact tldw flags** (two runs
per configuration, so noise is visible):

| configuration | $/call | sec | output tok | kept | coherent boundaries |
|---|---|---|---|---|---|
| today: prompt caching on, default effort | $0.122 | 12.2 | 964 | 29% | — |
| caching **off**, current prompt, default effort | **$0.070** | 11.4 | 910 | 32% | 76% |
| caching off, new prompt, default effort | $0.068 | 14.2 | 1,042 | 28% | 83% |
| caching off, new prompt, **low effort** | $0.054 | 7.2 | 316 | 23% | **67%** |

"Coherent boundaries" is the share of clip starts and ends that fall on a
sentence boundary. It's the prompt's own "most important rule", and it can be
checked objectively on punctuated transcripts.

**Conclusions:**
- **Caching off: −43%, with no effect on output.** The CLI (2.1.309) defines
  `DISABLE_PROMPT_CACHING`, and the live run showed cache writes going to 0.
  One-shot calls write a cache that nothing ever reads, at 2× the input rate.
- **Effort: rejected.** It is 23% cheaper and 4s faster, but clip coherence
  drops (67% vs 76–83%) and the cuts get harsher. That is the quality the
  project cares most about.
- **New segment prompt: cost-neutral.** Dropping the unused `reason` didn't
  reduce output, because thinking dominates. It's kept because it corrects the
  stale "lowercase, no punctuation" line and coherence held.

**Whisper, local, 3 minutes of real speech:**

| setting | realtime | "Doctrine and Covenants" | agreement with beam 5 |
|---|---|---|---|
| beam 5 (today) | 5.7× | correct | — |
| beam 3 | 6.8× | **wrong** | 0.964 |
| beam 2 | 7.5× | **wrong** | 0.965 |
| greedy | 10.7× | **wrong** ("darktinted") | 0.946 |
| batched pipeline | 14.9× | — | 0.887, and 8 segments for 3 min |

**Rejected.** Every faster setting gets the proper noun wrong, and that is
exactly what a summary repeats. The batched pipeline produces 22-second
segments, which would wreck citations and clip boundaries.

**TTS encoding: rejected.** Current VBR is already 71 kbps for 22 kHz mono.
48 kbps saves 32%, not the audit's 2–3×, and the quality cost is unheard.

**`claude --resume` from a dedicated working directory: works**, both from that
directory and from anywhere else (verified live).

**Disk (tldw-owned):**
- voices 520 MB (7 files; `en_US-ryan-medium` 63 MB is not in the allowlist and
  can never be used)
- Whisper 464 MB
- transcripts 4.8 MB (caption-URL maps make the largest file 579 KB against a
  32 KB median)
- summaries 0.7 MB
- usage log: 1,572 of its 1,760 rows are test fixtures

**Not tldw-owned:** 4.8 GB of faster-whisper models in
`~/.cache/huggingface/hub`, all downloaded 2026-07-20. That's after tldw began
using its own model directory, and nothing else on the machine uses
faster-whisper. They are almost certainly leftovers from a one-off model
comparison. They're **reported, not deleted**: they aren't ours, and being
wrong costs a 4.8 GB re-download.

## The changes (amended after plan review: Architect/Data and Backend/SDET both BLOCKED)

Both reviews blocked the first draft, and both pointed the same way: where an
item is risky, make it smaller. One item is dropped outright and several are
simplified. What changed and why is recorded under *Plan review* at the end.

### Spend
1. **Prompt caching off for one-shot `claude` calls.**
   - Passed per call through `env=`, never by mutating `os.environ` (the
     server is threaded).
   - Tied to `persist=False`.
   - Q&A *removes* any inherited value, so its cache is guaranteed.
   - `proc.run` gains an `env` parameter.
   - Custom backends are untouched. `backend_fingerprint` is unchanged, so no
     cached summary is lost.
2. **Durable segment selections, never computed twice.**
   - One full key — video, segment ratio, segment fingerprint, backend
     fingerprint, transcript source — shared by the disk cache *and* the three
     in-memory maps (`_seg_cache`, `_seg_prefetch_events`, `_seg_progress`).
     Today those maps key on (video, ratio) and ignore the model.
   - The key is built once, in the request thread under the request's model
     scope, and passed in explicitly. It is never re-derived after `use_model`
     has closed, and the source is that of the cues actually used.
   - The "already cached?" check lives inside `_start_seg_prefetch`, under
     `_cache_lock`, together with the existing in-flight check.
   - Stored in its own directory, so selections can't push summaries out of
     the 500-entry cap.
   - A request with `max_length` neither reads nor writes the shared key.
   - The cached-summary path checks under the *segment* ratio, fixing today's
     mismatch.
3. **Segment prompt and listing.**
   - Stamps use `timing.format_clock` (`m:ss`, or `h:mm:ss` past an hour).
     Both the single-pass and the chunked listing build through the same
     function.
   - The unread `reason` is dropped, and the false "lowercase, no punctuation"
     line is corrected.
   - The *segment fingerprint* hashes all three video prompts, a version string
     for the listing format, and the span-shaping constants (`MIN_CLIP_MS`,
     `BOUNDARY_PAD_MS`). A change to any of them misses cleanly.
4. **Measure whether key moments are used.**
   - Segment usage rows are tagged `prefetch` or `request`.
   - A zero-cost `segments_served` row is written on all three serve paths
     (cache hit, prefetch waiter, live stream).
   - `tldw usage` excludes it from model-call counts, reports "prefetched N,
     played M", and treats old untagged rows as unknown.

### Speed
5. ~~Start segment selection alongside the summary~~ — **dropped.** It would
   usually *double* the selection call. It would also pay for selections on
   videos the summary then refuses, and for summaries that get abandoned, all
   on a step that is already 54% of spend. Revisit once item 4 shows whether
   key moments are played.
6. **Cache-first check before reading the page, on its own endpoint.**
   - New read-only `POST /summarize/cached` checks the summary cache with the
     same key the real request uses, `_summary_key(vid, ratio,
     _effective_source(vid))`. It never fetches anything. It streams the cached
     result, or a single `{"type": "miss"}`.
   - Before `collectPageTranscript`, the panel asks the worker. The worker
     checks its own cache, then the endpoint.
   - Hit: delivered through the existing `result` handling. Miss, a 404 from an
     older server, or any error: the *existing* summarize flow runs exactly as
     it does today.
   - Skipped for refresh and for an accepted transcription.
   - `/summarize/stream` is unchanged, so old extensions are unaffected and an
     older server fails closed.
   - No token: reported straight away, instead of after the page read.
   - Goes through `_guarded` (token, body limit, model scope) and the route
     list. Without the model scope, `backend_fingerprint` makes every call miss.
   - A hit carries `stats` like the stream does.
   - A hit starts the key-moments prefetch exactly as today's cached-summary
     path does. Item 3's new fingerprint means existing summaries have no
     matching selection yet.
7. **Captionless metadata cached for 30 minutes**, so accepting the
   transcription offer doesn't run `yt-dlp -J` again. Failures are not cached.
8. **Watchdog on the `claude` stream.**
   - A timer and EOF share a lock. At EOF the stream is marked drained and the
     timer cancelled.
   - The timer kills only if the stream isn't drained, and sets `timed_out`.
   - After the loop: `timed_out` → `TldrTimeoutError` (504, not 502). A
     `result` event already seen → success, whatever exit code our own kill
     produced.
   - The deadline check before line parsing goes (it threw away complete
     answers).
   - stderr is drained on a thread into a bounded buffer. Today >64 KB of
     stderr can block the child, which is a second way to hang forever.
   - Precedence: a `result` seen wins over `timed_out`, and the read loop
     **stops at the `result` event**. A CLI that hangs after answering no longer
     holds the answer until the deadline.
   - The timer factory is injectable, so the race test is deterministic.
   - A resumed Q&A that *timed out* no longer retries.
9. **Test teardown:** `poll_interval=0.05` in the `srv` fixture (~37s of ~50s
   is teardown today).

### Disk
10. **Test isolation lands first.** An autouse `conftest.py` redirects:
    - every home-derived module constant (`CONFIG_FILE`, `TOKEN_FILE`,
      `CACHE_DIR`, `SUMMARY_DIR`, the new segment dir, whisper `_CACHE_DIR`,
      `VOICE_DIR`, `USAGE_FILE`, `DEFAULT_OUTPUT`, the claude working dir)
    - `tempfile.tempdir`
    - `HOME`/`USERPROFILE` for subprocesses

    It also clears `TLDW_*` per test, and resets every server registry
    (`_seg_prefetch_events`, `_seg_progress` and `_tx_jobs` included). Two
    guard tests: one fails if `src/` gains a home-derived constant the
    conftest doesn't cover; one makes a real `claude`/`yt-dlp` launch fail
    loudly.
11. **Fixture rows removed from `usage.jsonl`: a one-time, manual operation, not
    shipped code.**
    - Rule: `kind == "video"`, `video_id == "dQw4w9WgXcQ"`, and `video_ms` one of
      the four test durations (600,000, 1,000, 4,000, 10,000).
    - Counted from the log, not from either review: those durations give
      **1,570** rows. The other two rows for that id have `video_ms` 213,000.
      That is the real video's length (3:33), and each sits beside one of the
      two real summaries.
    - The audit's "1,572" counted those two real rows. A dry run that must equal
      1,572 would have aborted, and a looser rule would have deleted them.
    - The dry run must equal **1,570** or it aborts.
    - The backup is owner-only and never overwritten; the replace is atomic.
    - Only with no server running, and only after item 10 has merged.

12. **The transcript cache stops storing caption-URL maps.** They are only ever
    used on freshly fetched metadata. No compaction, TTL or prune changes:
    existing files age out on today's 14-day TTL.
13. **tldw's `claude` CLI calls run in `~/.cache/youtube-tldw/claude`** (0700,
    created before every call; the claude CLI only — custom backends keep
    today's cwd). Q&A sessions stop landing among your Claude Code sessions.
    Nothing is pruned: tldw Q&A sessions are a few hundred KB, and every rule
    for finding them safely was more risk than the disk at stake. Existing
    sessions in the old location are left alone.
14. **Voices.**
    - Files that aren't in the allowlist are removed. Exact `<stem>.onnx` and
      `<stem>.onnx.json` names only.
    - A voice's model file is touched when it is used to *speak*, not to
      preview.
    - `tldw cache clean` removes voices not spoken in 30 days, never the
      default and never the most recently spoken.
    - **Grace period:** a marker records when tracking began. Nothing is pruned
      until tracking has run 30 days, because until then a file's timestamp is
      its download date, not its last use.
    - Pruning one costs at most a re-download if you pick it again, never data.
    - At most two voice models are kept loaded in memory.
15. **`tldw cache`** reports tldw's disk use by area. It also lists Hugging
    Face's faster-whisper models (honouring `HF_HOME`/`HF_HUB_CACHE`), each
    with its own removal command, and never deletes them. **`tldw cache
    clean`** removes:
    - expired entries
    - stale voices (item 14)
    - audio temp directories older than any possible transcription

    It **never** touches the Whisper model directory. Every deletion function
    takes its root as an argument and refuses to act outside the expected base.
16. **Startup hygiene runs after bind, before `serve_forever`, and is
    age-based.** Only temp directories older than any possible transcription
    are swept, so a second server on another port can't delete a live job's
    download. Non-allowlisted voices are removed.
17. **Local chores (gitignored, not in the PR):** delete the stale
    `src/youtube_tldr.egg-info`; rebuild `extension/dist-firefox`.

## Acceptance

- Item 1:
  - The env passed to the subprocess is asserted for one-shot, Q&A and custom
    backends.
  - **Live:** summary and segment usage rows show 0 cache-write tokens.
  - **Live canary:** a `CLAUDE.md` in the new working dir doesn't reach the
    model, and input tokens are unchanged.
- Item 2:
  - Reopen, regenerate, restart, and a cached summary with a numeric segment
    ratio each make zero selection calls.
  - A prefetch under model A doesn't satisfy, or block, a request under
    model B.
- Item 6:
  - A cached summary is delivered without a page read, driven through the real
    `startSummarize` with a fake port.
  - A miss or 404 runs today's flow unchanged.
  - Plus a check in a real browser, with video muted.
- Item 8: tests (a)–(e) from the review:
  - (a) silent child → 504 and the child is dead
  - (b) 1 MB stderr flood completes
  - (c) result then silence succeeds
  - (d) watchdog fired by hand after EOF → no kill, no error
  - (e) server-level 504, then the slot comes back
- Item 10: the guard tests pass, and the suite passes with the real config set
  to `whisper: off`.
- Items 14–16: every deletion rule has a test proving it does **not** touch
  what it shouldn't:
  - the default voice and the most recently spoken voice
  - the Whisper model directory
  - Hugging Face's cache
  - a fresh temp directory
  - anything outside the expected base

## Out of scope, deliberately

- Effort, Whisper decoding, TTS bitrate: measured and rejected (above).
- Making the prefetch lazy: wait for change 4's data.
- Forking the summary session for Q&A (PERF-6): it conflicts with change 1 and
  the prize is small.
- PR #8's defects and the audit's non-performance findings.

## Plan review

**Architect/Data — BLOCK**, three blockers, all accepted:
- The fixture-row rule matched the real video rows too (item 11: now
  signature-matched, with a must-equal dry run, and manual).
- The segment key's backend fingerprint would have been computed outside the
  request's model scope, so every request would miss (item 2: built once in the
  request thread).
- The `/health` flag failed open across an editable-install branch switch
  (item 6: own endpoint, 404 fails closed).

Warnings taken:
- `max_length` must not poison the shared key.
- The fingerprint must cover the listing format and span constants.
- Dropped the TTL extension, compaction and LRU (staleness and concurrency risk
  for ~4 MB).
- Age-based temp sweep.
- Claude CLI only for the cwd.
- Deletion functions are base-checked.

**Backend/SDET — BLOCK**, four blockers, all accepted:
- The parallel prefetch would usually run selection twice, because the
  post-summary prefetch never checks the cache (item 5 dropped; the check moves
  inside `_start_seg_prefetch`, keyed on the full key).
- The early start could pay for selections on oversize videos (moot with
  item 5 dropped).
- The watchdog as first specified would raise 502 instead of 504 and bring back
  the kill race fixed in bc22a4f (item 8 redesigned).
- `need_page` on the existing stream would break old extensions (item 6 moved
  to its own endpoint, so the existing stream is unchanged).

Warnings taken:
- The full list of home-derived constants, `tempfile.tempdir`, and resetting
  the registries in tests.
- A guard against a real `claude`/`yt-dlp` launch in tests.
- Selections in their own directory.
- The chunked listing path.
- `tldw usage` must not count served rows as calls.
- No retry on a timed-out resumed Q&A.

**Confirmation round:** Architect/Data — all three blockers CLEARED.
Backend/SDET — PASS WITH ITEMS, all four blockers CLEARED. Their build notes are
folded in above:
- `/summarize/cached` uses auth and model scope, carries `stats`, and starts the
  prefetch on a hit.
- Watchdog precedence: a `result` ends the loop.
- The voice grace period is restored.
