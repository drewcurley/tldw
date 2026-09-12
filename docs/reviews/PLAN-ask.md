# PLAN — follow-up Q&A about a video

**Status: PASS WITH ITEMS**

A chat panel in the modal, once a video has been summarized, that answers questions
about it against the transcript the server already has.

## Decisions taken before building

- **Transcript-first, outside knowledge allowed but labelled.** The model answers
  from the transcript when it covers the question and must prefix anything else
  with "Not in the video:". It reports the video's claim and flags the discrepancy
  when the two disagree.
- **Citations are clickable.** Answers cite `[12:34]`; the client turns those into
  seek links, so an answer can always be checked against the source.
- **Collapsed behind a button** under the summary, so the summary stays the front
  page of a modal that is already long.

## Design

- `POST /ask/stream` — NDJSON: `progress` → `delta` × N → `answer_done`. Same auth,
  CORS, and concurrency slot as every other route.
- **The transcript never leaves the server.** The browser sends only the question
  and the conversation so far, so the server stays stateless and the request stays
  small. History is capped (12 turns, 4k chars each) so the prompt can't grow
  without bound.
- `ask.py` owns the prompt and payload shaping; `core.fetch_transcript()` was
  extracted from the two places that already duplicated it, and now serves three.
- `claude_client._stream_deltas()` is the one piece of streaming subprocess
  plumbing, with `stream_ndjson_segments` and the new `stream_text` on top of it.
  `ask_text` is the buffered fallback for custom `TLDW_LLM_CMD` backends, which
  have no streaming mode.
- **Cache TTL 15 min → 4 hours** (requested), with an entry cap. These caches are
  process memory, not disk, so an all-day server would otherwise accumulate every
  video visited; `_prune` drops expired entries then trims to 64. `_cache_get`
  gained `touch=True` so an active conversation renews its own transcript instead
  of expiring between questions.

## Two real bugs this surfaced

- **The streaming path never worked.** `claude -p --output-format stream-json`
  is rejected outright by the CLI without `--verbose`, and even with it the CLI
  emits one whole `assistant` message unless `--include-partial-messages` is
  passed — so the `content_block_delta` parsing matched nothing. That code has been
  in the tree since segment streaming landed. It survived only because the prefetch
  calls `select_segments` *without* `on_progress`, which takes the batch path;
  `stream_ndjson_segments` has no fallback, so anything reaching it would have
  502'd. Fixed for both callers, with the delta parser handling the current
  `stream_event` wrapper, the older top-level shape, and whole-message CLIs.
- **`/ask` had no working abort.** Silencing writes to a dead socket is not
  cancellation — `claude` kept generating. The delta callback now raises
  `_ClientGone`, which unwinds `stream_text` and closes the generator, killing the
  process. Mutation-checked.

## Verification

- 269 tests pass, 21 new.
- **End-to-end against the real `claude` CLI** with a seeded transcript: citations
  resolve to the right cues (`[5:12]`, `[12:34]`), a follow-up uses the prior turn,
  and an unanswerable question is labelled "Not in the video:". ~4-5s to first
  token, ~10s total.
- The client-disconnect abort is mutation-checked: removing the raise makes the
  test fail.
- `renderSummary`/`renderAnswer` now have real coverage via node
  (`tests/test_extension_render.py`) — markdown, citation offsets, and escaping of
  `<script>`/`<img onerror>`/quotes, plus the tag-split guard proving a timestamp
  inside an attribute is never rewritten. Also mutation-checked.

## Review dimensions

Checked in one pass rather than by eight agents (this session doesn't spawn
subagents) — recorded as such.

- **Analyst / scope** ✅ The feature as specified plus the requested TTL change.
  The `fetch_transcript` extraction and the `_stream_deltas` consolidation are
  in service of it, not extra.
