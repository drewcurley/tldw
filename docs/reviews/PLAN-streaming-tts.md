# PLAN — streaming TTS playback + voice preview

**Status: PASS WITH ITEMS**

Two changes to the "Listen to summary" path:

1. **Streaming synthesis.** Speech used to be fully synthesized, assembled into a WAV,
   encoded to mp3, base64'd whole, and only then handed to the page — nothing was
   playable until the last step. Now Piper's per-sentence PCM is piped into a
   long-lived ffmpeg encoder and the mp3 is delivered in blocks as it is produced.
2. **Voice preview.** A ▶ button next to both voice pickers (options page and the
   in-page picker) plays a short sample of the selected voice.

## Measurements (this machine, Amy voice, 71s spoken summary)

| | before | after |
|---|---|---|
| first playable audio | 2.5s (whole clip) | **0.83s** |
| full clip delivered | 2.5s | 2.5s |
| voice preview (cold / cached) | — | 1.15s / instant |

Piper runs ~25× realtime on a short script and ~7× on a long one (a real 13-minute
spoken summary took 107s), so synthesis stays well ahead of playback either way —
the buffer never starves in practice.

## Design

- `proc.stream_filter(argv, feed, timeout=)` — new streaming sibling of `proc.run`,
  keeping the single-subprocess-chokepoint rule. Feed thread writes stdin (flushing
  every write), reader thread drains stderr, generator yields stdout as it appears,
  watchdog timer kills the process on timeout.
- `audio.stream_speech(text, voice)` — yields mp3 blocks. One continuous bitstream
  (not per-sentence mp3s), so there are no encoder gaps at sentence boundaries and
  the blocks concatenate into a normal file. First block is 4KB (fast start), the
  rest 32KB (ffmpeg emits ~200-byte frames — too chatty to send one per line).
  `synthesize_speech` is now the batch form of this, used by the CLI.
- `/speak/stream` emits `audio_chunk` × N then `audio_end`, gated on an opt-in
  `stream_audio` flag in the request body.
- `POST /preview {voice}` → `audio/mpeg` of a fixed line, memoized per voice.
- Extension: content script feeds blocks into a `MediaSource`/`SourceBuffer`
  (`audio/mpeg`) and autoplays on the first append; background reassembles the
  blocks for the existing session cache and re-open restore.

## Verification

- 246 automated tests pass (`pytest`), 16 new.
- `stream_filter` is tested against a real subprocess, including that output is
  yielded *while the feed is still blocked* — that test caught a genuine bug
  (unflushed `stdin.write` buffered everything until close, which would have
  defeated the whole feature).
- End-to-end through the real server with real Piper + ffmpeg: chunks arrive
  incrementally, the reassembled bytes probe as a valid 70.8s mp3, preview is
  memoized after the first call.
- MSE assumption verified in a real browser: `audio/mpeg` is supported, arbitrary
  byte slices of one mp3 append cleanly, playback starts on the first append, and
  duration resolves correctly after `endOfStream()`.

## Review dimensions

Checked in a single pass rather than by eight separate agents (this session was
asked not to spawn subagents) — recorded honestly as such.

- **Analyst / scope** ✅ Two requested changes, nothing else. `synthesize_speech`
  lost its now-meaningless `workdir` parameter; both call sites updated.
- **Architect / security** ✅ `/preview` is authenticated, allowlists the voice
  before anything runs, body-capped, constant content-type, CORS unchanged. No
  user data reaches argv — ffmpeg's arguments are all server-side constants and the
  sample rate is cast to `int`. Streaming does not widen the request surface.
- **Data** — n/a, no schema or query changes.
- **Backend** ✅ Progress fires on the synthesis thread while blocks are yielded on
  the request thread, so NDJSON writes are serialized under a lock — without it two
  threads could interleave a line. Mid-stream failures still emit a typed `error`
  event after partial audio.
- **Frontend** ✅ `MediaSource.isTypeSupported` gates the whole path; the page
  declares support in the request so a non-MSE browser gets exactly the old
  buffered behavior with no wasted transfer.
- **UX** ✅ Playback autostarts (the Listen click is the gesture it belongs to) and
  falls back to visible controls if the browser blocks it. Progress keeps ticking
  while playing. The preview button parks itself during a generation so it can't
  contend for a server slot.
- **SDET** ✅ Every new path has an automated test: chunk ordering, opt-out
  back-compat, mid-stream failure, preview memoization, unknown voice, missing
  token, timeout, feed errors, coalescing.
- **DevOps** ✅ No new dependency, no config, no deployment change. ffmpeg and
  Piper were already required for this feature.

## Lenses (abbreviated — a latency fix, not a strategic decision)

Product, Middle Management, and Marketing all point the same way: the feature
already existed and simply felt slow; this is the cheapest possible win. Developer
lens: one new subprocess primitive, tested, in the file that already owns
subprocesses. Investor/CEO/Purchasing: no cost, vendor, or posture change — still
local-only, still no API keys.

## Round 2 — Stop mid-synthesis

Streaming made the first second instant but left a 13-minute summary synthesizing
for ~107s with no way out: the voice picker was locked the whole time, so a
wrong-sounding voice had to be waited out.

- **Third button state.** The Listen button is now idle / **⏹ Stop** / Regenerate,
  owned by one `setListenState()`. It had to become one function: `renderAudio`
  used to stamp "Regenerate" onto the button at the first streamed block, i.e.
  while generation was still running.
- **Stop is a real abort, not a UI reset.** The page disconnects the port, the
  worker aborts the fetch on `port.onDisconnect`, the server's next write fails,
  and `speech.close()` unwinds into `stream_filter`, which kills ffmpeg and
  unblocks Piper. Measured: ffmpeg gone 0.18s after hang-up, both concurrency
  slots reacquirable, no leaked threads. Without the abort the server happily
  finished the clip and held its slot.
- **Stopping a Regenerate restores the previous clip** rather than leaving an
  empty slot; a mid-stream error now does the same instead of stranding a partial
  player.
- **Timeout headroom.** A 13-minute read is ~107s of Piper against a 120s
  `SPEAK_TIMEOUT` — 89% of the budget. Raised to 600s server-side (and the
  extension's fetch cap to match). The old cap was sized for a request the client
  waited out; now the client hears audio immediately and can Stop at will, so a
  generous ceiling costs nothing. A test pins the constant.

Verification: `pytest` 248 tests. The disconnect test was mutation-checked — with
the abort removed it fails (the server ran the full clip). The client state machine
was driven in a browser against the **real** `content.js` with stubbed extension
APIs: Listen→Stop→re-pick voice→restart, finish→Regenerate, Stop-a-Regenerate,
and error-mid-stream all verified, including that the button still reads Stop while
a streamed clip is playing.

## Items (non-blocking)

- **Firefox gets no streaming.** Its MSE has no `audio/mpeg`, so it keeps today's
  behavior. Closing that gap would mean muxing to fragmented MP4 (AAC), a real
  encoder change; not worth it until someone actually runs the Firefox build.
- **Seeking mid-stream** is limited to what has buffered so far. Resolves itself
  once `endOfStream()` lands.
- **First preview of an undownloaded voice** pays the ~60MB model download. The
  button shows a pending state; there is no progress bar for it.
- **"Play key moments" has the same leak** the Stop button fixed for audio: its
  worker fetch isn't aborted when the port disconnects. Left alone as out of scope.
- **No JS test harness in the repo**, so the client state machine is verified by
  driving the real file in a browser rather than by a committed test.
