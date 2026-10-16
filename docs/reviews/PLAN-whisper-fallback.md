# PLAN — transcribe captionless videos with local Whisper

**Status: PLAN, Round 1 complete — awaiting sign-off. No code written yet.**

Today a video with no caption track ends at a dead end: *"This video has no
subtitles or auto-captions, so there's nothing to summarize. (Try a different
video.)"* The machinery to do better already exists and is only wired to the CLI.

**Round 1 blocked the first draft of this plan, three for three.** The first draft
treated an eight-minute CPU-saturating job as though it were a thirty-second one.
This version is the amended plan; what changed is recorded at the bottom.

## What already exists

`whisper_transcribe.py` is complete and in `main`: `is_available()`,
`require_whisper()`, `resolve_model()`, `download_audio()`, and `transcribe()`
which streams per-segment progress. `cli.py` uses it behind `--whisper-fallback`.
There are **zero** references to it in `server.py`, `core.py` or the extension.

## Measured first (this machine, before designing)

| | result |
|---|---|
| audio download via yt-dlp | **works, no 429** — a different endpoint from captions |
| Whisper `small`, int8, CPU | **5.5× realtime** |
| model on disk | 464MB, one-time, downloaded from Hugging Face on first use |
| accuracy | beat YouTube's ASR on the sample — "Doctrine and Covenants" where the caption track said "Doctrine of Covenants" |

10-min video ≈ 2 min, 30-min ≈ 5½ min, **45-min ≈ 8 min**.

## Decisions taken

- Three-way setting `off` / `ask` / `always`, **defaulting to `ask`**.
- `small` default model via the existing `whisper_model` config key, not exposed in
  the extension Options.

## Acceptance criteria

1. A captionless video with the setting on `ask` shows an offer, not an error,
   carrying a time estimate derived from the video's **trusted** duration
   (`duration_ms × 0.18`, ±30%), and transcribes only when clicked.
2. `always` transcribes without asking. `off` keeps today's exact 422.
3. **Absent** `whisper` field → `ask`. An unrecognized value → 400. (`_model` is the
   precedent: absent returns `None` and falls through, only unknown values 400.)
4. `transcribe: true` is honored only when the effective setting is `ask` or
   `always`. With `off` it is a 422, exactly as today. A server-side config key can
   pin `off` regardless of what any client sends.
5. A video whose trusted duration exceeds the ceiling is refused **before** any
   audio is downloaded, with its own error naming the limit.
6. A completed Whisper transcript is cached and never recomputed for the same video
   *and model*. Changing `whisper_model` produces a new transcript rather than a
   14-day-stale one.
7. Two requests for the same video run **one** transcription; the second attaches to
   it and sees its progress.
8. A transcription completes and is cached even if the client disappears — a
   finished transcript is never thrown away because a socket closed.
9. With the close setting on `continue`, closing the panel leaves the run going and
   re-opening re-attaches. With `abort`, closing stops it. Both are tested.
10. The run can be stopped explicitly, and the stop works **while the server is at
    its concurrency limit**.
11. A run that exceeds its whole-run budget fails with a timeout, not forever.
12. `faster-whisper` missing is detected **before** any audio is downloaded or an
    estimate offered, and shows the install command instead of a time estimate.
13. Audio-download and transcription failures (live stream, age-gated, silent audio,
    no file produced) each report why, leave nothing cached, and leave no temp files.
14. Summaries built from audio say so — **in the panel and in the saved `.md`** —
    including on a summary-cache hit and after a server restart.
15. Whisper cues satisfy the same contract as every other producer: sorted,
    non-overlapping, `end_ms > start_ms`, no runaway repeats.
16. CLI *behavior* is unchanged and covered by its existing tests; the orchestration
    lives in one place both callers use.
17. The end-to-end check runs against the **pipx-installed `tldw serve`**, not the
    project venv.

## Design

**Setting.** Extension Options, sent as `whisper: "off"|"ask"|"always"`, allowlisted
server-side (absent → `ask`, unknown → 400). `transcribe` checked with `is True`,
matching `body.get("refresh") is True`.

**Where the decision lives.** In the **server handler**, not `core`. `core.fetch_transcript`
keeps raising `NoTranscriptError`; the handler decides. (The first draft said both;
this resolves it.)

**Flow.** On `NoTranscriptError`: `off` → 422. `ask` → emit a typed
`needs_transcription` event with `{duration_ms, estimate_s, model, installed}` and
**end the stream cleanly**, releasing the semaphore rather than parking a slot on a
human decision. `always` / `transcribe: true` → start the job.

**Running it.** Not in the request. A background thread with an in-flight registry,
mirroring `_seg_prefetch_events` / `_seg_progress` (server.py:39-46, 140-186):

