# API: the `tldw serve` HTTP surface

**Status:** descriptive. It records how the code works today on `chore/docs-tier1`, and sets no new direction.
**Owner:** Backend Engineer (T-001.5).
**Companion:** [`contracts.md`](./contracts.md) is the exhaustive inventory: every route, body key, event type, status code, error string, limit, flag and entry point. This document explains the conventions behind that list and the procedure for changing it. Neither duplicates the other, so **a change to the API updates both documents in the same pull request.**

Paths are relative to the repo root. `server.py` means `src/youtube_tldw/server.py`, and `cli.py` means `src/youtube_tldw/cli.py`.

---

## 1. What the API is, and who calls it

`tldw serve` runs a small HTTP API, built on the standard library, for the browser extension. Each user runs their own copy on their own machine, against their own Claude plan. It has no hosted deployment and no multi-user mode (`server.py:1-10`, `server.py:1933-1939`).

| Caller | Routes it uses | Source |
|---|---|---|
| The extension's background script | `/summarize/cached`, `/summarize/stream`, `/transcribe/stop`, `/speak/stream`, `/ask/stream`, `/preview`, `/segments/stream` | `extension/background.js:108`, `:149`, `:231`, `:266`, `:385`, `:466`, `:511` |
| The extension's Options page (it calls the server directly; it does not message the background script) | `/health`, `/voices`, `/preview` | `extension/options.js:53`, `:70`, `:97` |
| Tests only | `/summarize` (buffered), `/speak` (buffered) | `tests/test_server.py`; no other caller in the repo. Callers outside the repo: `UNKNOWN — needs human confirmation` |

The content script never talks to the server. It talks to the background script over an extension port; that protocol is in `contracts.md`, in the extension sections.

## 2. Protocol and conventions

- **Transport: plain HTTP/1.0 on loopback.**
  - `protocol_version = "HTTP/1.0"` (`server.py:929`), so every response closes its connection. There is no keep-alive and no TLS.
  - Leaving out TLS is a recorded decision, resting on the loopback-only bind. The "TLS everywhere" exception is ADR 0003, in `docs/decisions/`.
- **Style: resource-ish RPC over JSON**, not REST and not GraphQL.
  - A route names an action (`/summarize`, `/ask`, `/speak`, `/preview`, `/transcribe/stop`).
  - `GET` is used only for the two read-only, unauthenticated routes, `/health` and `/voices` (`server.py:972-980`).
  - Everything else is `POST` with a JSON body (`server.py:982-1049`).
  - `OPTIONS` answers CORS preflights on any path (`server.py:966-970`).
  - Any other method gets the stdlib's HTML `501`.
- **Naming.**
  - `/<action>` is a buffered JSON or `audio/mpeg` response.
  - `/<action>/stream` is an NDJSON stream of the same work (`/summarize` and `/summarize/stream`, `/speak` and `/speak/stream`).
  - `/segments/stream` and `/ask/stream` exist only as streams.
  - `/summarize/cached` is a read-only probe.
  - Body keys are `snake_case` (`segment_ratio`, `stream_audio`, `key_points`). Every streamed event, and the probe's response, carries a `type` discriminator.
