# Plan — Browser extension + local server (text-only TL;DW)

## Goal
Click a toolbar button on a YouTube page → a modal shows the text TL;DW. Powered by
a small local HTTP server (`tldw serve`) that reuses the existing transcript +
Claude summarization. Local-first, but hostable later so trusted users can use a
thin extension with no local install.

## Scope (this PR)
- TEXT ONLY. No video/ffmpeg/piper in the server path.
- `tldw serve` HTTP API + a minimal MV3 (Chromium) extension.
- NOT in scope: hosting/deployment, multi-user accounts, video mode in the browser.

## Server (`youtube_tldw/server.py`, stdlib only)
- CLI: `tldw serve [--host 127.0.0.1] [--port 8765] [--token TOKEN]`
  (token from `--token` or `TLDW_TOKEN` env; if unset, generate one and print it).
- `ThreadingHTTPServer`; bounded worker concurrency (semaphore, e.g. 2).
- Endpoints:
  - `GET /health` → `{ok: true, version}` (no auth).
  - `POST /summarize` (auth) body `{url, ratio?, lang?}` →
    `{video_id, title, channel, source_url, length_label, key_points[], summary_md}`.
  - `OPTIONS *` → CORS preflight.
- Reuse: extract a `core.summarize_url(url, ratio, lang)` used by BOTH cli and
  server (URL validate → metadata → subtitle → parse → summarize_text). No video
  imports on this path.
- Errors → JSON `{error: "..."}` with right status (400 bad url / 422 no transcript
  / 502 claude failure / 401 auth / 429 busy).

## Security (Architect — must-do)
- **Bearer token** required on `/summarize` (`Authorization: Bearer <token>`),
  constant-time compare. Localhost servers are reachable by any web page via
  `fetch`, so a token is mandatory to stop drive-by triggering.
- **CORS** `Access-Control-Allow-Origin` echoes ONLY an allowlisted extension origin
  (`chrome-extension://<id>`), configurable; `Allow-Headers: authorization,content-type`.
- Bind `127.0.0.1` by default. URL goes through the existing youtube allowlist
  (`canonical_video_id`) before any subprocess — no SSRF/extractor abuse.
- Rate-limit / concurrency cap to bound Claude quota use.
- Token never logged; request bodies not logged verbatim.

## Extension (`extension/`, MV3)
- `manifest.json`: `action` (toolbar button), `permissions: [activeTab, scripting,
  storage]`, `host_permissions` for `*://*.youtube.com/*` and the server origin.
- Background service worker: on action click → read active tab URL → if YouTube
  watch/shorts → `scripting.executeScript` to mount a modal (spinner) → `fetch`
  POST to server with token → post result to the modal; handle errors.
- Content modal: shadow-DOM overlay (style isolation), shows title/channel, key
  points list, summary; Copy button; close; loading + error states.
- Options page: server URL + token, saved in `chrome.storage.local`.
- **XSS-safe rendering**: escape all text; render key_points via `textContent`;
  render summary_md through a tiny safe markdown subset (escape → bold/italic/
  headings/paragraphs only). Never `innerHTML` of raw model output.

## Tests
- Server (pytest, mock core.summarize_url): health; auth required (401 без token);
  CORS preflight headers; happy path JSON shape; bad url 400; no-transcript 422;
  busy 429; token constant-time path. Reuse refactor must not break existing CLI
  tests (cli still calls the same core).
- Extension JS: this harness can't run a browser; provide a manual verify checklist
  and keep logic minimal/pure where possible. (Optional: agent-browser smoke later.)

## 7 lenses (relevant)
- Architect/Purchasing: sharing Max inference w/ others = ToS gray area + quota;
  design supports swapping to API billing on the host. Flagged to user.
- Developer: thin extension + tiny stdlib server; easy to hand to trusted users.
- Product: text-only is the right MVP; video stays CLI (too slow for a click).
- UX: modal on-page (reading room) > cramped toolbar popup; Copy + clear errors.

## Review resolutions (authoritative)
- **CORS fail-closed:** allow ONLY an `Origin` of `chrome-extension://…` (default) or a
  pinned `--allow-origin`; reject missing/`null`/web origins; never echo `*`. Pair
  with `Vary: Origin`, `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`.
- **Token:** `secrets.token_urlsafe(32)`, `hmac.compare_digest`; malformed/absent auth
  → 401 via one path; printed once to the operator, never logged or echoed.
- **Loopback only this PR:** refuse to bind a non-127.0.0.1 host. Hosted mode (TLS +
  real per-user auth + Anthropic **API billing, not Max** — a ToS requirement, not a
  gray area) is a separate, separately-reviewed PR. Nothing here assumes Origin=auth.
- **Shared `core.py`** (no `cli`/`audio`/`videomode` imports): `summarize_url(url,
  ratio, lang, *, timeout, max_chars)` → `Summary(meta, result, cue_count)`. Owns and
  `finally`-cleans its OWN tempdir per call. Server imports `core`, never `cli`.
- **Typed errors** (subclasses of `TldrError`, so CLI `except TldrError` is unchanged):
  `BadUrlError`→400, `NoTranscriptError`→422, `TranscriptTooLongError`→413,
  `ClaudeError`→502, `TldrTimeoutError`→504. Raised at the existing sites.
- **Browser flow = single-pass only:** server passes `max_chars=SINGLE_PASS_CHARS`;
  map-reduce-sized transcripts → 413 "use the CLI" (keeps clicks under ~30s, bounds
  quota).
- **Concurrency:** `Semaphore(2)`, **non-blocking** acquire → 429 (no time-window rate
  limiter). Released in `finally`. Per-request claude timeout (server picks ~120s),
  `ThreadingHTTPServer` with deliberate shutdown (call `shutdown()` off-thread).
- **Body:** require `application/json`, `Content-Length` present and ≤ 16 KB, reject
  chunked; validate `url:str`, `0<ratio<=1`, `lang` matches `^[A-Za-z][A-Za-z0-9-]{0,15}$`.
- **`/health`:** `{ok, name:"tldw", version}` only, no auth, no secrets.
- **CLI surface:** `main()` dispatches `serve` before argparse so `tldw <url>` is
  untouched (no subparsers, existing tests unaffected).
- **Extension scope cuts:** `/watch` only; tiny options (server URL + token in
  `chrome.storage.local`, never `.sync`); toolbar-only (no in-page button);
  `host_permissions` = exact `http://127.0.0.1:8765/*` + `activeTab` (no broad youtube
  host grant, no `<all_urls>`).
- **Extension UX musts:** instant modal on click; staged status ("Fetching
  transcript…" → "Summarizing with Claude (~20s)…"); `role=dialog`+`aria-modal`, focus
  trap, Escape closes, focus restore; shadow DOM + own WCAG-AA theme honoring
  `prefers-color-scheme`, ~65ch column; render via escape→safe-markdown-subset
  (no `<a>`/handlers from model output); Copy copies the markdown; re-click focuses the
  open modal; cache last summary per `video_id`; render above + inside fullscreen
  element. Error copy: server-down → "Start it with `tldw serve`"; 422 no captions;
  502 claude; 401 token; 413 too long.

## Effort
~Half day server + tests; ~half day extension + manual verify.