- Keyed `(video_id, model)`. A second caller waits on the event and reads the
  published progress — criterion 7.
- Writes to `txcache` the moment it completes, regardless of client state —
  criterion 8. This is why it cannot be in-request: `_run_stream`'s `emit` has no
  `OSError` guard (unlike `_run_speak`/`_run_ask`, which set a `gone` event), so a
  closed socket would otherwise discard the whole run.
- Its own `threading.Semaphore(1)` — **at most one transcription ever runs** — and it
  does not hold a general request slot. `MAX_CONCURRENCY = 2` was sized for 30–120s
  model calls; two 8-minute runs would 429 everything else, including the stop.
- `cpu_threads` pinned to about half the cores. Two runs oversubscribing the CPU
  finish slower than running them in series and make the laptop unusable — which
  matters on a daily driver that is often in meetings.
- **Stop bypasses the semaphore entirely** (it does no work) and is keyed by video
  id — criterion 10. A client must be able to stop the thing wedging the server.

**Bounds.** Checked before `download_audio`:
- Duration ceiling from a **trusted** source (`md.fetch_metadata`, plus
  `--match-filter` and a check against the decoded `info.duration`). Request-supplied
  duration drives the cosmetic estimate only — otherwise a client claiming
  `duration_ms: 60000` for a three-hour video bypasses the cap.
- Whole-run budget → `TldrTimeoutError` → 504, which `_STATUS` already maps.
  `transcribe()` currently has no wall clock at all.
- `yt-dlp`: `bestaudio` with explicit failure rather than `bestaudio/best` (which
  falls back to a muxed multi-GB video), `--max-filesize`, reject live
  (`canonical_video_id` accepts `/live/` URLs), `--ignore-config` so a user's yt-dlp
  config can't inject `--exec` into an HTTP-triggered download.
- The `video_id` reaching `download_audio` is always `canonical_video_id` output —
  never `body["url"]`, never anything from `body["page"]`. `workdir.glob(f"{video_id}.*")`
  is injection-free only because of that. It also picks `sorted(...)[0]`, which can
  be a `.part` or fragment file, so the real path comes from
  `--print after_move:filepath`.
- Cue count and total chars capped at construction, as `parse_panel_rows` does.

**Cue quality.** Whisper cues go through `_normalize` like every other producer
(criterion 15) and collapse runs of identical consecutive segments the way
`parse_json3` does. `vad_filter=True` and `condition_on_previous_text=False`, because
a music bed or long silence makes Whisper repeat a sentence for minutes — on exactly
the material captionless videos are made of, and the result would be cached 14 days
and summarized.

**Caching and provenance.**
- The txcache entry gains `source` (`captions` | `audio:<model>`) and is read with
  `raw.get("source") or "captions"` so **every pre-upgrade cache file stays a hit** —
  on a captionless-heavy cache, each spurious miss costs eight minutes.
- `_summary_key` gains a transcript fingerprint. Today it hashes
  `video_id | backend | ratio | PROMPT_FINGERPRINT` and the lookup runs *before* any
  transcript work, so switching `whisper_model` would serve the old summary without
  the transcript layer ever being consulted — the same class of bug the backend
  fingerprint was added to fix. One-time flush of existing summary keys; called out
  rather than discovered.
- Provenance also goes in the **summary payload**, not just the txcache entry:
  criterion 14 must hold on a summary-cache hit, which never touches the transcript.
- Page-supplied cues beat an audio-derived cache entry, so a video that later gains
  real captions stops being shadowed.
