# PLAN — spend less, wait less, keep less

**Status: PLAN — awaiting review. No code written yet.**

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

## The changes

### Spend
1. **Turn prompt caching off for one-shot `claude` calls** (summaries, segment
   selection, anything `--no-session-persistence`). It stays on for Q&A, whose
   follow-ups resume the session and do read the cache. Environment only, not
   argv, so `backend_fingerprint` and every cached summary are unchanged.
   Custom backends are untouched.
2. **Make segment selections durable and stop recomputing them.**
   - Persist to disk next to summaries, keyed by video, segment ratio, a new
     *video*-prompt fingerprint, the backend fingerprint and the transcript
     source.
   - Fix the cached-summary path, which checks under the summary `ratio` but
     prefetches under the segment ratio.
   - Skip the prefetch when a selection is already cached. That covers
     regenerate, which redoes the summary but not the transcript.
3. **Segment prompt:** compact `m:ss` stamps (−8% input), drop the unread
   `reason`, and correct the "lowercase, no punctuation" line, which is false
   for panel and Whisper transcripts. It gets its own fingerprint so stale
   selections miss.
4. **Measure whether key moments are actually used.** Tag segment usage rows as
   `prefetch` or `request`, and record when a selection is actually served to
   the panel, so `tldw usage` can show how many prefetched selections were ever
   played. Prefetch is 54% of spend today, but the right fix (lazy vs keep)
   depends on a number we don't have. After change 1 it's roughly half its
   current cost anyway.

### Speed
5. **Start segment selection alongside the summary, not after it**, whenever the
   transcript is already in hand (page or cache). Key moments become ready
   10–50s sooner at the same cost.
6. **Check caches before reading the page transcript.** Today every click opens
   YouTube's transcript panel (0.5–13s, plus a request to YouTube) before the
   worker or server cache is consulted, including for a summary that's already
   cached. The panel now asks the server first and reads the page only on a
   miss. This is gated on a `/health` feature flag, so a new extension against
   an old server keeps today's behaviour instead of losing the page transcript.
7. **Don't fetch a captionless video's metadata twice.** Accepting the
   transcription offer currently re-runs `yt-dlp -J` (about 4s and a YouTube
   request). Cache it for 30 minutes.
8. **A silent `claude` process can no longer hang a request forever** (the
   audit's one High). A watchdog kills it at the deadline, as `stream_filter`
   already does; stderr is drained; a line arriving after a complete answer no
   longer discards it.
9. **Test suite teardown:** each server test waits ~0.5s in `shutdown()`.
   `poll_interval` cuts the suite from about 50s to about 10s.

### Disk
10. **Tests stop touching your home directory.** A `conftest.py` redirects every
    `Path.home()`-derived path and clears `TLDW_*`. This stops the usage-log
    pollution, and stops a documented setting like `whisper: off` turning the
    suite red.
11. **Remove the 1,572 fixture rows from `usage.jsonl`**, keeping the two real
    summaries of that video and their own video rows. A backup is written
    first.
12. **Transcript cache:**
    - stop storing caption-URL maps (they're only ever used on freshly fetched
      metadata, which was checked)
    - compact existing files
    - prune least-recently-*used* rather than least-recently-written
    - lengthen TTLs to 90 days, since keys already capture everything that
      makes an entry stale and a revisit costs a model call
13. **tldw's `claude` calls run in a tldw-owned directory**
    (`~/.cache/youtube-tldw/claude`). Q&A sessions stop accumulating among
    your Claude Code sessions. Those older than the transcript TTL are pruned
    at startup. (This also closes the audit's SEC-1.)
14. **Voices:**
    - delete files not in the allowlist (63 MB today)
    - cache preview clips on disk, so a preview never needs the model again
    - record when each voice was last used to *speak*, and prune voices unused
      for 30 days (never the most recent, and nothing in the first 30 days
      after the record starts)
    - keep at most two voice models loaded in memory
15. **`tldw cache`** shows what tldw uses on disk, area by area, and reports the
    Hugging Face models with the command to remove them. `tldw cache clean`
    runs the same safe hygiene on demand.
16. **Startup hygiene runs after the port is bound.** Today the audio-temp
    sweep runs before the bind, so a second `tldw serve` deletes the first
    one's in-progress download and then crashes on "address in use".
17. **Repo:** delete the stale `src/youtube_tldr.egg-info` (old package name,
    gitignored) and rebuild the July-stale `extension/dist-firefox`.

## Acceptance

- One-shot calls are launched with `DISABLE_PROMPT_CACHING=1` and Q&A without
  it. **End to end against real `claude`:** usage rows for a summary and a
  segment selection show 0 cache-write tokens.
- A segment selection survives a restart and is not recomputed on reopen,
  regenerate, or a cached-summary hit with a numeric segment ratio.
- A cached summary is served without opening the page's transcript panel;
  against a server without the feature flag, the panel opens as before.
- A `claude` process that goes silent is killed at its deadline, and the slot
  comes back.
- The suite never writes to or reads from the real home directory, and passes
  with `whisper: off` in the real config.
- Cleanup deletes only what the rules above name. Each deletion rule has a test
  that it does **not** touch what it shouldn't: the most recent voice, a voice
  within the grace period, the configured Whisper model, Hugging Face's cache,
  and the user's own Claude sessions.

## Out of scope, deliberately

- Effort, Whisper decoding, TTS bitrate: measured and rejected (above).
- Making the prefetch lazy: wait for change 4's data.
- Forking the summary session for Q&A (PERF-6): it conflicts with change 1 and
  the prize is small.
- PR #8's defects and the audit's non-performance findings.