- **Architect / security** ✅ Question and transcript go to the model on stdin,
  never argv. The URL is run through the YouTube allowlist before any work. Body
  capped, question capped, history capped and sanitized (non-user/assistant roles
  are dropped, so a crafted `system` turn can't be injected through history). The
  prompt tells the model the transcript is untrusted content, not instructions.
  Answers are escaped before rendering, with coverage.
- **Data** — n/a, no schema. The cache change is bounded in both time and size.
- **Backend** ✅ Writes serialized under a lock; a client hang-up unwinds the model
  call rather than being swallowed; partial answers are still delivered before an
  error event.
- **Frontend** ✅ Streaming deltas re-render one bubble; an answer in flight
  survives the panel being closed and re-attaches on re-open.
- **UX** ✅ Enter sends, Shift+Enter newlines, the box grows with the question,
  Send becomes ⏹ Stop while answering (matching the audio control), citations are
  underlined and titled, `aria-live` on the log, and keystrokes don't leak to the
  modal's Escape handler.
- **SDET** ✅ New coverage for payload shaping, history trimming and sanitization,
  streaming, the buffered fallback, cache reuse, cache TTL/cap/touch, every
  rejection path, disconnect abort, and the client renderers.
- **DevOps** ✅ No new dependency or config. `node` is optional — its tests skip
  when it's absent.

## Lenses

Product/Middle-management: the summary answers "should I watch this"; this answers
"wait, what did they say about X" without scrubbing. Developer: one new module, one
new route, and a net *reduction* in duplicated subprocess and fetch code. Investor/
CEO/Purchasing: no new vendor, no API key, still local-only. Marketing: it demos in
one line — ask the video a question, click the timestamp, land on the moment.

## Round 2 — typing in the chat drove the video

Spaces typed into the chat box paused and unpaused the video, while letter
shortcuts like `m` did nothing. The asymmetry is the diagnosis:

- Events crossing a shadow boundary are **retargeted**, so the page sees our host
  `<div>` as the target. YouTube's "is the user typing?" check looks at that, sees
  a plain div, and happily applies its shortcuts to our textarea.
- The chat input's own `stopPropagation()` ran in the **target phase**, which is
  late. It killed YouTube's *bubble-phase* handlers — hence `m` going quiet — but
  space and the arrows are scroll keys YouTube grabs in the **capture phase** on
  `document`, which runs before the event ever reaches our input.

Capture order is window → document, so the shield moved to a `window` capture
listener that swallows every key originating inside the modal (keydown, keyup and
keypress). Enter-to-send moved into it, since the input's own handler can no longer
run, and the focus trap now includes the textarea.

`m` still does nothing while the chat is focused. That is the intended end state:
typing a message should not drive the player.

Covered by `tests/test_extension_render.py`: `fromModal` retargeting logic
(composedPath and the `contains` fallback) plus guards that the listener stays on
window/capture and that the input never regrows a handler of its own. Both
mutation-checked — reverting the listener to `document`, or making `fromModal`
always false, fails the suite.

## Round 3 — closing the panel killed the run

Clicking off the modal during a summary killed it. Nothing aborted the request:
`close()` called `clearTimers()`, which clears `pingTimer` — and that 20s ping is
the only thing keeping the MV3 service worker alive. Without it Chrome suspends the
worker mid-fetch and the result never arrives. `background.js` has warned about
exactly this at the top of the file since the beginning.

So "keep going in the background" is not "decline to abort" — it is "keep the port
and its heartbeat connected after the UI unmounts".

- New `closeAction` preference (`continue` default / `abort`), in Options, read once
  into the content script and kept in sync via `storage.onChanged` because `close()`
  has to decide synchronously.
- `close()` splits into a background path (keep every port and ping, unmount only)
  and an abort path (send the explicit `stopSpeak`/`stopAsk` that a bare disconnect
  deliberately doesn't trigger, then tear everything down). `closePolicy()` is a
  pure function so the decision is directly testable.
- A summary finishing while the panel is closed no longer yanks the modal open over
  the video — it's stashed and announced with a clickable notice.
- Re-opening mid-run re-attaches to the in-flight port instead of starting a second
  identical request, and the audio / key-moment controls render in their real state
  rather than as buttons that silently no-op.

Covered by `tests/test_extension_render.py`: the `closePolicy` truth table
(including that a click event's argument isn't mistaken for `keepSeg`) plus guards
that the background path never calls `clearTimers()` or disconnects the port, and
that the abort path does both and sends the explicit stops. Mutation-checked both
ways — reinstating `clearTimers()` on the background path, or ignoring the
preference, fails the suite.

Also fixed the test harness itself: `grab()` validated only its start marker, so a
stale end marker sliced to EOF and surfaced as an unrelated syntax error.

## Round 4 — re-opening mid-run showed a dead 0% spinner

Reported: close the panel during "summarizing with Claude", re-open it, and the
modal sits on "Starting… 0%" forever; the server finishes, nothing updates, but
closing and re-opening once more shows the full summary instantly.

Two independent faults, both mine from round 3.

- **`mount()` called `close()`.** `close()` is where the abort-vs-background
  decision lives, so the re-attach path did `backgrounded = false` and then
  immediately `showLoading()` → `mount()` → `close()` → `backgrounded = true` again.
  The result arrived, took the backgrounded branch, and was stashed silently — which
  is exactly why the *next* open had it ready. `mount()` now calls `unmount()`; a
  new run retires the previous run's port itself, which is the only thing `close()`
  was doing for it.
- **A re-attach reset the progress bar.** `showLoading()` starts at "Starting… 0%",
  and the long Claude step emits ONE progress event (with `creep`) and then goes
  quiet for a minute, so there was nothing to move it off zero. The last progress
  event is now remembered and replayed on re-attach, which restores the message,
  the percentage and the creep animation.

Guarded in `tests/test_extension_render.py`: `mount()`'s prologue must not invoke
the close policy, `startSummarize` must retire the old port itself, and the
re-attach must replay the remembered progress. Mutation-checked — restoring
`close()` in `mount()` fails the suite.

Two things this round says about the harness: a static guard has to strip comments
before matching (the first version tripped on the word `close()` inside the comment
explaining the fix), and scope to a function's prologue rather than its whole body
(`mount()` legitimately *registers* `close` as the backdrop handler).

## Round 5 — the progress bar rewound on re-open

Re-opening during the Claude step showed 16% after the bar had crept to 22%, every
time. 16 is not elapsed seconds: `setProgress(15)` restored the server's last
reported figure for that step, one creep tick added 0.6, and `Math.round(15.6)` is
16. The bar climbs ~1%/s there, which is why it reads like a timer.

- **Carry the crept progress across a re-attach.** Replaying `lastProgress` alone
  restores only what the *server* last said, discarding everything the creep had
  added since. `progressPct` is now carried across `showLoading()`'s reset.
- **Let the creep run while the panel is closed.** It only advances `progressPct`
  and calls `applyWidth()`, which is already a no-op while unmounted, so the bar
  now tracks real elapsed time instead of freezing at the moment of closing.
  `clearTimers()` still retires it on completion or abort.

Harness note: two rounds running, a structural guard matched the very call it
forbids inside the comment explaining the fix. Comment-stripping is now applied to
every structural check rather than bolted onto one, and the close-body checks key
off code landmarks (`stopSpeak`) instead of comment text.

## Round 6 — download the synthesized mp3

A **⬇** next to the player, once a complete clip exists, saving
`{video title} - {channel} - tldw version.mp3`.

- **It can't go inside the control bar.** Native `<audio controls>` draws its own
  shadow UI, so there's no way for page script to place a button beside the volume
  icon. The alternative would be replacing the native controls wholesale, which
  costs their keyboard handling and accessibility for a cosmetic gain. It sits
  immediately alongside the player instead.
- **Only for a finished file.** Mid-stream the player is backed by a MediaSource,
  which isn't a file that can be handed over; the button stays hidden until
  `audio_end` (or a restored/cached clip) provides the complete mp3.
- **Downloads via a Blob**, not the `data:` URL directly — browsers are far more
  willing to download `blob:` from a content script, and the object URL is released
  afterwards.
- `sanitizeField` mirrors `naming.sanitize_field` server-side: control and
  filesystem-illegal characters out, the `" - "` joiner protected so a title can't
  fake a field boundary, whitespace collapsed, length capped, `untitled` fallback.

Tested in node: the filename table (illegal characters, embedded `" - "`, empty and
all-illegal fields, truncation, leading/trailing dots) plus a byte-exact base64
round-trip — verified against a real ffmpeg-produced mp3 as well as a synthetic
payload of NULs, high bytes and newlines, since a decode slip would produce a
corrupt file that still "downloads successfully". Mutation-checked.

## Items (non-blocking)

- **Each question is a fresh `claude -p` invocation**, so it re-sends the transcript
  and pays the CLI's own startup context (~20k input tokens before the transcript).
  ~10s per answer. `claude --resume` would keep the context server-side; worth
  measuring before adopting, since it trades statelessness for speed.
- **No browser-driven verification** — the preview pane refused localhost and
  external navigation, and its policy check never cleared. Rendering, citation
  parsing and the shield's targeting logic are covered in node, but nothing was
  exercised in a live page, and the key shield in particular has not been tested
  against YouTube's actual listeners. If a space still reaches the player, the
  remaining possibility is a YouTube listener on `window` capture registered before
  ours, which no amount of `stopPropagation` can outrun; the fallback would be to
  stop using a shadow root for the chat input so the page's own typing check
  recognizes it.
- **`background.js` now has four near-identical NDJSON read loops.** Worth one
  shared helper next time one of them changes.
- Very long transcripts still go to the model whole; there's no retrieval step. Fine
  at current lengths, but a 6-hour video would be slow.