- The tuple widening (`txcache.get/put`, `_cache_get`'s `(deadline, *on_disk)` with
  readers indexing `entry[1]`/`entry[2]`, `core.fetch_transcript`'s return) is decided
  deliberately up front, not mid-implementation.

**Metadata for a captionless video.** `_page_transcript` returns `None` outright when
there are no cues, discarding the page's title, channel and duration — so the offer
and the cache entry would have nothing to use. The page payload is split so meta is
accepted without cues.

**Timeouts.** `CLIENT_TIMEOUT_MS = 150000` (background.js:14) aborts the summarize
fetch at 2.5 minutes, which caps transcription at roughly a 14-minute video — the
45-minute example in this very plan would die mid-run after the user accepted an
estimate the client cannot honor. The transcribe path gets its own budget derived
from the estimate. (Server-side `REQUEST_TIMEOUT = 120.0` bounds only the model call,
verified, so it is not in the way.)

**Corrections in scope.** `whisper_transcribe.py:108` tells users transcription takes
"roughly 0.5–2× the video length" against a measured 0.18×, and `_MODEL_SIZES` calls
`small` 244MB against 464MB on disk. Both feed the estimate the user is asked to
accept.

**New egress, stated for the record.** This feature adds two server-side network
paths to a project whose posture is "the server makes no request to YouTube": a
yt-dlp audio download, and a 464MB Hugging Face model download on first use. The
model download happens *before* the first segment, so it is outside the
between-segments cancel check and needs its own progress and bound — otherwise the
first-ever `always` request looks like a hang.

## Verification plan

- Unit: the three setting values plus absent and unknown; `off` > `transcribe:true`
  precedence; the duration ceiling; cue construction, `_normalize` conformance and
  repeat collapsing; cancellation between segments; the whole-run timeout; cache
  write, model-keyed miss, and a **pre-upgrade cache file still reading as a hit**.
- Server: `needs_transcription` shape and clean stream end; in-flight dedupe running
  one job for two callers; completion cached after the client disappears; stop while
  at the concurrency limit; a captioned summarize still served during a transcription.
- JS: offer renders with the estimate; not-installed shows the command; button sends
  `transcribe: true`; progress, stop, and re-attach after re-open.
- End to end against the **pipx-installed** `tldw serve` with real Whisper on a
  genuinely captionless video, timings recorded here.
- Whisper is stubbed in unit tests; the real model runs only in the e2e check.

## Review — Round 1 (Analysis): **BLOCK**, all three

Every finding was checked against the code rather than taken on trust.

- **Analyst — BLOCK.** The client timeout (confirmed, `background.js:14`); "defaults
  to ask" absent from the criteria; criteria 4/5 contradictory for `closeAction:
  "abort"` (confirmed, `content.js:42`); no criteria for Whisper-path failures; the
  estimate had no formula while two code strings contradict it; criterion 8 ambiguous
  in a way that would have duplicated the CLI's orchestration.
  *One finding downgraded:* it argued the `model`-shaped validation would 400 every
  older extension. `_model` returns `None` for absent and only 400s on unknown
  values, so the precedent is already correct — kept as criterion 3 for explicitness,
  not as a back-compat hazard.
- **Architect — BLOCK.** No duration ceiling and the existing 350k-char gate fires
  *after* the CPU is spent; `transcribe: true` could override a user's `off`; no
  timeout on `transcribe()` at all; criteria 4/5 incompatible with in-request work
  given the unguarded `emit`; no in-flight dedupe; `MAX_CONCURRENCY` wrong lever;
  `bestaudio/best` muxed-video fallback, live streams, orphaned ffmpeg,
  `sorted(...)[0]` picking a `.part`; memory and model-download egress uncosted.
- **Data Engineer — BLOCK.** `_summary_key` blind to the transcript; video-id-only
  key conflating two accuracy profiles with no way out of a bad one; provenance with
  no plumbing and the payload path that needs it never touching the transcript;
  pre-upgrade cache files silently invalidated; Whisper cues bypassing `_normalize`
  (confirmed); no repetition guard; coarser cues shifting span and citation
  behaviour; page meta discarded for captionless videos; Whisper unreachable from
  `/ask`.

## Review — Round 2 (Implementation): Backend, Frontend, UX — *pending*
## Review — Round 3 (Verification): SDET, DevOps — *pending*
## Seven lenses — *pending*

## Deferred, with reasons

- **Q&A on a captionless video** is bounded by the transcript cache; once it expires,
  `/ask` 422s. In scope: the error says *that* rather than "this video has no
  subtitles". Out of scope: threading `allow_whisper` through `/ask`.
- **`summarize._PROMPT` says the transcript "may be auto-generated: lowercase, no
  punctuation"** — false for Whisper output. Correcting it moves `PROMPT_FINGERPRINT`
  and flushes the entire summary cache. Deferred as a separate change so this PR's
  cache flush has exactly one cause.
- **`word_timestamps`** would tighten citation seeks; **recording `info.language`**
  is a cheap accuracy signal. Both suggestions, both deferable.
- Streaming audio into Whisper instead of downloading in full; `mlx-whisper` for
  Metal on Apple Silicon (mac-only dependency).
- Deleting the stale June `feat/whisper-fallback` branch — a `chore/`, not this PR.

## Open for sign-off

- **This is no longer a wiring job.** The first draft was wrong about that. Done to
  the project's "no intentional tech debt" standard it is a background-job subsystem
  with its own concurrency, bounds, cancellation and cache semantics. That is the
  honest estimate; the alternative is to cut scope deliberately rather than by
  accident.
- **Merge order:** stacked on PR #7, which should merge first.
- **`pipx inject youtube-tldw faster-whisper`** is required on your side, and
  criterion 17 makes the e2e run depend on it.
