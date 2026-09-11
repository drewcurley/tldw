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

## Items (non-blocking)

- **Each question is a fresh `claude -p` invocation**, so it re-sends the transcript
  and pays the CLI's own startup context (~20k input tokens before the transcript).
  ~10s per answer. `claude --resume` would keep the context server-side; worth
  measuring before adopting, since it trades statelessness for speed.
- **The browser-driven UI check didn't run this round** — the preview pane refused
  localhost, unlike previous rounds. The pure rendering logic is covered in node;
  the DOM wiring follows the same port/state pattern verified twice already, but it
  has not been exercised in a live page.
- **`background.js` now has four near-identical NDJSON read loops.** Worth one
  shared helper next time one of them changes.
- Very long transcripts still go to the model whole; there's no retrieval step. Fine
  at current lengths, but a 6-hour video would be slow.