- **Requests.** Bodies must be:
  - `Content-Type: application/json` (else `415`);
  - sent with a `Content-Length` (else `411`), not chunked (else `400`);
  - non-empty and no larger than the route's cap (else `413`);
  - valid JSON (else `400`).

  See `server.py:1129-1149`. The caps are per route (16 KiB default, 64 KiB for speech and questions, 8 MiB for summaries, which may carry the page's transcript). The values are in `contracts.md` → "Limits, pools and slots".
- **Unknown body keys are ignored.** Handlers read with `.get` and there is no strict schema. The exceptions are values that end up in a subprocess's argv or decide whether to spend minutes of CPU; those are refused rather than defaulted:
  - `model` (`server.py:1090-1100`);
  - `voice` (`server.py:1470-1476`);
  - `whisper` (`server.py:136-160`);
  - `ratio` and `lang` (`server.py:1151-1166`).
- **Query strings are ignored**, because both dispatchers drop everything after `?` (`server.py:973`, `server.py:983`). No route has path or query parameters.
- **Responses** always carry `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`. They carry CORS headers only for an allowed Origin (`server.py:932-953`).

## 3. Base URL

- **Default:** `http://127.0.0.1:8765`. The port comes from `--port`, default `8765` (`cli.py:305`), and the host from `--host`, default `127.0.0.1` (`cli.py:304`). There is no path prefix and no version segment.
- **Allowed hosts:** `serve` refuses any host but `127.0.0.1`, `localhost` and `::1` (`server.py:1935-1939`). `::1` passes that check but fails to bind today; see section 11.
- **The extension's view:** the base URL is the `serverUrl` setting, default `http://127.0.0.1:8765` (`extension/background.js:10`). Trailing slashes are stripped before a path is appended (`extension/background.js:108`).
  - Both manifests grant `host_permissions` for `http://127.0.0.1:8765/*` only (`extension/manifest.json:7`, `extension/manifest.firefox.json:7`).
  - So a different port or host in `serverUrl` is outside the extension's granted hosts. That coupling is documented in `contracts.md` (data and extension sections) and `deployment.md`.

## 4. Authentication and authorization

**The model: one bearer token, all or nothing.**

| Question | Answer | Source |
|---|---|---|
| Header | `Authorization: Bearer <your-token>`. The prefix is exactly `Bearer ` and case-sensitive; the comparison is constant-time (`hmac.compare_digest`). | `server.py:955-960` |
| Which routes need it | Every `POST` route. Not `GET /health`, `GET /voices`, `OPTIONS`, or a `404` for an unknown `POST` path (the 404 is sent before the token check). | `server.py:974-980`, `server.py:990`, `:1017-1022`, `:1106` |
| Scopes and roles | None. One token authorizes every route; there is no per-route or read-only token. | `server.py:955-960` (the only check) |
| Where the token comes from | `--token`, else the `TLDW_TOKEN` env var, else `~/.config/youtube-tldw/token`, else a new `secrets.token_urlsafe(32)` written to that file and chmod-ed `0600` (best effort; a no-op on Windows). | `cli.py:603`, `server.py:689-708`, `config.py:44-56` |
| Lifetime | No expiry and no rotation command. A generated token lasts until the file is deleted. An explicit `--token` or `TLDW_TOKEN` is used for that run only and is never written to disk. | `server.py:692-708`, `tests/test_server.py:771`, `:784` |
| How the extension gets it | The user pastes it into Options; it is stored in `chrome.storage.local` in plain text (see `contracts.md`, storage keys). | `extension/options.js` (save handler) |
| Origin | **CORS only, never authentication.** `_origin_allowed` decides whether `Access-Control-Allow-Origin` is echoed: a pinned origin (`--allow-origin`), or by default any `chrome-extension://` origin, never a missing, `null` or web origin, never `*`. A request is processed whatever its Origin says; only the token gates it. | `server.py:917-924`, `server.py:932-939` |
| What the token protects | Spending the user's Claude plan, their CPU (Piper, faster-whisper), and their YouTube rate limit. | `server.py:1-10` |

**Known weaknesses.** These are recorded as facts, with follow-up tasks from the Architect (`architecture.md`):
- `serve` prints the token on every start (`server.py:1959`).
- The token file is written, then chmod-ed (`server.py:706-707`).
- Origin is not authentication, and the two open `GET`s answer any Host header; there is no Host check in `server.py` (DNS rebinding).
- The extension sends the token to whatever `serverUrl` says.
- The Firefox `moz-extension://` origin is not in the default CORS allowance (`server.py:924`). Whether that matters for Firefox is `UNKNOWN — needs human confirmation`.
- Whether Chrome or Firefox applies CORS to the extension's own fetches, given its `host_permissions`, is `UNKNOWN — needs human confirmation`.

## 5. Rate limits and abuse prevention

There is **no per-client rate limit**: on loopback, the only client is the user. What the server bounds instead is **concurrent work, by kind**, plus the size and shape of every input.

- **Concurrency pools.** Busy is a `429` that names the limit; it is never a queue.

  | Pool | Limit | Taken by | Source |
  |---|---|---|---|
  | `claude` | 6 | summaries, key moments, questions, and the background prefetch | `server.py:560` |
  | `speech` | 2 | reading aloud, and synthesizing a voice preview | `server.py:561` |
  | transcription permit | 1 | transcriptions, machine-wide | `server.py:65` |

  Rules that keep the pools from starving each other:
  - The prefetch never takes the last 2 claude slots, or the last 1 when the caller is handing over its own slot (`keep_free=1`) (`server.py:562`, `server.py:465-466`).
  - Waiting is not work. A request waiting on a transcription or a prefetch releases its slot, then gets it back within 30 s or ends with an in-stream `429` (`server.py:563`, `server.py:1065-1088`, `server.py:1424`, `server.py:1582`).
  - `/transcribe/stop` takes no slot, so a full server can still be told to stop (`server.py:985-989`).
  - The two busy messages are in `contracts.md` → "Server error text".
- **Input bounds:**
  - body caps per route;
  - `ratio` in `(0, 1]`;
  - `lang` regex-bound (`server.py:688`);
  - `question` at most 2,000 chars;
  - `history` trimmed to 12 turns of at most 4,000 chars each (`ask.py:17-19`, `ask.py:52-64`);
  - page-supplied title, channel and duration bounded (`server.py:805-834`);
  - page cues capped at 20,000 rows (`transcript.py:99-140`);
  - transcripts over 350,000 characters refused with a `413` (`core.py:186-192`).
- **Allowlists before any subprocess:**
  - the URL (`urls.py:12-60`);
  - the model (`claude_client.py:164`);
  - the voice (`audio.py:64-70`).

  The backend command is operator config, never request-controlled (`claude_client.py:9-10`, `:205-213`). No subprocess uses a shell (`proc.py:1-6`).
- **CPU guard:** a captionless video longer than the transcription ceiling is refused before any audio is fetched. The ceiling is 2 h by default, or the `whisper_max_minutes` config key. It is checked against yt-dlp's duration, never the client's (`server.py:70`, `server.py:125-133`, `server.py:1382-1400`).
- **Timeouts:** every subprocess and model call has one (`server.py:682-687`, `server.py:549`, `proc.py:66-102`, `claude_client.py:370-392`).
- **Not present:**
  - circuit breakers;
  - backoff on our own model calls, beyond `ask_json`'s single repair retry (`claude_client.py:621-646`);
  - yt-dlp's own retry flags (`metadata.py:56-57`);
  - three voice-download attempts (`audio.py:230-264`).

## 6. Error shapes

Errors come in **three layers**. Every status, and every error string the server can send, is enumerated in `contracts.md` → "Errors".

1. **Before a stream, and on every non-stream route:** the HTTP status plus a JSON body `{"error": "<text>"}` (`server.py:941-953`). Example: a missing token gets `401` `{"error": "missing or invalid token"}`.
2. **Inside a stream**, after a `200` has been sent: one NDJSON line `{"type": "error", "status": <n>, "error": "<text>"}`, which ends the stream. `status` is the status the same failure would have had before the stream. Example: `{"type": "error", "status": 422, "error": "<no-transcript text>"}`.
3. **From the stdlib** (unsupported method, malformed request): an HTML page with no CORS headers. Nothing in the extension handles it.

**From exception to status.** Expected failures are `TldrError` subclasses, and the class decides the status (`_STATUS`, `server.py:711-717`):

| Exception | Status |
|---|---|
| `BadUrlError` | `400` |
| `NoTranscriptError` | `422` |
| `TranscriptTooLongError` | `413` |
| `ClaudeError` | `502` |
| `TldrTimeoutError` | `504` |
| any other `TldrError` | `500` (YouTube's own rate-limiting included, `metadata.py:66-76`) |

The speech routes differ: a timeout is `504` and any other `TldrError` is `502` (`server.py:1711`, `server.py:1889`). **`429` always means the server's own concurrency limit**, never YouTube's.

**Rules the code follows:**
- **`TldrError` text is interface copy.** It is sent verbatim (`str(exc)`), and for most statuses the extension shows it verbatim (`httpError`, `askError` and `speakError` in `extension/background.js:440-447`, `:491-497`, `:571-581`). Rewording a `TldrError` message anywhere in `src/youtube_tldw/` changes what users read.
- **Internals never reach the client.** Anything that isn't a `TldrError` becomes `500` `internal error`, and its detail goes to the server log only (`server.py:1306-1309` and the other `except Exception` blocks).
- **Validation that can fail happens before the stream starts**, so the client gets a real status rather than a `200` followed by an error line (`server.py:1017-1035`, `server.py:1745-1764`, `server.py:1653-1655`). The exceptions are listed in `contracts.md`: an unknown `whisper` value, a URL that fails the allowlist on `/summarize/stream` and `/segments/stream` (an in-stream `400`, because `_validate` checks only the type), and anything only discovered while working (`server.py:1219-1223`).
- **A message should name a next step.** That invariant is UX's, recorded in `contracts.md` alongside the extension's copy. Server strings the user can see that name none are listed there as a follow-up.

## 7. Streaming (NDJSON)

- **Framing:**
  - `Content-Type: application/x-ndjson`;
  - one JSON object per line, written and flushed one line at a time;
  - no `Content-Length`;
  - the stream ends when the server closes the connection (`server.py:1192-1203`, `server.py:1494-1505`, `server.py:1663-1686`, `server.py:1766-1786`).
- **Event types** (full table in `contracts.md` → "NDJSON events"):
  - `progress` is non-terminal on every stream: `message`, `percent`, and `creep` on summaries.
  - **`/summarize/stream`:**
    - non-terminal: `meta` (sent before the summary), `partial` (`kind` `key_point` or `paragraph`), `transcribing`;
    - terminal: `result`, `needs_transcription` or `error`.
  - **`/segments/stream`:**
    - non-terminal: `segment_added`;
    - terminal: `segments`, `segments_done` or `error`.
  - **`/speak/stream`:**
    - with `stream_audio`: `audio_chunk`…, then terminal `audio_end`;
    - without it: one terminal `audio`;
    - or a terminal `error`.
  - **`/ask/stream`:**
    - non-terminal: `delta`;
    - terminal: `answer_done` or `error`.
- **One terminal event per stream** is the contract the extension relies on. A stream that closes without one is reported as "ended unexpectedly" (`extension/background.js:210-212`, `:321-323`, `:423-425`, `:553-555`). The server ends a stream without a terminal event only when the client has already gone.
- **Liveness:**
  - waiting on a prefetch sends a heartbeat every 2 s, only when it changes (`server.py:1517-1536`);
  - a transcription is narrated every 0.5 s (`server.py:1427-1445`);
  - **a summary or an answer in progress sends nothing between progress lines**, so a client timeout must outlast the model call. `creep: true` tells the client to ease its bar forward meanwhile (`core.py:197-198`).
- **Hang-ups:**
  - `/speak/stream` and `/ask/stream` guard their writes, and stop the synthesis or the model process when the client leaves (`server.py:1674-1686`, `server.py:1821-1824`).
  - `/summarize/stream` and `/segments/stream` do not guard their writes. That is why a transcription runs outside the request: a closed socket must not throw away minutes of work (`server.py:52-63`).
- **Ordering:** lines arrive in the order emitted. The speak stream writes from two threads; the speak and ask streams both serialize their writes under one lock, so lines never interleave (`server.py:1670-1686`, `server.py:1775-1786`). On `/ask/stream`, the deltas and the progress both run on the request thread (`claude_client.py:577-579`), so its lock is defensive.

## 8. Versioning, compatibility and deprecation

**No API versioning:**
- no version in any path;
- no version header;
- no content negotiation.

`GET /health` reports `version` (`__version__`, currently `1.3.0`, `src/youtube_tldw/__init__.py:3`), but **nothing in the extension reads it**: `extension/options.js:53-63` reads only `backend`. Nothing checks that the installed server and the loaded extension match. They ship from one repo, but a user's `pipx` install and their unpacked extension can drift apart. So **both directions of skew are real**: a newer extension against an older server, and an older extension against a newer server.

**Deprecation window:** none is defined in the code or the repo. `UNKNOWN — needs human confirmation`. In practice, no route or field has been removed. The code keeps old shapes working, and says so where it does.

**The compatibility procedure, as it works today:**
1. **Add, don't change.** New behaviour goes on a **new route**, or behind an **opt-in body flag** whose absence means exactly the old behaviour.
   - Route example: `/summarize/cached` is a separate route rather than a flag on `/summarize/stream`. An older server answers `404`, which the extension already treats as a cache miss, instead of running a summary without the page's transcript (`server.py:1341-1343`, `extension/background.js:113-126`).
   - Flag examples:
     - `stream_audio`: without it, `/speak/stream` sends the single `audio` event older extensions expect (`server.py:1657-1658`);
     - `whisper`: absent means `off`, the pre-transcription behaviour (`server.py:136-160`);
     - `transcribe` (`server.py:1380`).
2. **Keep reading old request shapes.** `page.captions` is still parsed for extensions older than `page.cues` (`server.py:815-821`).
3. **Unknown keys are ignored, so a newer client can send fields an older server doesn't know.** The exceptions are the refused values in section 2. Sending a new `model` name to an older server is a `400` `unknown model`, so a new model must reach the server before the extension offers it. `tests/test_claude_client.py:284-294` keeps the extension's `MODELS` lists equal to the server's.
4. **Clients tolerate the unknown.** The extension skips unparseable lines and event types it doesn't know (`extension/background.js:174-209`), and shows the server's `429` text whatever it says (`extension/background.js:575-577`). A new **non-terminal** event type is therefore safe for old clients. A new **terminal** type is not: an old client reports "ended unexpectedly".
5. **Removing or renaming a route or field** has no precedent and no defined window. Until the owner defines one: keep the old name working alongside the new one, ship the extension change that stops using it, and record the removal as a decision.

## 9. Machine-readable schema

`N/A — there is no OpenAPI document, JSON Schema, GraphQL SDL or protobuf.` Nothing is generated, so there is nothing to regenerate. The inventory in `contracts.md` is the schema. `tests/test_docs_contracts.py`, added by T-001, checks it against the code by exact backticked tokens: every route literal at the definition sites, every `body.get` key, every NDJSON `type`, every CLI flag, and more. It also checks the reverse, so a route listed in `contracts.md` but gone from the code fails.

## 10. How to add or change a route safely

### 10.1 The definition sites

A route lives in **five places in `server.py` that must agree**, plus the CORS preflight:

1. **`do_GET`** (`server.py:972-980`), for a `GET` route. It takes no body, no auth and no slot, and **should stay that way**: the open `GET`s are the unauthenticated surface.
2. **The `do_POST` chain** (`server.py:982-1016`), for a `POST` route that takes no slot or runs through `_guarded` (`/transcribe/stop`, `/preview`, `/summarize/cached`, `/ask/stream`). It must `return` before the tuple check, or it is 404'd.
3. **The "not one of ours" tuple** (`server.py:1017`, plus `speak` at `server.py:984`), for a route handled in the slot block. Leave it out and the route is unreachable.
4. **The in-slot dispatch** (`server.py:1036-1049`). The final `else` is `/summarize`, so a new name added to the tuple but not here **silently runs the buffered summarizer.** The slot block also does its own work first (`server.py:1026-1033`): every non-`speak` path goes through `_validate`, so a `url` is required, and takes a `_claude_slots` slot; only the `speak` boolean selects `_speech_slots`. A new route added to the tuple therefore silently requires a `url` and takes a claude slot.
5. **`_body_limit`** (`server.py:773-782`), if the route needs more than 16 KiB. A `_guarded` route takes its cap from the `max_bytes` argument instead. `/ask/stream` sets its cap in both places (`server.py:780-781`, `server.py:1015`), and `tests/test_server.py:172` asserts the `_body_limit` one, so change both together.
6. **`do_OPTIONS`** (`server.py:966-970`) answers any path, so it needs no edit. But a new **method** must be added to `Access-Control-Allow-Methods` (`server.py:938`) and given a `do_<METHOD>`.

### 10.2 Rules for the handler

- **Auth first.** Use `_guarded` (`server.py:1102-1127`), or put the route in the slot block, which checks the token at `server.py:1020`. A new `POST` route without the token check is a BLOCK (Backend redline).
- **Bound the body.** Use `_read_body` with the right cap. Never read `rfile` directly.
- **Validate before you start a stream.** Send `400`/`413` as JSON before `send_response(200)`. Reuse `_validate` (`url`, `ratio`, `lang`) and `_model`. `_validate` checks only that `url` is a string (`server.py:1153`); call `canonical_video_id` before `send_response(200)`, as `_run_ask` and `_run_cached` do (`server.py:1762`, `server.py:1355`). Refuse values that reach argv; don't default them.
- **Handle non-object JSON.** `_validate`, `_run_ask`, `_run_preview` and `_run_cached` check `isinstance(body, dict)`; the speech routes don't (section 11).
- **Pick the pool by what the work costs:**
  - a model call takes `claude`;
  - Piper takes `speech`;
  - a cheap read takes none (`slots=None`);
  - minutes of CPU belong in a background job with its own permit, never in a request slot.

  A request that waits on other work releases its slot and retakes it (`_Hold.release`, `_retake`; `server.py:614-637`, `server.py:1065-1088`).
- **Run under the request's model.** Handlers run inside `claude_client.use_model(model)` (`server.py:1037`, `server.py:1117`, `server.py:1124`). A thread you start doesn't inherit that, so re-enter `use_model` in the thread, and build any cache key in the request thread (`server.py:376-394`, `server.py:499-500`).
- **Errors:**
  - raise the right `TldrError` subclass and let `_STATUS` pick the status;
  - catch everything else into `500` `internal error`;
  - write user-facing text that names a next step: it is shown verbatim.
- **Streams:** use `application/x-ndjson`, send exactly one terminal event, and guard writes (`OSError`) if the work is worth stopping when the client leaves.
- **Shared state:** anything module-level is shared across request threads, so guard it with `_cache_lock` or its own lock (`server.py:37`).

### 10.3 Tests to add in the same pull request

All in `tests/test_server.py`, which starts a real `ThreadingHTTPServer` on loopback port 0 (the `srv` fixture, `tests/test_server.py:34-43`) and calls it with `urllib` (`_req`, `_read_ndjson`, `_auth`). `tests/conftest.py` isolates `HOME` and resets the server's registries for every test.

1. **The happy path:** status, `Content-Type`, the response or event shape, and `Access-Control-Allow-Origin` for the extension origin (pattern: `test_summarize_happy`, `tests/test_server.py:103`).
2. **The route needs a token:** `401` without one (pattern: `tests/test_server.py:96`, `:446`, `:619`, `:1664`, `:2216`).
3. **Every validation refusal and its status**, before the stream starts (pattern: `test_ask_rejects_bad_requests`, `tests/test_server.py:574`; `test_a_bad_probe_is_a_400`, `:2228`).
4. **The body cap:** over-cap is `413`, and `_body_limit` returns the intended cap (pattern: `tests/test_server.py:151-174`).
5. **The slot pool:** add the route to `ROUTES` (`tests/test_server.py:2509`). `test_each_route_counts_against_its_own_limit` then proves the route is refused by its own pool and never by the other.
6. **Error mapping:** each `TldrError` class the route can raise gives the expected status, in the right layer (pattern: `test_summarize_error_status_mapping`, `tests/test_server.py:119`; `test_summarize_stream_error_is_in_band`, `:222`).
7. **Compatibility:** if you add a flag, test that its absence gives exactly the old behaviour (pattern: `test_an_absent_setting_keeps_the_old_behaviour_exactly`, `tests/test_server.py:1352`).
8. **If the extension calls it:** cover the caller's handling in the JS harnesses (`tests/js/`; see `testing.md`).

Run the whole suite, `tests/test_docs_contracts.py` included, before committing. `scripts/ci-tldw-tests.sh` runs the same suite in CI.

### 10.4 Docs and the extension, in the same pull request

- **Update `contracts.md`** in every section the change touches: the route row, body keys, events, error strings, limits. `tests/test_docs_contracts.py` fails the build if a route or key is missing from it, or if it lists one that is gone.
- **Update this document** if a convention changed: auth, an error layer, streaming rules, the compatibility procedure.
- **Update the extension caller** (`extension/background.js` or `extension/options.js`), and its status mapper (`httpError`, `askError`, `speakError`) if the route can return a status that needs its own copy.
- **Changing or removing an existing route, key or event:** follow section 8. If it can't be done additively, it is a breaking change. Record it as an ADR, and get the owner's decision on the deprecation path (Backend redline: "No breaking change to a published API contract without a deprecation window and consumer notice").

## 11. Known weaknesses (recorded, not fixed here)

Each one has a follow-up task. The code is unchanged in this pull request.

- **`--host ::1` passes the loopback check but can't bind.** `ThreadingHTTPServer` is IPv4 by default, so `_Server(("::1", …))` raises `socket.gaierror`, which `cli.py` doesn't catch: a traceback and exit `1` (`server.py:1935`, `server.py:1941`, `cli.py:601-608`).
- **A JSON body that isn't an object crashes `/speak` and `/speak/stream`.** For example `[1]`: `_validate_speak` calls `body.get` on it (`server.py:1465`). The client gets a closed connection with no response, and the server log gets a traceback.
- **An empty body is answered `413` `request body too large`** (`server.py:1142-1144`).
- **The transcription-specific `504` texts never reach the user.** `httpError` replaces every `504` with its summarizing copy (`server.py:1429-1431`, `whisper_transcribe.py:231-233`; `extension/background.js:579`).
- **A stop during transcription is a `500`**, unless the job was still waiting for the permit. `whisper_transcribe` raises a plain `TldrError("Transcription cancelled.")`, while the server maps only its own `_Cancelled` to `499` (`whisper_transcribe.py:190`, `:212`, `:229`, `:280`, `:292`; `server.py:265-277`).
- **If the `claude` executable is missing, a streamed call fails with `internal error`.** `subprocess.Popen` raises `FileNotFoundError`, which is not a `TldrError` (`claude_client.py:333-343`). This is derived from the code; no test exercises it.
- **Unauthenticated callers can map the `POST` routes.** Unknown `POST` paths get `404` before the token check (`server.py:1017-1019`).
- **The `server.py:962-963` comment says the request log has "no query".** The stdlib log line includes the query string, if a client sends one (observed).
- **`/summarize` (buffered) reports `transcript_source` `captions` whatever the transcript was, and ignores `page`** (`server.py:1172-1188`).
- **`tldw config set` validates only the hyphen spellings.** `whisper_model` and `whisper_max_minutes`, typed with underscores, skip `_check` (`cli.py:318-330`, `cli.py:349-352`). The server then ignores a bad value.

## How this was verified

- **Routes.** Read `src/youtube_tldw/server.py` in full, plus `cli.py`, `core.py`, `summarize.py`, `ask.py`, `claude_client.py`, `proc.py`, `urls.py`, `txcache.py`, `config.py` and `__init__.py`. Every route literal was matched against the `fetch(` calls in `extension/background.js` and `extension/options.js`. `git grep` confirmed that the buffered routes have no caller outside `tests/`.
- **Claims against tests.** Cross-checked against `tests/test_server.py` (fixtures, status mapping, body caps, the per-route pool matrix, preflight, token persistence) and `tests/test_cli.py`.
- **Edge behaviour.** Confirmed with a throwaway loopback probe of `server._Server` in the scratchpad, with `HOME` and `TMPDIR` isolated and no model calls:
  - `501` for unsupported methods;
  - unauthenticated `404`;
  - an ignored query string;
  - empty-body `413`;
  - the `/speak` non-object crash;
  - an unknown-model `400`;
  - an in-stream `whisper` `400`;
  - a case-sensitive `Bearer`;
  - a web Origin getting no CORS echo;
  - `::1` failing to bind.

  No probe output containing a token is reproduced. The claude-missing path is derived from the code alone.

## Update this doc when

- `src/youtube_tldw/server.py` changes:
  - the route definition sites (`do_GET`, `do_POST`, the tuple, the in-slot dispatch, `_body_limit`, `do_OPTIONS`, `_cors_headers`);
  - `_authorized`, `_origin_allowed`, `_read_body`, `_validate`, `_model` or `_guarded`;
  - `_STATUS`, the pools, limits or timeouts;
  - any stream's framing or terminal events;
  - `serve`, `load_or_create_token` or `TOKEN_FILE`.
- `src/youtube_tldw/cli.py` changes the `serve` parser or the `--host`/`--port` defaults.
- `extension/background.js` or `extension/options.js` changes a `fetch(` call, `serverUrl` handling, or a status mapper.
- Either manifest changes `host_permissions`.
- A compatibility mechanism from section 8 is added or removed.
- A deprecation window is defined.
- A machine-readable schema is introduced.
- **Always together with `contracts.md`.**
