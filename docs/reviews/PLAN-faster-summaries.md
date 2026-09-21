# PLAN — faster summaries in the extension

**Status: PASS WITH ITEMS**

Three approved changes, plus one requested mid-build:

1. **Lean `claude` invocation** for every model call.
2. **Stream the summary** to the extension as it's written.
3. **Model setting** — Thorough (Opus, default) / Fast (Sonnet).
4. *(requested during the build)* **Years as numerals** in summaries, voiced
   correctly by TTS.

In-page transcript gathering (option 4 in the proposal) was dropped: yt-dlp is
4.1s of a ~49s run.

## Where the time went (measured, 18-minute video)

| stage | time |
|---|---|
| yt-dlp metadata + subtitles | 4.1s |
| claude startup → first token | 2.6–4.3s |
| claude generating ~3,500 output tokens at ~87 tok/s | **~35–41s** |

Generation dominates, so total time barely moves with Opus; what changes is when
reading can start.

## Results (real server, real video)

| | title | first key point | first paragraph | complete |
|---|---|---|---|---|
| before | — | — | — | ~49s, all at once |
| Opus (default) | 4.4s | **7.6s** | 21.2s | 45.8s |
| Sonnet | 5.2s | **7.9s** | 14.5s | 22.5s |

Input tokens per call: **57,890 → ~6,500**. Cost per summary on this video: Opus
$0.15, Sonnet $0.04 (was $0.38–0.67 depending on cache state).

## Design

- **Lean argv** (`claude_client._claude_argv`): `--tools "" --strict-mcp-config
  --setting-sources "" --system-prompt <short>`, plus `--no-session-persistence`
  for one-shot calls. Verified the default invocation loaded the user's personal
  `~/CLAUDE.md` into every summarize call and the lean one does not. Q&A keeps
  persistence so `--resume` works.
- **Model** is thread-scoped (`use_model`), mirroring `usage.interaction`. Validated
  once in the server against `MODELS` — it ends up in argv, so an unknown name is a
  400, not a silent default. The segment prefetch runs on its own thread and is
  handed the model explicitly.
- **Streaming**: `_TEXT_BODY` holds the rules once; `_TEXT_PROMPT` (batch, byte-
  identical to before) and `_TEXT_PROMPT_STREAM` (one JSON object per line: key
  points, then paragraphs, then a closing line) differ only in output format.
  `stream_ndjson` generalizes the segments parser. The server emits `meta` (enough
  to draw the layout) then `partial` events, then the unchanged `result`.
- **Fallbacks**: custom `TLDW_LLM_CMD` backends and the CLI get the buffered path.
  A stream that doesn't add up to a summary gets one buffered retry; a *real*
  failure (timeout, logged out, CLI error) does not — it would fail identically at
  double the wait.
- **Client**: the in-progress view uses the same `audioRowHtml` as the finished one
  (controls disabled), so nothing jumps when the result replaces it. Key points and
  paragraphs are appended rather than re-rendered. A closed panel keeps the state
  and draws it on re-open instead of a spinner.
- **Years**: prompt asks for numerals. Piper mangles them (1842 → "one thousand
  eight hundred forty two", "the 1990s" → "nineteen hundred ninety z"), so
  `_speakify` voices 1000–2099 itself — before the `%` rewrite, so `1908%` stays a
  quantity.

## Bugs found along the way

- **`stream_ndjson_segments` dropped a final line with no trailing newline** — which
  is usually the `{chosen_ratio, rationale}` line. Mutation-checked.
- **Usage rows never recorded the model**: the CLI's result envelope reports it under
  `modelUsage`, not `model`. Now populated, and `tldw usage` splits cost by model.
- A missed import in the server was caught only by the raw-socket disconnect test —
  the handler died before writing a byte.

## Verification

- `pytest`: all passing, including new coverage for lean argv, per-thread model
  scope, the allowlist (and its sync across server/worker/options), NDJSON line
  reassembly, the streaming summary and its fallbacks, partial events, prefetch
  model inheritance, year voicing, and the client's streaming view.
- Mutation-checked: the final-line flush, the backgrounded no-draw, key-point
  escaping.
- End to end against the real server and real `claude` (timings above).

## Review dimensions

Checked in one pass rather than by eight agents — this session doesn't spawn
subagents.

- **Analyst** ✅ Items 1–3 as approved, plus the requested year change. Recording the
  model in usage is in service of item 3.
- **Architect** ✅ Model allowlisted before argv, refused rather than defaulted. The
  system prompt avoids cmd.exe metacharacters (guarded by a test). Lean mode stops
  personal instructions from riding along with every transcript. Streamed text is
  escaped client-side (guarded).
- **Data** — n/a.
- **Backend** ✅ Retry only on format failures. Prefetch inherits the model.
- **Frontend** ✅ Shared layout, append-only updates, backgrounded state preserved.
- **UX** ✅ Reading starts at ~8s. Disabled controls explain themselves.
- **SDET** ✅ See Verification.
- **DevOps** ✅ No new dependency. Old extensions keep working against the new
  server (unknown event types are ignored; no model means the CLI default).

## Lenses

Product/Middle-management: the complaint was waiting; the fix makes the wait
readable. Developer: net code reuse (one NDJSON parser, one prompt body). Investor/
Purchasing: per-summary cost fell ~5× from the lean flags alone, which is the unit
economics question from the pricing discussion. CEO/Marketing: "you're reading in
eight seconds" demos well; no new risk.

## Items (non-blocking)

- **Summary cache is keyed by video, not model**: switching to Sonnet shows a video's
  cached Opus summary. Arguably the better behaviour; there's no regenerate control.
- **`--setting-sources ""` means Claude Code settings no longer apply to tldw** —
  including a preferred model set there. The Options setting replaces that for the
  extension; the CLI uses the CLI default.
- **npm-installed `claude` on Windows** passes empty-string args through cmd.exe;
  untested (the VM has the native .exe).
- **Years heuristic**: a 1000–2099 count written as digits is voiced as a year
  ("fifteen hundred people"). The prompt asks for counts in words, and the reading
  is still natural.
- The CLI (`tldw URL`) doesn't stream; it gets the lean flags and prints at the end.
