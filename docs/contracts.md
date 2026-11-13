# Contracts — the exhaustive inventory

> **The single most important doc for safe autonomous commits** (`AGENTS.md` § 6, T-001.17). It lists **every** route, request and response contract, event, port message, public interface, data store, environment variable, config key, storage key, manifest permission, page selector, integration, background job and cross-cutting invariant in tldw. Each entry gives the invariant (what must stay true), the blast radius (what depends on it) and the source `path:line`.
>
> **Before changing anything, find it here.** If your change alters or removes an entry, it is a **contract change**:
> - run the regression-safety check (`AGENTS.md` § 7, [`testing.md`](./testing.md));
> - update this file in the same PR, under the `/docs` unanimous-consensus gate.
>
> **This file is checked mechanically.** `tests/test_docs_contracts.py` runs in CI and fails if:
> - an item in the code isn't listed here as a backticked token;
> - a listed route, env var or storage key no longer exists in the code.

**Narrative companions:**
- [`api.md`](./api.md): protocol, auth, errors, streaming, and how to change a route safely.
- [`data-model.md`](./data-model.md): stores, record shapes, PII, and how to change a format safely.
- [`architecture.md`](./architecture.md): trust boundaries and security invariants.

## Category index (T-001.17)
| T-001.17 category | Where it is here |
|---|---|
| Routes and endpoints | HTTP routes; Extension → server HTTP calls |
| Request and response contracts | Request body keys, per route; Response shapes; Errors; User-facing message contracts |
| Event and message schemas | NDJSON events (the server's stream events); Extension message protocol (port and tab messages). Webhooks: N/A, because tldw neither sends nor receives any. |
| Machine-readable schema | N/A: no OpenAPI or JSON Schema exists, so this inventory *is* the schema. |
| Public interfaces and signatures | CLI; Public module entry points; Source text tests depend on |
| Data contracts | Data stores; `chrome.storage.local` keys; Lists mirrored from the server (tables, indexes, foreign keys: N/A, file-backed, see `data-model.md`) |
| Third-party integrations | Third-party integrations and subprocesses; Model I/O contract; YouTube page contract |
| Environment variables and config keys | Environment variables; Config keys |
| Feature flags | Feature flags (N/A, with the env vars that act like flags) |
| Scheduled jobs, cron and queues | Scheduled jobs and background work; Threads (no cron and no queues) |
| Cross-cutting invariants | Server-side cross-cutting invariants; Keyboard and accessibility baseline; Cross-cutting data invariants; Manifests |

## HTTP server: conventions used in this section

- **Source of truth:** `src/youtube_tldw/server.py` (abbreviated `server.py` below), `src/youtube_tldw/cli.py`, and the modules they call. Every `path:line` is against branch `chore/docs-tier1`.
- **Runtime values** in quoted interface text are written `<like-this>`; everything else in a quoted string is the exact text the code sends.
- **"Caller"** names the extension file that issues the request. The extension side of each call (message types, storage, what the page renders) is in the extension sections of this document.
- **Transport:** plain HTTP/1.0 on loopback. `protocol_version = "HTTP/1.0"` (`server.py:929`), so every response closes its connection; NDJSON streams carry no `Content-Length` and end when the socket closes.
- **Query strings are ignored** on every route: both dispatchers do `self.path.split("?")[0]` (`server.py:973`, `server.py:983`). There are no path parameters and no query parameters.

## HTTP routes

### Where routes are defined (all of these must agree)

| Definition site | What it decides | Invariant | Blast radius | Source |
|---|---|---|---|---|
| `do_GET` | The two `GET` routes, `/health` and `/voices`; any other GET path is a JSON 404 | A new GET route is added here and nowhere else (GET routes take no body, no auth, no slot) | `options.js` reads both; a rename silently breaks the Options page (it swallows errors, `options.js:66`, `options.js:75`) | `server.py:972-980` |
| `do_POST` chain (routes outside the slot block) | `/transcribe/stop` (inline, no slot), `/preview`, `/summarize/cached` and `/align/stream` (`_guarded`, `slots=None`; `/align/stream` brings its own `_align_lock` and the transcription permit, T-020), `/ask/stream` (`_guarded`, claude pool) | A route handled here must `return` before the tuple check below, or it is 404'd | Every POST caller in `background.js` and `options.js` | `server.py:982-1016` (before T-020); the chain now runs `server.py:995-1036`, with `/align/stream` at `:1030-1034` |
| The "not one of ours" tuple | `("/summarize", "/summarize/stream", "/segments/stream")` plus `speak = path in ("/speak", "/speak/stream")`; anything else that reached here is a JSON 404, **sent before auth** | A route dispatched in the slot block must appear in this tuple (or in `speak`), or it can never be reached | Every slot-block route; the tuple is the only place `/summarize` (buffered) is named besides `_body_limit` and the dispatch | `server.py:984`, `server.py:1017-1019` |
| In-slot dispatch | Which handler runs after auth, body, validation, model check and slot | Each name here must also be in the tuple; the `else` branch is `/summarize` (buffered). Before dispatch, every non-`speak` path runs `_validate` (a `url` is required) and takes a `_claude_slots` slot; only `speak` selects `_speech_slots` (`server.py:1026-1033`) | Same routes as the tuple | `server.py:1036-1049` |
| `_body_limit` | Per-path body cap for the slot-block routes | A new large-body route gets its cap here **and** must be routed through `_read_body(_body_limit(path))` | 413s on any route whose cap is wrong | `server.py:773-782`, used at `server.py:1023` |
| `do_OPTIONS` | Answers **every** path with `204`, `Content-Length: 0`, and the CORS headers if the Origin is allowed | Must keep answering without auth: the browser's preflight carries no `Authorization` | Any browser caller subject to CORS: every extension POST sends `Authorization` and `Content-Type: application/json`, which makes it non-simple. Whether Chrome or Firefox actually preflights fetches from the extension's background or options page, given its `host_permissions`, is `UNKNOWN — needs human confirmation` | `server.py:966-970` |
| CORS `Access-Control-Allow-Methods` | `POST,GET,OPTIONS` | Must list every method a route uses | Preflight for every POST | `server.py:938` |

**`/ask/stream`'s body cap is set in two places:** `_body_limit` returns `MAX_ASK_BYTES` for it (`server.py:780-781`), and `do_POST` passes `max_bytes=MAX_ASK_BYTES` to `_guarded` (`server.py:1015`). At runtime only the second one is used, because `/ask/stream` never reaches `_read_body(_body_limit(path))`; the first is asserted by `tests/test_server.py:172`. Change both together.

### Route inventory

Order of checks, for every non-`speak` POST route in the slot block: path (404) → bearer token (401) → body (`_read_body`: 400/411/413/415) → field validation (400) → `model` allowlist (400) → slot (429) → handler (`server.py:1017-1049`). For `/speak` and `/speak/stream`: path → token → body → `model` → speech slot (429) → field validation in the handler (`_validate_speak`: 400/503) → handler (`server.py:1030-1035`, `server.py:1653`), so with the speech pool full a malformed speak request gets `429`, not `400`. For `_guarded` routes: token → body → `model` → slot if any → handler (`server.py:1102-1127`).

| Route | Method | Auth | Body cap | Slot pool | Response | Caller | Invariant | Blast radius | Source |
|---|---|---|---|---|---|---|---|---|---|
| `/health` | `GET` | none | no body read | none | JSON `200` (keys below) | `options.js:53` | Unauthenticated and must stay so; must not reveal the operator's model command (`claude_client.py:238-248`) | Options page hides the model picker for a custom backend (`options.js:54-63`) | `server.py:974-976` |
| `/voices` | `GET` | none | no body read | none | JSON `200` `{"voices": [{"id","label"}]}` | `options.js:70` | Shape is `audio.voice_list()` | Options voice picker (falls back to `VOICE_FALLBACK` if the call fails) | `server.py:977-978`, `audio.py:73-74` |
| `/transcribe/stop` | `POST` | bearer | 16 KiB (`MAX_BODY_BYTES`) | **none, on purpose** (it must work while every slot is busy) | JSON `200` `{"stopped": bool}` | `background.js:305` | Never takes a slot; does no work but setting a flag; does not validate `model` | Stop button during a transcription; `tests/test_server.py:1617` | `server.py:985-1005` |
| `/preview` | `POST` | bearer | 16 KiB | none for a memoized preview; a `speech` slot around synthesis (`try_take`, given back in `finally`) | `audio/mpeg` bytes `200` | `background.js:540`, `options.js:97` | Voice is allowlisted before anything runs | Voice pickers in the panel and in Options | `server.py:1006-1009`, `server.py:1857-1901` |
| `/summarize/cached` | `POST` | bearer | 16 KiB | none (one disk read); a hit may start a key-moment prefetch, which takes its own claude slot with the full reserve | plain JSON `200`: `{"type":"miss"}` or `{"type":"result", …}` | `background.js:182` | Read-only on a miss: fetches nothing, starts nothing. A separate route (not a flag) so an older server answers 404, which the extension treats as a miss | The panel's cache probe before it reads the page (`background.js:187-200`) | `server.py:1011-1013`, `server.py:1330-1369` |
| `/ask/stream` | `POST` | bearer | 64 KiB (`MAX_ASK_BYTES`) | `claude` | NDJSON `200` | `background.js:459` | Validation errors are sent before the stream starts; a question never starts a transcription | Q&A in the panel | `server.py:1014-1016`, `server.py:1743-1855` |
| `/align/stream` | `POST` | bearer | 16 KiB (`MAX_BODY_BYTES`) | none from the pools; `_align_lock` (one alignment at a time, process-wide) and `_tx_permit` (waited for at most `ALIGN_PERMIT_WAIT_S`, 20 s), both taken only when some window misses the cache | NDJSON `200`, or plain JSON `400`/`401`/`411`/`413`/`415`/`503` before the stream | `background.js` `handleAlign` (T-020) | The check order is contracted (see "Word timing (`/align/stream`, T-020)"). Cached windows are served before anything is required; nothing is downloaded until ffmpeg, the model on disk, the lock, the cooldown and any running transcription all allow it. The word list is never in the request. | "Cover listed words" word timing. An older server answers `404`, and the panel keeps every hit on its caption-line range | `server.py:1030-1031`, `server.py:1876-2096`, `align.py` |
| `/summarize` | `POST` | bearer | 8 MiB (`MAX_SUMMARIZE_BYTES`) | `claude` | JSON `200` (`_to_payload`, no `stats`) or JSON error | none in the repo outside `tests/test_server.py`; external callers `UNKNOWN — needs human confirmation` | Buffered. Ignores `page`, `whisper`, `refresh`; always reports `transcript_source` `captions` (`server.py:1188` calls `_to_payload` without a source); does not write the summary cache | Tests (`tests/test_server.py:103`, `:119`, `:159`, `:176`) | `server.py:1017`, `server.py:1047`, `server.py:1172-1188` |
| `/summarize/stream` | `POST` | bearer | 8 MiB | `claude` (released while waiting on a transcription, retaken after) | NDJSON `200` | `background.js:223` | One terminal event per stream (`result`, `error` or `needs_transcription`) unless the client hung up | The main summary flow | `server.py:1042-1043`, `server.py:1190-1328` |
| `/segments/stream` | `POST` | bearer | 16 KiB | `claude` (released while waiting on a prefetch, retaken before a live call) | NDJSON `200` | `background.js:585` | One terminal event (`segments`, `segments_done` or `error`) | "Play key moments" | `server.py:1044-1045`, `server.py:1485-1650` |
| `/speak` | `POST` | bearer | 64 KiB (`MAX_SPEAK_BYTES`) | `speech` | `audio/mpeg` bytes `200` or JSON error | none in the repo outside `tests/test_server.py`; external callers `UNKNOWN — needs human confirmation` | Buffered mp3 | Tests (`tests/test_server.py:282`, `:397`, `:465`, `:473`, `:486`) | `server.py:1038-1039`, `server.py:1652-1741` |
| `/speak/stream` | `POST` | bearer | 64 KiB | `speech` | NDJSON `200` | `background.js:340` | `stream_audio: true` opts into `audio_chunk`/`audio_end`; without it, one `audio` event | "Listen" in the panel | `server.py:1040-1041`, `server.py:1652-1741` |
| any path | `OPTIONS` | none | no body read | none | `204`, CORS headers only if the Origin is allowed | the browser (preflight) | See the definition-site table | Every extension POST | `server.py:966-970` |
| any other method (`PUT`, `DELETE`, `HEAD`, `PATCH`, …) | — | none | — | none | stdlib HTML `501` ("Unsupported method"), **no CORS headers** | none | There is no `do_<METHOD>` for them; the stdlib answers | None today | Python `http.server.BaseHTTPRequestHandler`; observed by the probe in "How this was verified" |
| any unknown path | `GET` / `POST` | none (POST 404 is sent **before** the token check) | — | none | JSON `404` `{"error": "not found"}` | none | — | An unauthenticated caller can tell known POST paths from unknown ones (follow-up) | `server.py:979-980`, `server.py:1017-1019` |

### Request headers the server reads

| Header | Used for | Invariant | Blast radius | Source |
|---|---|---|---|---|
| `Authorization` | `Bearer <your-token>`; the prefix is case-sensitive and exactly `"Bearer "` (`bearer <your-token>` is a 401); compared with `hmac.compare_digest` | The only authentication there is | Every POST route | `server.py:955-960` |
| `Origin` | CORS only: decides whether `Access-Control-Allow-Origin` is echoed. A request with a disallowed or missing Origin is **still processed** | Origin is never authentication | A web page can't read a response without the echo; non-browser callers are unaffected. Whether the extension's own fetches depend on the echo (they run with `host_permissions`) is `UNKNOWN — needs human confirmation` | `server.py:917-924`, `server.py:932-939` |
| `Content-Type` | Must start with `application/json`, else 415 | JSON only | Every POST | `server.py:1133-1136` |
| `Content-Length` | Required (411), must be `> 0` and `<=` the route's cap (413) | Bounded reads | Every POST | `server.py:1137-1144` |
| `Transfer-Encoding` | `chunked` is refused (400) | No unbounded bodies | Every POST | `server.py:1130-1132` |
| `Host` | Not read anywhere in `server.py` | — | DNS-rebinding exposure of the open GETs is tracked by the Architect's follow-up | `server.py` (no match for `Host`) |

### Response headers the server sets

| Header | Value | Where | Source |
|---|---|---|---|
| `Content-Type` | `application/json`, `audio/mpeg` or `application/x-ndjson` (always a server constant) | every response | `server.py:942-948`, `server.py:1193`, `server.py:1495`, `server.py:1665`, `server.py:1767` |
| `Content-Length` | set on JSON and mp3 responses, **absent** on NDJSON | non-stream | `server.py:948` |
| `X-Content-Type-Options` | `nosniff` | every response we build | `server.py:949`, `:1195`, `:1497`, `:1667`, `:1769` |
| `Cache-Control` | `no-store` | every response we build | `server.py:950`, `:1194`, `:1496`, `:1666`, `:1768` |
| `Access-Control-Allow-Origin` + `Vary: Origin` + `Access-Control-Allow-Headers: authorization,content-type` + `Access-Control-Allow-Methods: POST,GET,OPTIONS` + `Access-Control-Max-Age: 600` | only when `_origin_allowed` | every response we build, and `OPTIONS` | `server.py:932-939` |
| `Server` | `tldw` plus the stdlib's Python version suffix | every response | `server.py:928` |

## Request body keys, per route

Unknown keys are ignored on every route (bodies are read with `.get`; there is no strict schema). The exceptions that reject a value are noted.

| Key | Route(s) | Type / validation | Invariant | Blast radius | Source |
|---|---|---|---|---|---|
| `url` | `/summarize`, `/summarize/stream`, `/segments/stream`, `/summarize/cached`, `/ask/stream`, `/transcribe/stop`, `/align/stream` | string; through the YouTube allowlist (`urls.canonical_video_id`) before any subprocess | No URL reaches yt-dlp unvalidated | Every video request | `server.py:1153-1154`, `server.py:1747-1750`, `server.py:1000`, `urls.py:22-60` |
| `video_id` | `/transcribe/stop` | used if `url` is absent, same allowlist | — | Nothing in the extension sends it (`background.js:309` sends `url`) | `server.py:1000` |
| `ratio` | `/summarize`, `/summarize/stream`, `/segments/stream`, `/summarize/cached` | number in `(0, 1]`, else 400 | Part of the summary and selection cache keys | `background.js:588` sends it to `/segments/stream` as the segment ratio | `server.py:1155-1162` |
| `lang` | `/summarize`, `/summarize/stream`, `/segments/stream`, `/summarize/cached`, `/ask/stream` | string matching `^[A-Za-z][A-Za-z0-9-]{0,15}$`, default `en`, else 400 | Becomes a yt-dlp argument, so it is regex-bound | The extension never sends it (default used) | `server.py:688`, `server.py:1163-1165`, `server.py:1758-1760` |
| `model` | every POST route except `/transcribe/stop` | `null`/absent, or a key of `claude_client.MODELS` (`opus`, `sonnet`); anything else is a **400 `unknown model`** | Ends up in argv, so it is an allowlist, never a default swap | `background.js` sends it on summarize, probe, segments, ask | `server.py:1090-1100`, `claude_client.py:164` |
| `refresh` | `/summarize/stream` | `true` re-runs the model but keeps the cached transcript | Only `is True` counts | "Regenerate" in the panel | `server.py:1217` |
| `whisper` | `/summarize/stream` | `off`/`ask`/`always`; absent means `off`; anything else is an **in-stream** 400 `unknown whisper setting`; a server-side `whisper` config key overrides the body | Absent must keep meaning the pre-transcription behaviour | `background.js:227` | `server.py:136-160`, `server.py:1219-1223` |
| `whisper` (on `/align/stream`) | `/align/stream` | the same `_whisper_policy` (`off`/`ask`/`always`, a server-pinned `whisper` wins); an unknown value is a plain-JSON `400`; `off` is a `503` `whisper_off`. `ask` allows word timing with no second prompt: turning the feature on is the consent (T-020 Q4) | `off` must block alignment, pinned or client-sent | `background.js` `handleAlign` sends the `whisper` setting | `server.py:1909-1918` |
| `windows` | `/align/stream` | list of 1–`MAX_ALIGN_WINDOWS` (200) objects; else `400` naming the field (`align.WindowError`). Exact duplicates are timed once, and each original index still gets its `window` event | Only server-validated ints reach ffmpeg's argv | "Cover listed words" | `align.py:96-118`, `server.py:1905` |
| `start_ms`, `end_ms` (in `windows`) | `/align/stream` | `int`, not `bool`; `0 <= start_ms < end_ms`; `end_ms - start_ms <= MAX_ALIGN_WINDOW_MS` (12,000); any `end_ms` past `_max_transcribe_ms()` is a `413` `too_long` (not a `400`) | Rendered into argv only through `timing.to_ffmpeg_ts` | Same | `align.py:107-117`, `server.py:1923-1925` |
| `transcribe` | `/summarize/stream` | `true` accepts a transcription offer | Only `is True` counts | Accepting the offer in the panel | `server.py:1380` |
| `segment_ratio` | `/summarize/stream`, `/summarize/cached` | number in `(0, 1]`, else ignored (no error) | Must match the ratio the later `/segments/stream` uses, or the prefetch is wasted | Key-moment prefetch | `server.py:360-373` |
| `page` | `/summarize/stream` | object, or ignored | Untrusted; every field is bounded | The page-transcript path (no YouTube request) | `server.py:785-834`, `server.py:1246-1250` |
| `title` (in `page`) | `/summarize/stream` | string, trimmed to 300 chars, default `(untitled)` | — | Title shown in the panel and saved `.md` | `server.py:831` |
| `channel` (in `page`) | `/summarize/stream` | string, trimmed to 200 chars, default `(unknown)` | — | Same | `server.py:832` |
| `duration_ms` (in `page`) | `/summarize/stream` | number, not bool, `0 < n < 24h`, else ignored (falls back to the last cue's end) | Never trusted for the transcription ceiling (`server.py:1382-1384`) | Original-length label | `server.py:805-808`, `server.py:830` |
| `cues` (in `page`) | `/summarize/stream` | list of `{start_ms, text}` rows; at most `MAX_PANEL_ROWS` (20,000), each text cut to 1,000 chars; bad rows dropped | Preferred over `captions` | The panel-read transcript | `server.py:809-814`, `transcript.py:104-140` |
| `start_ms` | inside `page.cues` rows | number, not bool, `0..24h` | — | Same | `transcript.py:119-123` |
| `text` | inside `page.cues` rows | non-empty string | — | Same | `transcript.py:124-126` |
| `captions` (in `page`) | `/summarize/stream` | caption-track text (older extensions) | Still parsed for an older extension | Compatibility | `server.py:815-821` |
| `captions_format` (in `page`) | `/summarize/stream` | string hint (`json3` or other) | — | Same | `server.py:820-821`, `transcript.py:143-147` |
| `status` (in `page`) | `/summarize/stream` | logged (first 200 chars), never used otherwise | — | Server log only | `server.py:1247-1248` |
| `max_length` | `/segments/stream` | duration string (`90s`, `5m`); unparseable is ignored; a capped request has no cache key | A capped selection never reads or overwrites the shared one | The extension never sends it | `server.py:1487-1493`, `server.py:1554-1558` |
| `title`, `channel`, `summary` | `/speak`, `/speak/stream` | required strings, else 400 | — | "Listen" (`background.js:344-345`) | `server.py:1465-1466` |
| `key_points` | `/speak`, `/speak/stream` | list of strings, default `[]`, else 400 | — | Same | `server.py:1467-1469` |
| `voice` | `/speak`, `/speak/stream`, `/preview` | string, allowlisted by `audio.resolve_voice` (`female`/`male` aliases too); default `amy` | The caller's string is only an allowlist key, never a path | Voice pickers | `server.py:1470-1476`, `server.py:1861-1867`, `audio.py:59-70` |
| `stream_audio` | `/speak/stream` | `true` opts into `audio_chunk` blocks | Only `is True` counts; absent keeps the single `audio` event | `background.js:346` | `server.py:1658` |
| `question` | `/ask/stream` | non-empty string, at most `ask.MAX_QUESTION_CHARS` (2,000) or 413 | — | Q&A | `server.py:1748-1754`, `ask.py:17` |
| `history` | `/ask/stream` | list (else 400); items that aren't `{role, content}` are dropped; last 12 turns, each cut to 4,000 chars | — | Q&A context | `server.py:1755-1757`, `ask.py:52-64` |
| `role`, `content` | inside `history` items | `role` is `user` or `assistant`; `content` a string | — | Same | `ask.py:58-63` |

## Response shapes

### Summary payload (`_to_payload`)

Returned by `/summarize`, carried by the `result` event of `/summarize/stream`, stored in the summary cache, and returned by `/summarize/cached` on a hit (`server.py:1904-1921`).

| Key | Meaning | Invariant | Blast radius | Source |
|---|---|---|---|---|
| `transcript_source` | `captions` or `audio:<model>` | Rides in the payload because a cached summary never touches a transcript | Panel's "transcribed from audio" note and the saved `.md` (`content.js:3191`) | `server.py:1911` |
| `video_id` | 11-character id | — | Extension's in-memory cache key | `server.py:1912` |
| `title` | video title | — | Panel header, saved `.md` | `server.py:1913` |
| `channel` | channel name | — | Same | `server.py:1914` |
| `source_url` | `https://www.youtube.com/watch?v=<id>` | — | Saved `.md` | `server.py:1915`, `metadata.py:25-26` |
| `original_length` | formatted video length | — | Panel meta line | `server.py:1916` |
| `length_label` | read-time label | — | Panel meta line | `server.py:1917` |
| `key_points` | list of strings | — | Panel, Listen, saved `.md` | `server.py:1918` |
| `summary_md` | markdown body | — | Panel, Listen, saved `.md` | `server.py:1919` |
| `rationale` | model's note on its compression | — | Panel | `server.py:1920` |

Added outside `_to_payload`: `stats` (from `usage.stats()`: `videos`, `watch_ms`, `read_ms`, `saved_ms`, `saved_hours`; `usage.py:208-224`) on every streamed or probed `result`, and `cached: true` on a result served from the summary cache (`server.py:1233-1234`, `server.py:1328`, `server.py:1368-1369`). The stored payload never contains `stats` (`server.py:1325-1327`). The extension reads `stats.videos` and `stats.saved_hours` (`content.js:1221-1229`).

### `/health`

| Key | Value | Invariant | Blast radius | Source |
|---|---|---|---|---|
| `ok` | `true` | — | — | `server.py:975` |
| `name` | `"tldw"` | — | — | `server.py:975` |
| `version` | `__version__` (currently `1.3.0`) | The only version signal the server exposes; nothing in the extension reads it | Future version-skew checks | `server.py:975`, `__init__.py:3` |
| `backend` | `{"kind": "claude", "label": "Claude", "models": ["opus", "sonnet"], "streaming": true}` or `{"kind": "custom", "label": "your model", "models": [], "streaming": true}` | Never exposes the configured command | `options.js:54-63` | `server.py:976`, `claude_client.py:238-248` |
| `kind` (in `backend`) | `claude` or `custom` | The only `backend` field the extension reads | `options.js:57` disables the model picker for anything but `claude` | `claude_client.py:245`, `:247` |
| `label` (in `backend`) | `Claude` or `your model`; also used in progress text | — | Progress line "summarizing with <label>" | `claude_client.py:245`, `:247`, `summarize.py:134-137` |
| `models` (in `backend`) | sorted `MODELS` keys, or `[]` for a custom backend | Must equal the allowlist `_model` enforces | Nothing reads it today | `claude_client.py:246`, `:248` |
| `streaming` (in `backend`) | always `true` | — | Nothing reads it today | `claude_client.py:246`, `:248` |

`/voices` keys: `voices` (a list), each item `id` and `label` (`audio.py:73-74`), read by `options.js:70-73`.

### Other non-NDJSON responses

| Route | Body | Source |
|---|---|---|
| `/voices` | `{"voices": [{"id": "<voice id>", "label": "<label>"}, …]}` for the 13 voices in `audio.VOICES` | `server.py:978`, `audio.py:44-58`, `audio.py:73-74` |
| `/transcribe/stop` | `{"stopped": true}` if a transcription for that video was running, else `{"stopped": false}` | `server.py:1004`, `server.py:172-184` |
| `/summarize/cached` | `{"type": "miss"}`, or `{"type": "result", <stored payload>, "cached": true, "stats": {…}}` — plain JSON, one object | `server.py:1361`, `server.py:1368-1369` |
| `/preview`, `/speak` | `audio/mpeg` bytes | `server.py:1741`, `server.py:1875`, `server.py:1901` |
| `/summarize` | the `_to_payload` object | `server.py:1188` |

## NDJSON events

Every stream is `application/x-ndjson`: one JSON object per line, `\n`-terminated, written and flushed one at a time (`server.py:1201-1203`, `:1503-1505`, `:1674-1686`, `:1777-1786`). Each object has a `type`. The extension ignores lines it can't parse and types it doesn't know (`background.js:248-283`).

| `type` | Route(s) | Fields | Terminal? | Invariant | Blast radius | Source |
|---|---|---|---|---|---|---|
| `progress` | all four streams, and `/align/stream` (`message` only) | `message`, `percent` (number or null); `/summarize/stream` adds `creep` (bool: "ease forward until the result") | no | Human-readable; never parsed | Progress bar and status line | `server.py:1205-1207`, `:1507-1509`, `:1688-1690`, `:1788-1790` |
| `meta` | `/summarize/stream` | `video_id`, `title`, `channel`, `source_url`, `original_length` | no | Sent before the summary, including on a cache hit | Panel draws its layout early | `server.py:1229-1232`, `server.py:1252-1258` |
| `partial` | `/summarize/stream` | `kind`, `text` | no | `kind` is one of the values in the next table | Summary fills in as it is written | `server.py:1259-1260` |
| `result` | `/summarize/stream` | the `_to_payload` keys, `stats`, and `cached: true` on a cache hit | yes | Exactly one per successful stream | Panel's final render, extension cache | `server.py:1233-1234`, `server.py:1328` |
| `needs_transcription` | `/summarize/stream` | `video_id`, `title`, `channel`, `duration_ms`, `estimate_s` (0 when the length is unknown), `model`, `installed`, `install_hint` (empty when installed) | yes (accepting sends a new request with `transcribe: true`) | Offered when there are no captions and the policy is `ask` and not accepted, or faster-whisper isn't installed | The transcription offer | `server.py:1404-1410` |
| `transcribing` | `/summarize/stream` | `video_id`, `estimate_s` | no | Sent before the wait starts, on every transcription (including `always` and a joined job) | Stop button and the long client timeout | `server.py:1418-1419` |
| `segment_added` | `/segments/stream` | `segment`: `{start, end, label}` (seconds, seconds, `m:ss`) | no | One per clip as the model finds it, live calls only | Playback starts before the list is done | `server.py:1601-1607`, `core.py:132-136` |
| `segments_done` | `/segments/stream` | `title`, `channel`, `source_url` | yes | Ends a live stream that sent `segment_added` | Same | `server.py:1545-1550`, `server.py:1647-1648` |
| `segments` | `/segments/stream` | `segments` (list of `{start, end, label}`), `title`, `channel`, `source_url` | yes | The whole list at once: cache hit, prefetch hit, or a live call that streamed nothing | Same | `server.py:1538-1543`, `server.py:1649-1650` |
| `audio_chunk` | `/speak/stream` with `stream_audio: true` | `seq` (0-based), `mp3_base64` | no | Blocks concatenate (as bytes) into one mp3 | Streaming playback | `server.py:1702-1705` |
| `audio_end` | `/speak/stream` with `stream_audio: true` | `chunks`, `bytes` | yes | — | Same | `server.py:1735-1736` |
| `audio` | `/speak/stream` without `stream_audio` | `mp3_base64` (the whole clip) | yes | Kept for older extensions | Older extensions | `server.py:1737-1739` |
| `window` | `/align/stream` (T-020) | `i` (the index into the request's `windows`), `start_ms`, `end_ms`, `words` (a list of at most 200 `{s, e, w}`: absolute media ms, clamped to the window, `w` at most 64 characters), `cached` (bool) | no | One per request index, cached ones first; words with `e < s`, or more than `BAD_TIMESTAMP_SLACK_MS` (1 s) outside the window, are dropped on the server. The words are only ever compared with the list's pattern in the browser, never put in the DOM | Word ranges replace caption-line ranges | `server.py:1959-1963` |
| `result` (align) | `/align/stream` | `timed`, `cached`, `failed` (counts of request indices) | yes | Exactly one per finished request | The "done" status | `server.py:1975`, `server.py:2084` |
| `delta` | `/ask/stream` | `text` | no | Appended in order | The answer as it streams | `server.py:1821-1822` |
| `answer_done` | `/ask/stream` | none | yes | — | End of an answer | `server.py:1855` |
| `error` | all four streams, and `/align/stream` | `status` (an HTTP status number), `error` (string); on `/align/stream` also `reason` (every value is in "Word timing (`/align/stream`, T-020)") and, for `whisper_missing`, `install_hint` | yes | The in-stream error layer (below) | Every error message the panel shows mid-stream | `server.py:1084-1085`, `:1221-1222`, `:1304`, `:1307`, `:1395`, `:1429`, `:1449`, `:1453`, `:1633`, `:1637`, `:1714`, `:1721`, `:1841`, `:1845` |

The plain-JSON `/summarize/cached` response also carries a `type`: `miss` or `result` (`server.py:1361`, `server.py:1368`).

### `partial` `kind` values

| `kind` | Meaning | Source |
|---|---|---|
| `key_point` | one key point, as the model writes it | `summarize.py:283-285` |
| `paragraph` | one paragraph; a model `heading` line is sent as a `paragraph` whose text starts `## ` | `summarize.py:286-294` |

`core.summarize_url` also produces an internal `kind` `meta` (`core.py:195-196`), which the server converts into the `meta` event and never sends as a `partial` (`server.py:1253-1258`).

## Errors

### The three layers

| Layer | Shape | When | Invariant | Blast radius | Source |
|---|---|---|---|---|---|
| Before a stream (and every non-stream route) | JSON `{"error": "<text>"}` with the HTTP status | Any refusal before `send_response(200)` on a stream route; every failure on a non-stream route | Status and text both matter: the extension maps the status, and shows the text for most statuses | `httpError`, `askError`, `speakError` in `background.js`; `options.js:105-107` | `server.py:941-953` and every `_send_json(<4xx/5xx>, …)` |
| Inside a stream | NDJSON line `{"type": "error", "status": <n>, "error": "<text>"}`, after a `200` | Any failure once the stream has started | `status` is the status the same failure would have had before the stream | Same mappers, fed `ev.status` and `ev.error` | lines listed under `error` in the NDJSON table |
| Stdlib | HTML page from `send_error`, **no CORS headers** (a CORS-bound caller can't read it) | Unsupported method (`501`), malformed request line or headers (`400`), over-long request line (`414`), over-long or too many headers (`431`), bad HTTP version (`505`) | Not our code; nothing in the extension handles these | The extension sees a failed fetch | Python `http.server`; `501` observed by the probe |

**Exception → status** (`_STATUS`, `server.py:711-717`): `BadUrlError` `400`, `NoTranscriptError` `422`, `TranscriptTooLongError` `413`, `ClaudeError` `502`, `TldrTimeoutError` `504`; any other `TldrError` `500` (`server.py:1183`, `:1302`, `:1630`, `:1838`). **Speech routes map differently:** `TldrTimeoutError` `504`, any other `TldrError` `502` (`server.py:1711`, `server.py:1889`). A non-`TldrError` exception becomes `500` `internal error`, and its detail goes only to the server log (`server.py:1306-1309`, `:1635-1638`, `:1718-1724`, `:1843-1846`, `:1892-1894`). **`TldrError` text is passed through as-is** (`str(exc)`), so every `TldrError` message below is interface text.

### Every status the server sends

| Status | Meaning here | Layer(s) | Source |
|---|---|---|---|
| `200` | success, and every started stream | — | throughout |
| `204` | `OPTIONS` | — | `server.py:967` |
| `400` | bad request field, invalid JSON, chunked body, bad URL, unknown model/voice/whisper setting | pre-stream, in-stream (`BadUrlError`, `unknown whisper setting`) | `server.py:997`, `:1002`, `:1099`, `:1131`, `:1148`, `:1154`, `:1160`, `:1162`, `:1165`, `:1222`, `:1346`, `:1357`, `:1466`, `:1469`, `:1472`, `:1476`, `:1746`, `:1750`, `:1752`, `:1757`, `:1760`, `:1764`, `:1860`, `:1863`, `:1867`, `_STATUS` |
| `401` | missing or wrong token | pre-stream | `server.py:991`, `:1021`, `:1107` |
| `404` | unknown path | pre-stream | `server.py:980`, `:1018` |
| `411` | no `Content-Length` | pre-stream | `server.py:1140` |
| `413` | body over the cap (or empty); question too long; transcript too long for one pass; captionless video over the transcription ceiling | pre-stream, in-stream | `server.py:1143`, `:1754`, `:1395`, `_STATUS` |
| `415` | not `application/json` | pre-stream | `server.py:1135` |
| `422` | no transcript | pre-stream (`/summarize`), in-stream | `_STATUS`, `server.py:1810` |
| `429` | a slot pool is full (the server's own concurrency limit, not YouTube's) | pre-stream, in-stream (a slot that couldn't be retaken within `RETAKE_BUDGET`) | `server.py:1063`, `server.py:1084` |
| `499` | a transcription stopped while it was still waiting for the transcription permit | in-stream | `server.py:243-248`, `server.py:265-266` |
| `500` | unexpected failure, or a plain `TldrError` (YouTube rate-limiting included: it is a plain `TldrError`, `metadata.py:66-76`) | pre-stream, in-stream | `server.py:1183`, `:1307`, `:1454`, `:1637`, `:1721`, `:1723`, `:1845`, `:1894`, `server.py:218` |
| `501` | unsupported method (stdlib) | stdlib | probe |
| `502` | model call failed; any non-timeout speech failure | pre-stream (`/summarize`, `/speak`, `/preview`), in-stream | `_STATUS`, `server.py:1711`, `server.py:1889` |
| `503` | Piper TTS not installed or not usable | pre-stream | `server.py:1480`, `server.py:1871` |
| `504` | a subprocess or budget timed out | pre-stream, in-stream | `_STATUS`, `server.py:1429`, `server.py:1711`, `server.py:1889` |

### What the extension does with a status

For the status → copy mapping itself see the extension section (`httpError`, `askError`, `speakError`). From the server's side, what matters is **which server text reaches the user verbatim**:

| Mapper | Statuses whose server text is **replaced** by fixed copy | Every other status | Used for | Source |
|---|---|---|---|---|
| `httpError` | `401`, `502`, `504` | server `error` text shown if non-empty | `/summarize/stream`, `/segments/stream` | `background.js:645-655` |
| `askError` | `401`, `413`, `504` | server text shown if non-empty | `/ask/stream` | `background.js:514-521` |
| `speakError` | `401`, `504` | server text shown if non-empty | `/speak/stream`, `/preview` from the panel | `background.js:565-571` |
| Options preview | `401` | server text shown if non-empty | `/preview` from Options | `options.js:105-107` |
| (none) | — | never shown | `/summarize/cached` (any non-`200` is a miss, `background.js:187-200`), `/transcribe/stop` (response ignored, `background.js:305-313`) | — |

### Server error text the extension can show (interface copy)

Strings in `server.py`. "Shown" means it can reach the user verbatim through the mappers above; a backtick in the text is rendered as `<code>` by the panel (extension section).

| Text (exact) | Status | Route(s) | Shown? | Source |
|---|---|---|---|---|
| `Already working on 6 summaries, key moments or questions. Try again in a moment.` (the number is `CLAUDE_LIMIT`) | `429` | `/summarize/stream`, `/segments/stream`, `/ask/stream`, `/summarize` | yes | `server.py:642-644` |
| `Already reading 2 summaries aloud. Try again when one finishes.` (the number is `SPEECH_LIMIT`) | `429` | `/speak/stream`, `/preview`, `/speak` | yes | `server.py:645-647` |
| `missing or invalid token` | `401` | every POST | no (replaced) | `server.py:991`, `:1021`, `:1107` |
| `not found` | `404` | unknown paths | yes (`Server error` fallback shows it) — only reachable with a wrong `serverUrl` path or an older server | `server.py:980`, `:1018` |
| `chunked transfer not supported` | `400` | every POST | yes, but the extension never sends chunked | `server.py:1131` |
| `expected application/json` | `415` | every POST | yes, not reachable from the extension | `server.py:1135` |
| `Content-Length required` | `411` | every POST | yes, not reachable from the extension | `server.py:1140` |
| `request body too large` | `413` | every POST (also sent for an empty body) | yes on summarize/segments/speak; replaced on ask | `server.py:1143` |
| `invalid JSON` | `400` | every POST | yes | `server.py:1148` |
| `missing 'url'` | `400` | `/summarize*`, `/segments/stream`, `/summarize/cached`, `/ask/stream` | yes | `server.py:1154`, `:1750` |
| `ratio must be a number` | `400` | same as `ratio` | yes | `server.py:1160` |
| `ratio must be in (0, 1]` | `400` | same | yes | `server.py:1162` |
| `invalid lang` | `400` | same as `lang` | yes | `server.py:1165`, `:1760` |
| `unknown model` | `400` | every POST except `/transcribe/stop` | yes | `server.py:1099` |
| `expected a JSON object` | `400` | `/transcribe/stop`, `/summarize/cached` | no (both ignored) | `server.py:997`, `:1346` |
| `bad video url` | `400` | `/transcribe/stop`, `/summarize/cached` | no (both ignored) | `server.py:1002`, `:1357` |
| `unknown whisper setting` | `400` in-stream | `/summarize/stream` | yes | `server.py:1222` |
| ``This video is <length> long and has no captions. Transcribing its audio is limited to <N> minutes of video (`tldw config set whisper-max-minutes N` to change that).`` | `413` in-stream | `/summarize/stream` | yes | `server.py:1395-1399` |
| `Transcribing this video is taking longer than expected. It may still finish; try again in a few minutes.` | `504` in-stream | `/summarize/stream` | **no** — `httpError` replaces every `504` with its summarizing copy (follow-up) | `server.py:1429-1431`, `background.js:653` |
| `Transcription produced nothing usable.` | `500` in-stream | `/summarize/stream` | yes | `server.py:1453-1454` |
| `Transcription cancelled.` | `499` (permit wait) or `500` (see the pass-through table) | `/summarize/stream` | yes | `server.py:245`, `server.py:248` |
| `Whisper is installed but could not be loaded. Try reinstalling it: pipx inject youtube-tldw faster-whisper` | `500` in-stream | `/summarize/stream` | yes | `server.py:270-272`, `whisper_transcribe.py:62` |
| `Transcribing this video failed unexpectedly.` | `500` in-stream | `/summarize/stream` | yes | `server.py:279` |
| `Could not start transcribing this video.` | `500` in-stream | `/summarize/stream` | yes | `server.py:293` |
| `This video has no subtitles, so there's no transcript to answer from. If it was summarized from its audio, that transcript has since expired -- summarize it again to rebuild it, then ask.` | `422` in-stream | `/ask/stream` | yes | `server.py:1810-1815` |
| `invalid body` | `400` | `/ask/stream`, `/preview` | yes | `server.py:1746`, `:1860` |
| `missing 'question'` | `400` | `/ask/stream` | yes | `server.py:1752` |
| `question too long` | `413` | `/ask/stream` | no (replaced by `askError`) | `server.py:1754` |
| `history must be a list` | `400` | `/ask/stream` | yes | `server.py:1757` |
| `missing title/channel/summary` | `400` | `/speak`, `/speak/stream` | yes | `server.py:1466` |
| `key_points must be a list of strings` | `400` | `/speak`, `/speak/stream` | yes | `server.py:1469` |
| `voice must be a string` | `400` | `/speak*`, `/preview` | yes | `server.py:1472`, `:1863` |
| `internal error` | `500` | every route with a handler | yes | `server.py:1307`, `:1637`, `:1721`, `:1723`, `:1845`, `:1894` |

### Pass-through `TldrError` text reachable from a route

Raised in other modules and sent as `str(exc)`. Routes: **S** = `/summarize` and `/summarize/stream`, **G** = `/segments/stream`, **A** = `/ask/stream`, **P** = `/speak`, `/speak/stream` and `/preview`, **T** = the transcription job behind `/summarize/stream`. S, G and A reach the YouTube-fetch rows only when no cached or page transcript was usable.

| Text (exact) | Class → status | Routes | Source |
|---|---|---|---|
| `No URL provided.` | `BadUrlError` → `400` | S, G, A | `urls.py:29` |
| `URL must start with https://` | `BadUrlError` → `400` | S, G, A | `urls.py:39` |
| `Unsupported host '<host>'. Only youtube.com / youtu.be URLs are allowed.` | `BadUrlError` → `400` | S, G, A | `urls.py:42-44` |
| `Could not find a video id in the URL. Use a normal watch / youtu.be / shorts link (not a playlist).` | `BadUrlError` → `400` | S, G, A | `urls.py:53-56` |
| `That doesn't look like a single YouTube video URL.` | `BadUrlError` → `400` | S, G, A | `urls.py:59` |
| `YouTube is rate-limiting this machine (HTTP 429) after too many requests in a short time. It clears on its own — wait a few minutes and try again. Videos summarized recently are cached and unaffected.` | `TldrError` → `500` | S, G, A | `metadata.py:71-75` |
| `Could not read video metadata from yt-dlp.` | `TldrError` → `500` | S, G, A | `metadata.py:91` |
| `This video has no subtitles or auto-captions, so there's nothing to summarize. (Try a different video.)` | `NoTranscriptError` → `422` | S, G, A (A replaces it with its own text, `server.py:1805-1815`) | `metadata.py:121-124` |
| `Refusing to fetch a caption track from an unexpected host.` | `TldrError` → `500` | S, G, A | `metadata.py:165` |
| `Caption download failed (HTTP <code>).` | `TldrError` → `500` | S, G, A | `metadata.py:177` |
| `Caption download failed: <error>` | `TldrError` → `500` | S, G, A | `metadata.py:179` |
| `Caption track is implausibly large; refusing it.` | `TldrError` → `500` | S, G, A | `metadata.py:181` |
| `yt-dlp did not produce a subtitle file.` | `TldrError` → `500` | S, G, A | `metadata.py:233` |
| `Caption data was not valid json3.` / `Caption data contained no readable cues.` / `Subtitle file contained no readable cues.` / `Subtitle file produced no usable text.` | `NoTranscriptError` → `422` | S, G, A | `transcript.py:71`, `:87`, `:154`, `:197-198`, `:205` |
| ``This transcript is too long for the browser flow; use the `tldw` CLI for very long videos.`` | `TranscriptTooLongError` → `413` | S | `core.py:189-192` |
| `Claude selected no usable segments.` | `TldrError` → `500` | G | `spans.py:54` |
| `Claude selected no segments.` | `TldrError` → `500` | G | `summarize.py:442` |
| ``Claude returned no output. Are you logged in? Try `claude` once interactively to confirm your session.`` | `ClaudeError` → `502` | S, G, A (502 replaced by `httpError`; shown by `askError`) | `claude_client.py:41-44` |
| `Could not parse Claude's response envelope.` / `Claude reported an error: <detail>` / `Claude returned an empty result.` / `The LLM command returned no output.` | `ClaudeError` → `502` | S, G, A | `claude_client.py:48`, `:51`, `:54`, `:62` |
| `` `<cmd>` failed: <message> `` / `` `<cmd>` failed (exit <n>): <tail> `` | `ClaudeError` → `502` | S, G, A | `claude_client.py:95`, `:97`, `:484`, `:495` |
| `The model did not return valid structured output after a retry: <error>` | `ClaudeError` → `502` | S, G | `claude_client.py:644-646` |
| `` `<cmd>` timed out after <seconds>s. `` | `TldrTimeoutError` → `504` | S, G, A, P, T | `claude_client.py:489`, `proc.py:92`, `proc.py:181` |
| `` `<cmd>` is not installed or not on PATH. `` | `TldrError` → `500` (S/G/A via yt-dlp or a custom backend), wrapped as `ClaudeError` → `502` by `ask_json`/`ask_text`; → `502` on P | S, G, A, P | `proc.py:94`, `proc.py:128`, `claude_client.py:598-599`, `:634-635` |
| `` `<cmd>` failed (exit <n>): <tail> `` (from `proc`) | `TldrError` → `500`; `502` on P | S, G, A, P, T | `proc.py:99-101`, `proc.py:186` |
| ``Refusing to run `<cmd>` through cmd.exe with an argument containing shell metacharacters: '<arg>'`` | `TldrError` → `500` (Windows only) | any subprocess | `proc.py:58-61` |
| `Unknown voice: '<voice>'` | `TldrError`, sent as `400` by the validator | P | `audio.py:69`, `server.py:1474-1476`, `server.py:1865-1867` |
| ``Text-mode --render-audio needs Piper TTS. Install it with `pipx inject youtube-tldw piper-tts` (or `pip install piper-tts`).`` | `TldrError`, sent as `503` | P | `audio.py:198-202`, `server.py:1478-1480` |
| `Piper TTS is installed but not usable on this machine (<machine>): <reason>. Speech needs Piper's native espeak phonemizer, which has no wheel for every platform — Windows on ARM has none today. Everything except text-to-speech still works.` | `TldrError`, sent as `503` | P | `audio.py:210-216` |
| `Couldn't download the voice “<model>” after 3 tries (network/SSL error: <last line>). Check your connection and try again.` | `TldrError` → `502` | P | `audio.py:260-264` |
| `yt-dlp produced no audio for this video. It may be live, private, age-restricted, or longer than the size limit.` | `TldrError` → `500` | T | `whisper_transcribe.py:147-150` |
| `Transcription cancelled.` | plain `TldrError` → **`500`** (not `499`: only the permit wait raises `_Cancelled`) | T | `whisper_transcribe.py:190`, `:212`, `:229`, `:280`, `:292`; `server.py:274-277` |
| `Transcribing this video took longer than its time budget.` | `TldrTimeoutError` → `504` (replaced by `httpError`) | T | `whisper_transcribe.py:231-233` |
| `Whisper produced no text -- the audio may be silent or inaudible.` | `NoTranscriptError` → `422` | T | `whisper_transcribe.py:249-251` |
| `faster-whisper is not installed. …` (multi-line, with the install hint) | `TldrError` → `500` | T (only if faster-whisper vanishes between the `is_available` check and the run) | `whisper_transcribe.py:65-73`, `server.py:1379` |

Not reachable from a route: `timing.py` duration errors (the only server use swallows them, `server.py:1489-1493`), `naming.py:69`, `audio.py:227`, `audio.py:336`, `proc.require` (`proc.py:27-35`, CLI only).

## Limits, pools and slots

| Limit | Value | Invariant | Blast radius | Source |
|---|---|---|---|---|
| `CLAUDE_LIMIT` | 6 concurrent claude-pool slots (summaries, key moments, questions, prefetch) | Busy is a `429` naming the limit, never a queue | Every summarize, segments and ask request | `server.py:560`, `server.py:642-644` |
| `SPEECH_LIMIT` | 2 concurrent speech-pool slots (reading aloud, synthesizing a preview) | The two pools never refuse each other's routes (`tests/test_server.py:2524`) | Listen and voice previews | `server.py:561`, `server.py:645-647` |
| `PREFETCH_RESERVE` | prefetch never takes the last 2 claude slots (1 when the caller is handing over its own slot) | Background work is never why a click gets 429 | Key-moment prefetch | `server.py:562`, `server.py:465-466` |
| `RETAKE_BUDGET` | 30 s to get a released slot back, else an in-stream `429` | A wait never stalls without end | Transcription and prefetch waiters | `server.py:563`, `server.py:1065-1088` |
| `_tx_permit` | 1 transcription at a time, machine-wide; separate from both pools | A transcription never holds a claude slot while it runs | Captionless videos | `server.py:65`, `server.py:243-258`, `server.py:1424` |
| `MAX_TRANSCRIBE_MS` | 2 h, overridden by config `whisper_max_minutes` (bools and junk ignored); checked against yt-dlp's duration, never the client's | — | The `413` transcription refusal | `server.py:70`, `server.py:125-133`, `server.py:1385-1400` |
| Transcription budget | `max(TRANSCRIBE_FLOOR_S (300 s), estimate × TRANSCRIBE_SLACK (3.0))`; a request narrates for `budget × 2 + 300 s` before its `504` | — | Long videos | `server.py:73-74`, `server.py:217`, `server.py:115-122` |
| `MAX_BODY_BYTES` | 16 KiB | Default cap | `/transcribe/stop`, `/preview`, `/summarize/cached`, `/segments/stream` | `server.py:542` |
| `MAX_SPEAK_BYTES` | 64 KiB | — | `/speak`, `/speak/stream` | `server.py:543` |
| `MAX_ASK_BYTES` | 64 KiB | Set in two places (see above) | `/ask/stream` | `server.py:544` |
| `MAX_SUMMARIZE_BYTES` | 8 MiB (room for the page's caption track) | — | `/summarize`, `/summarize/stream` | `server.py:548` |
| `SINGLE_PASS_CHARS` | 350,000 transcript characters; more is `413` on the browser routes | A click never triggers a map-reduce | `/summarize*` | `summarize.py:17`, `core.py:186-192`, `server.py:1177`, `server.py:1266` |
| `REQUEST_TIMEOUT` | 120 s per summarize model call | — | `/summarize*` | `server.py:682` |
| `SEGMENTS_TIMEOUT` | 300 s per selection call, and the longest a request waits on a prefetch | — | `/segments/stream`, prefetch | `server.py:683`, `server.py:1528` |
| `SPEAK_TIMEOUT` | 600 s for one whole synthesis | — | `/speak*`, `/preview` | `server.py:687` |
| `ASK_TIMEOUT` | 300 s per answer | — | `/ask/stream` | `server.py:549` |
| `ask.MAX_QUESTION_CHARS` / `MAX_HISTORY_TURNS` / `MAX_HISTORY_CHARS` | 2,000 / 12 / 4,000 | — | `/ask/stream` | `ask.py:17-19` |
| `MAX_ALIGN_WINDOWS` / `MAX_ALIGN_WINDOW_MS` | 200 windows / 12,000 ms each: at most 40 minutes of audio per request | Checked before any work | `/align/stream` | `align.py:30-31` |
| `MAX_WORDS_PER_WINDOW` / `MAX_WORD_CHARS` | 200 / 64 | Enforced on the way out and on every cache read | `/align/stream`, store 13 | `align.py:32-33` |
| `_align_lock` | 1 alignment at a time, process-wide (a non-blocking acquire; a second request gets an in-stream `429` `busy`) | Bounds parallel YouTube fetches; a held lock is never released by the refused request | `/align/stream` | `server.py:72`, `server.py:1993` |
| `ALIGN_PERMIT_WAIT_S` | 20 s waiting for `_tx_permit`, with a `progress` line about every 5 s; then an in-stream `429` `busy` | Alignment is an improvement, so it never queues behind a long transcription | `/align/stream` | `server.py:78`, `server.py:2037-2049` |
| `ALIGN_BUDGET_S` | 600 s for the **whole** request from the moment `_align_lock` is taken (download, permit wait, model load and windows); no window starts with less than `ALIGN_WINDOW_DEADLINE_S` (30 s) left, and the rest are counted `failed` | The server ends within about 630 s, inside the extension's 660 s timer. While it runs it holds `_tx_permit`, so a queued whole-video transcription can wait that long | `/align/stream`, transcriptions | `server.py:76-77`, `server.py:2000` |
| `ALIGN_DOWNLOAD_TIMEOUT` | 300 s for the one audio download; no retry | — | `/align/stream` | `server.py:75` |
| `ALIGN_CUT_TIMEOUT_S` | 15 s per ffmpeg window cut; a timed-out cut fails that window and the next one runs | — | `/align/stream` | `align.py:35` |
| `ALIGN_COOLDOWN_S` | 600 s with no YouTube fetch for alignment after a YouTube 429; in memory, so a restart clears it; alignment only | — | `/align/stream` | `server.py:79` |

**Routes that take no slot:** `GET` `/health`, `GET` `/voices`, `OPTIONS`, `/transcribe/stop`, `/summarize/cached`, `/align/stream` (its own lock and the permit instead), and `/preview` when the preview is already memoized (`server.py:985-1012`, `server.py:1872-1876`, as of the Tier 1 docs; after T-020 the chain is `server.py:995-1036` and the memoized preview is in `_run_preview`, `server.py:2098`).

**Logged, not returned:** every refusal prints `busy: <pool> <in_use>/<limit>` (`server.py:1062`, `server.py:1083`); startup prints the limits line (`server.py:654-656`, `server.py:1943`).

## Streaming behaviour

| Behaviour | Invariant | Blast radius | Source |
|---|---|---|---|
| One connection per response (HTTP/1.0); a stream ends when the socket closes | The extension reads to EOF and treats a stream with no terminal event as "ended unexpectedly" | Every stream | `server.py:929`; `background.js:284-286` |
| Waiting on a key-moment prefetch: a progress heartbeat every **2 s**, sent only when the message changes | — | `/segments/stream` | `server.py:1517-1536` |
| Waiting on a transcription: narrated every **0.5 s** from the job's published progress, mapped to 10–70 % | A client hanging up stops the narration, never the job | `/summarize/stream` | `server.py:1427-1447` |
| Getting a released slot back: one `progress` line `Waiting for a free slot (<pool> <in_use>/<limit>)…`, then up to `RETAKE_BUDGET` | Ends in the slot or an in-stream `429` | `/summarize/stream`, `/segments/stream` | `server.py:1065-1088` |
| **No keepalive during a summary or an answer:** between `progress` lines the stream is silent while the model works; `creep: true` tells the client to ease the bar forward | Client timeouts must outlast the model call | `/summarize/stream`, `/ask/stream` | `server.py:1205-1207`, `core.py:197-198` |
| Hang-up handling: `/speak/stream` and `/ask/stream` guard writes (`OSError` sets `gone`, the synthesis or model process is stopped); `/summarize/stream` and `/segments/stream` do **not** guard `emit`, so a hang-up surfaces as an `OSError` in the handler | The transcription is kept outside the request for exactly this reason | Server log noise on a closed tab | `server.py:1674-1686`, `server.py:1777-1786`, `server.py:1821-1824`, `server.py:1201-1203`, `server.py:1503-1505`, `server.py:57-59` |

## Compatibility mechanisms in use today

The narrative and procedure are in `api.md`. This is the inventory of the mechanisms the code already relies on.

| Mechanism | Invariant | Who depends on it | Source |
|---|---|---|---|
| New behaviour on a **new route**: `/summarize/cached` instead of a flag, so an older server's `404` reads as a miss | An unknown route must stay a `404`, and the extension must keep treating a non-`200` probe as a miss | Newer extension against an older server | `server.py:1341-1343`, `background.js:187-200` |
| New behaviour behind an **opt-in body flag**: `stream_audio` | Absent keeps the single `audio` event | Older extension against a newer server | `server.py:1657-1658`, `background.js:355-356` |
| Absent `whisper` means the old behaviour exactly (`off`) | — | Older extensions | `server.py:136-160` |
| `page.cues` preferred, `page.captions` still parsed | — | Older extensions | `server.py:809-821` |
| Unknown keys ignored, except an unknown `model` (`400`), `voice` (`400`) or `whisper` (in-stream `400`) | Values that end up in argv or cost CPU are refused, not defaulted | Every client | `server.py:1090-1100`, `server.py:1470-1476`, `server.py:152-160` |
| `/health` reports `version` | Nothing reads it today | — | `server.py:975` |
| Busy text from an older server still reads fine in the extension | The extension shows `429` detail verbatim | — | `background.js:649-651` |

There is no version prefix in any path, no version header, and no machine-readable schema (this inventory is the schema).

## CLI

### Dispatch

`main` dispatches on `argv[0]` **before** argparse, in this order: `config`, `usage`, `cache`, `serve`; anything else goes to the default `<url>` parser (`cli.py:589-610`). The console script is `tldw = "youtube_tldw.cli:main"` (`pyproject.toml:17-18`). Invariant: a new subcommand is added to this chain, and its name can never be a valid `<url>` (11-character ids and `https://` URLs). Blast radius: every script or doc that runs `tldw`.

| Subcommand | Parser | Invariant | Blast radius | Source |
|---|---|---|---|---|
| `serve` | `_serve_parser` | Runs `server.serve`; a `TldrError` (such as a non-loopback `--host`) prints `error: <text>` and exits `1` | Every user's daily start; onboarding docs | `cli.py:596-608`, `cli.py:301-311` |
| `config` | **none** (hand-parsed) | Accepts arbitrary keys | Settings read by the server and CLI | `cli.py:590-591`, `cli.py:314-367` |
| `usage` | `tldw usage` argparse | Read-only | — | `cli.py:592-593`, `cli.py:457-573` |
| `cache` | `tldw cache` argparse | Without `clean`, read-only | — | `cli.py:594-595`, `cli.py:376-440` |
| (default) `<url>` | `build_parser` | `tldw <url>` must keep working unchanged | The original CLI | `cli.py:610-618`, `cli.py:40-95` |

### Flags, every parser

| Flag | Parser | Type / default | Source |
|---|---|---|---|
| `url` (positional) | default | YouTube URL or bare id | `cli.py:47` |
| `--mode` | default | `text` \| `video`, default `video` | `cli.py:48-49` |
| `--render-audio` | default | flag | `cli.py:50-52` |
| `--voice` | default | `female`, `male`, or an `audio.VOICES` key; default `female` | `cli.py:53-56` |
| `--ratio` | default | float, `0 < r <= 1` (else `TldrError`) | `cli.py:57-59`, `cli.py:99-100` |
| `--max-length` | default | duration string, must be positive | `cli.py:60-62`, `cli.py:101-103` |
| `--lang` | default | default `en` | `cli.py:63` |
| `--burn-captions` | default | flag | `cli.py:64-65` |
| `--keep-source` | default | flag | `cli.py:66-67` |
| `--keep-intro` | default | int SECONDS; mutually exclusive with `--no-intro` | `cli.py:68-71` |
| `--no-intro` | default | flag | `cli.py:72-73` |
| `--no-badge` | default | flag | `cli.py:74-75` |
| `--no-banner` | default | flag | `cli.py:76-77` |
| `--no-end-card` | default | flag | `cli.py:78-79` |
| `--no-timestamps` | default | flag | `cli.py:80-81` |
| `--output-dir` | default | Path; default `TLDW_OUTPUT_DIR` > config `output_dir` > `~/Downloads/youtube-tldw/tldws` | `cli.py:82-84`, `cli.py:20-32` |
| `--llm-cmd` | default, `serve` | string; sets `TLDW_LLM_CMD` for the process | `cli.py:85-88`, `cli.py:278-279`, `cli.py:309-310`, `cli.py:599-600` |
| `--whisper-fallback` | default | flag | `cli.py:89-93` |
| `--version` | default | prints `tldw <version>`, exits `0` | `cli.py:94` |
| `--host` | `serve` | default `127.0.0.1`; must be `127.0.0.1`, `localhost` or `::1` | `cli.py:304`, `server.py:1935-1939` |
| `--port` | `serve` | int, default `8765` | `cli.py:305` |
| `--token` | `serve` | default: `TLDW_TOKEN`, then the token file; visible in the process list | `cli.py:306`, `cli.py:603` |
| `--allow-origin` | `serve` | pins one exact Origin; default allows any `chrome-extension://` | `cli.py:307-308`, `server.py:917-924` |
| `action` (positional, optional) | `cache` | `clean` | `cli.py:383-385` |
| `--dry-run` | `cache` | flag, with `clean` | `cli.py:386-387` |
| `--days` | `usage` | float | `cli.py:463-464` |
| `--by-video` | `usage` | flag | `cli.py:465-466` |
| `--file` | `usage` | path to a `usage.jsonl` | `cli.py:467` |
| `-h` / `--help` | every argparse parser | argparse built-in, exits `0` | argparse |

### `config` behaviour

| Form | Behaviour | Source |
|---|---|---|
| `tldw config` or `tldw config get` | Prints each stored key with `_` shown as `-`, then `effective output-dir` and `effective model`; exits `0` | `cli.py:338-348` |
| `tldw config set <key> <value…>` | `<key>` goes through the keymap (`output-dir` → `output_dir`, `whisper-model` → `whisper_model`, `whisper` → `whisper`, `whisper-max-minutes` → `whisper_max_minutes`), **otherwise any key is accepted with `-` turned into `_`**; the value is every remaining word joined by spaces and stored as a string; `output-dir` is made absolute; exits `0` | `cli.py:332-360` |
| Validation on `set` | Only for the hyphen spellings `whisper` (`off`/`ask`/`always`), `whisper-max-minutes` (positive integer) and `whisper-model` (`whisper_transcribe.VALID_MODELS`); a failure prints `error: <problem>` **to stdout** and exits `2` | `cli.py:318-330`, `cli.py:352-355` |
| `tldw config unset <key>` | Same key mapping; prints `removed` or `<key> was not set`; exits `0` | `cli.py:361-364` |
| Anything else | Usage line to stderr, exits `1` | `cli.py:365-367` |

Keys the code reads (a key outside this list is stored and ignored): `output_dir` (`cli.py:29`), `whisper_model` (`whisper_transcribe.py:78`), `whisper` (`server.py:152`), `whisper_max_minutes` (`server.py:126`), `llm_cmd` (`claude_client.py:210`, `cli.py:281`, `cli.py:346`), `ytdlp_cookies` (`metadata.py:53-54`). The store is `~/.config/youtube-tldw/config.json` (`config.py:12`); its format is in the data sections.

### Exit codes

| Code | When | Source |
|---|---|---|
| `0` | success: a finished `<url>` run, `config get/set/unset`, `usage`, `cache`, `serve` stopped with Ctrl-C, `--version`, `--help` | `cli.py:298`, `cli.py:348`, `:360`, `:364`, `:417`, `:440`, `:573`, `:605`; `server.py:1966-1967` |
| `1` | a `TldrError` in the `<url>` mode or in `serve`; `config` usage error; an uncaught non-`TldrError` exception (the Python default; for example a port already in use, or `--host ::1`, see follow-ups) | `cli.py:606-608`, `cli.py:613-615`, `cli.py:367` |
| `2` | `config set` validation failure; any argparse error (missing `url`, bad `--mode`, `--keep-intro` with `--no-intro`, …) in any parser | `cli.py:355`; argparse |
| `130` | Ctrl-C during a `<url>` run (`Aborted.` to stderr) | `cli.py:616-618` |

## Public module entry points

Functions and names imported across modules. Changing one of these signatures breaks the callers listed. Private names (`_x`) that are imported across modules are listed too, because they are depended on all the same.

| Entry point | Signature / shape | Callers | Invariant | Source |
|---|---|---|---|---|
| `cli.main` | `main(argv: list[str] \| None = None) -> int` | console script; `tests/test_cli.py` | Returns the exit code; never raises `TldrError` | `cli.py:576`, `pyproject.toml:18` |
| `server.serve` | `serve(host="127.0.0.1", port=8765, token=None, allow_origin=None) -> None` | `cli.py:602-604` | Refuses a non-loopback host with `TldrError` | `server.py:1933-1971` |
| `server._Server` | `_Server(addr, token, allow_origin)`, a `ThreadingHTTPServer` | `serve`; `tests/test_server.py:36` | `daemon_threads = True` | `server.py:1924-1930` |
| `server._stale_audio_age` | `() -> float` | `cli.py:399` (`tldw cache clean`) | The server and the CLI sweep with the same age | `server.py:77-86` |
| `server.startup_hygiene` | `() -> list` | `serve` | Never touches work in progress | `server.py:89-112` |
| `server.load_or_create_token` | `(explicit: str \| None) -> tuple[str, bool]` | `serve`; tests | explicit > file > new | `server.py:692-708` |
| `core.summarize_url` | `(url, ratio=None, lang="en", *, timeout=300.0, max_chars=None, on_progress=None, on_partial=None, _prefetched=None) -> Summary` | `server.py:1176`, `:1264` | Raises only the typed `TldrError` subclasses for expected failures | `core.py:154-203` |
| `core.select_segments` | `(url, ratio=None, lang="en", *, max_length_ms=None, timeout=300.0, on_progress=None, on_segment_ready=None, _prefetched=None) -> (meta, segments)` | `server.py:502`, `:1620` | Segments are `{start, end, label}` in seconds | `core.py:90-151` |
| `core.fetch_transcript` | `(video_id, lang="en", *, on_progress=None, pcts=(3, 7, 13)) -> (meta, cues)` | `server.py:1803`, `core.py` | A `NoTranscriptError` carries `.meta` | `core.py:56-87` |
| `core.Summary`, `core.SEGMENT_FINGERPRINT` | dataclass `(meta, result, cue_count, cues)`; 12-hex string | `server.py:1904`, `server.py:392` | The fingerprint moves with every input that shapes a selection | `core.py:45-53` |
| `summarize.summarize_text` | `(cues, channel, title, ratio, *, timeout, on_partial=None, on_progress=None) -> TextResult` | `core.py:199`, `cli.py:157` | — | `summarize.py:307-359` |
| `summarize.select_video_segments` | `(cues, channel, title, ratio, max_length_ms, *, timeout, on_progress=None, on_segment_found=None) -> VideoSelection` | `core.py:140`, `cli.py:197` | — | `summarize.py:362-447` |
| `summarize.SINGLE_PASS_CHARS`, `PROMPT_FINGERPRINT`, `LISTING_FORMAT`, `backend_label`, `TextResult`; and the private `_VIDEO_PROMPT_BODY`, `_VIDEO_PROMPT`, `_VIDEO_PROMPT_STREAM`, `_ratio_clause`, `_maxlen_clause` | constants and helpers | `server.py:538`, `:767`; `core.py:35-41`, `:197` | The private names feed `core.SEGMENT_FINGERPRINT`; renaming one breaks `core` at import | `summarize.py:17`, `:86-131`, `:134-170`, `:247` |
| `ask.stream_answer` | `(meta, cues, history, question, *, timeout=300.0, on_delta=None, session_id=None, on_session=None) -> str` | `server.py:1829-1832` | A stale session falls back to a full call | `ask.py:93-142` |
| `ask.MAX_QUESTION_CHARS` | `2_000` | `server.py:1753` | — | `ask.py:17` |
| `claude_client.use_model` / `current_model` / `MODELS` | context manager pinning the model on this thread; `{"opus": "opus", "sonnet": "sonnet"}` | `server.py` throughout; `test_claude_client.py:284-294` checks the extension's copies | Thread-local: a new thread must re-enter `use_model` | `claude_client.py:164-186` |
| `claude_client.backend_fingerprint` / `backend_info` / `is_claude_cli` | `() -> str` / `() -> dict` / `() -> bool` | `server.py:393`, `:765`, `:976`, `:1947`; `summarize.py:136` | `backend_info` never exposes the command | `claude_client.py:227-254` |
| `claude_client.ask_json` / `ask_text` / `stream_text` / `stream_ndjson` / `stream_ndjson_segments` | see source | `summarize.py:11`, `ask.py:14` | The prompt and transcript go on stdin, never argv | `claude_client.py:498-646` |
| `proc.run` / `proc.stream_filter` / `proc.require` / `proc._resolve` | `run(argv, *, stdin=None, timeout=None, cwd=None, check=True, env=None) -> ProcResult`; `stream_filter(argv, feed, *, timeout=None, block=65536)`; `require(*binaries)`; `_resolve(argv)` | `claude_client.py:31`, `audio.py:17`, `metadata.py:19`, `videomode.py:11`, `cli.py:283` | No shell, argv lists only | `proc.py:27-186` |
| `urls.canonical_video_id` | `(url: str) -> str` (raises `BadUrlError`) | `server.py`, `core.py`, `cli.py` | The allowlist every URL passes before a subprocess | `urls.py:22-60` |
| `txcache.get` / `put` / `get_summary` / `put_summary` / `get_segments` / `put_segments`, `CAPTIONS`, `CACHE_DIR`, `SUMMARY_DIR`, `SEGMENT_DIR`, `TTL_SECONDS` | see source | `server.py`, `cli.py:390-397` | Best-effort writes; a bad file is a miss | `txcache.py:21-164` |
| `audio.voice_list` / `resolve_voice` / `require_piper` / `stream_speech` / `build_spoken_script`, `VOICES`, `DEFAULT_VOICE`, `VOICE_DIR` | see source | `server.py`, `cli.py` | The voice is an allowlist key | `audio.py:44-74`, `:187-216`, `:281-327`, `:353-362` |
| `whisper_transcribe.transcribe_video` / `estimate_seconds` / `resolve_model` / `is_available`, `INSTALL_HINT`, `VALID_MODELS`, `_CACHE_DIR` | `transcribe_video(video_id, workdir, *, on_progress=None, should_cancel=None, deadline=None, model_size=None)` | `server.py:250`, `cli.py:146`, `cli.py:327`, `cli.py:392` | The CLI and the server share one orchestration | `whisper_transcribe.py:23-101`, `:261-294` |
| `usage.interaction` / `note_video` / `note_served` / `stats` / `read_rows`, `USAGE_FILE` | see source | `server.py`, `cli.py:459-573` | — | `usage.py:26`, `:124-227` |
| `hygiene.sweep_stale_audio` / `unlisted_voice_files` / `idle_voice_files` / `expired_entries` / `remove_under` / `ensure_voice_tracking` / `huggingface_whisper_models` / `human` / `_size`, `Removal`, `AUDIO_TMP_PREFIX` | see source | `server.py:99-111`, `server.py:236`, `cli.py:394-454` | `remove_under` never leaves its base | `hygiene.py:27-245` |
| `config.load` / `get` / `set_value` / `unset` / `restrict_to_owner` | see source | everywhere | — | `config.py:15-56` |
| Exception classes and `__version__` | `TldrError` and its subclasses `BadUrlError`, `NoTranscriptError`, `TranscriptTooLongError`, `ClaudeError`, `TldrTimeoutError` | `server.py:711-717` (the `_STATUS` map), everywhere | The class decides the HTTP status | `__init__.py:3-29` |

**Source text the Python tests depend on:** `tests/test_claude_client.py:284-294` finds `const MODELS = [` in `extension/background.js` and `extension/options.js` and compares it with `claude_client.MODELS`; rewording that declaration breaks the test. The JS harness markers are in the extension sections.

## Threads

### Real background jobs

| Job | Started | Invariant | Blast radius | Source |
|---|---|---|---|---|
| Transcription (`tldw-transcribe-<video id>`, daemon) | `_start_transcription` (target `_run`), from `/summarize/stream` when a captionless video is accepted or the policy is `always` | One job per (video, model); a second caller joins it; serialized by `_tx_permit`; owns its temp dir (prefix `hygiene.AUDIO_TMP_PREFIX`) and removes it; caches the transcript before anything else, whether or not anyone is listening; stoppable through `/transcribe/stop` | CPU for minutes; the transcript cache | `server.py:187-295`, thread start `server.py:288-289` |
| Key-moment prefetch (daemon, unnamed) | `_start_seg_prefetch` (target `_run`), after a summary (`server.py:1241`, `:1321`) and on a probe hit (`server.py:1366`) | Deduped by key under `_cache_lock`; takes a claude slot under the reserve or is skipped; always gives its slot back and sets its event, even on failure | Claude spend; `/segments/stream` waiters | `server.py:434-522`, thread start `server.py:519` |

### Sweeps (not threads)

| Sweep | When | Source |
|---|---|---|
| `startup_hygiene`: stale audio temp dirs, voice files the allowlist can't select | Once per `serve`, after the bind, synchronously; failures are printed, never fatal | `server.py:89-112`, `server.py:1954-1958` |
| `tldw cache clean` | On demand | `cli.py:394-417` |
| In-memory prune (`_prune`, 64 entries, TTL) and on-disk prune (`txcache._prune`, 500 files) | On every cache write | `server.py:298-306`, `txcache.py:95-99` |

### Per-call helper threads

| Helper | Lifetime | Source |
|---|---|---|
| One request thread per connection (`ThreadingHTTPServer`, `daemon_threads = True`) | One request | `server.py:1924-1925` |
| `claude` stdin writer (`_write`, in `_stream_deltas`) | One streamed model call | `claude_client.py:345-355` |
| `claude` stderr drainer (`_drain_stderr`, in `_stream_deltas`) | Same | `claude_client.py:361-368` |
| Deadline watchdog (`_Timer` firing `_expire`, in `_stream_deltas`; `_Timer` is a seam tests replace) | Same | `claude_client.py:296`, `:390-392` |
| Post-answer stdout drainer (`_drain_stdout`, in `_stream_deltas`) | After a complete streamed answer | `claude_client.py:450-456` |
| `stream_filter` feeder and stderr drainer (`_feed`, `_drain_err`) | One filter process (ffmpeg for speech, a custom backend) | `proc.py:140-161` |
| `stream_filter` watchdog (`threading.Timer` firing `_kill`) | Same | `proc.py:162-165` |

### Shared state across request threads

All of it lives in module globals and is guarded:

- **`_cache_lock`** (`server.py:37`) guards `_transcript_cache`, `_seg_cache`, `_seg_prefetch_events`, `_ask_sessions`, `_seg_progress` (`server.py:38-49`), `_tx_jobs` (`server.py:64`), and `_captionless_meta` (`server.py:852`).
- **`_preview_lock`** (`server.py:552`) guards `_preview_cache`.
- **Each pool's own `Condition`** guards its counter (`server.py:582`).
- **`_tx_permit`** (`server.py:65`).
- **`_align_lock`** (`server.py:72`) serialises `/align/stream`. **`_align_backoff_until`** (`server.py:73`), the rate-limit cooldown, is read and written only while holding it. `_tx_jobs` is read under `_cache_lock` before an alignment downloads anything.
- **The model** is thread-local (`claude_client.py:165`), so the prefetch thread re-enters `use_model` (`server.py:499-500`), and selection keys are built in the request thread (`server.py:376-394`).
- **Usage accounting** has its own lock and thread-local (`usage.py:27-28`).
- **Per-stream write locks** stop two threads interleaving NDJSON lines (`server.py:1672`, `server.py:1775`).

Invariant: a new piece of shared state takes `_cache_lock` (or its own lock). Blast radius: any cache race shows up as a selection paid for twice, or a waiter that never wakes.

## Server-side cross-cutting invariants

| Invariant | Blast radius if broken | Source |
|---|---|---|
| **Loopback-only bind:** `serve` refuses any host but `127.0.0.1`, `localhost`, `::1`, with ``Refusing to bind a non-loopback host. Hosting for others needs TLS and API billing — see docs/reviews/PLAN-extension.md.`` | The whole security model (ADR 0003): there is no TLS | `server.py:1935-1939` |
| **Bearer token on every POST route**, compared in constant time; the token is the only authentication | Anyone who can reach the port could spend the user's Claude plan | `server.py:955-960`, `server.py:990`, `:1020`, `:1106` |
| **CORS fails closed:** only a pinned Origin, or any `chrome-extension://` Origin; never a missing, `null` or web Origin; never `*` | Web pages could read responses | `server.py:917-924` |
| **Bodies are JSON with size caps**; chunked refused | Memory exhaustion from one request | `server.py:1129-1149`, `server.py:542-548` |
| **Argv allowlists:** the URL passes `canonical_video_id` before any subprocess; `model` is a `MODELS` key; `voice` is a `VOICES` key; `lang` is regex-bound; the backend command is operator config only; a `--resume` id only ever comes from the CLI's own output; no subprocess ever uses a shell | Command or argument injection into the executables the server runs: `yt-dlp`, `claude` (or the `TLDW_LLM_CMD` command), `ffmpeg`, and Piper's voice downloader `piper.download_voices` (`ffprobe` is run only by the CLI; the subprocess inventory is in the integrations section) | `urls.py:22-60`, `server.py:1090-1100`, `audio.py:64-70`, `server.py:688`, `claude_client.py:9-10`, `:205-213`, `:326-330`, `proc.py:1-6`, `proc.py:38-63` |
| **No detail of an *unexpected* exception reaches a client:** a non-`TldrError` exception becomes `internal error`, and its detail is printed to the server log only. This does not cover `TldrError`: its text is sent as-is to authenticated clients, and for a failed subprocess that text includes up to 3 lines of its stderr (or stdout) | Leaking internals; subprocess stderr (`yt-dlp`, `ffmpeg`, the backend command) is already client-visible by design | `server.py:1306-1309` and the other `except Exception` blocks; `proc.py:97-101` |
| **`/health` never exposes the operator's command** | `/health` is unauthenticated | `claude_client.py:238-248` |
| **The transcription ceiling uses yt-dlp's duration, never the client's** | A client could talk the server into hours of CPU | `server.py:1382-1384` |
| **Every subprocess has a timeout** | A silent child would hold a slot forever | `proc.py:66-102`, `proc.py:162-165`, `claude_client.py:370-392` |
| **The code's pointer to `docs/reviews/PLAN-extension.md`** (the module docstring and the refusal message above) **must not be moved or renamed** | The refusal text would point users at a missing file | `server.py:3`, `server.py:1938` |
| **The startup token line:** `serve` prints the token on every start | Screen shares and pasted logs leak it (known weakness, tracked by the Architect's follow-up) | `server.py:1959` |

## Extension message protocol

The extension has three scripts: `content.js` (the on-page panel), `background.js` (the MV3 service worker, or a Firefox event page) and `options.js` (the Options page). The content script and the worker talk in two ways:

- **Long-lived ports**, opened by `content.js` with `runtime.connect`. Each piece of work opens its own port:
  - the summarize port, `content.js:598`;
  - the speak port, `content.js:1074`;
  - the ask port, `content.js:1346`;
  - the preview port, `content.js:1448`;
  - the key-moments port, `content.js:2785`.
- **One-shot `tabs.sendMessage`**, from the worker to the tab, after a toolbar click (`background.js:54-68`).

There is **no messaging between `options.js` and `background.js`**. `options.js` calls the server itself (see "Extension → server HTTP calls"). The two only share `chrome.storage.local`.

The port message types below are the extension's internal protocol. They are **not** the server's NDJSON event types, even where the spelling is the same (`progress`, `result`, `error`). The NDJSON `ev.type` values that `background.js` reads are listed separately, in "Server NDJSON events read by background.js", and the server's own contract for them lives in the NDJSON section of this file.

### Port name

| Item | Invariant | Blast radius | Source |
|---|---|---|---|
| `tldw` | Every `runtime.connect` in `content.js` uses `{ name: "tldw" }`. The worker drops any port with a different name (`if (port.name !== "tldw") return;`). | If either side is renamed alone, every feature's messages go nowhere. The content side then sees only its own safety timer, or a disconnect. | `background.js:71`; `content.js:598`, `:1074`, `:1346`, `:1448`, `:2785` |

### Content → background port messages

The dispatcher is `background.js:72-82`. `stopSpeak` and `stopAsk` are not in the dispatcher. Each is handled by an extra listener that `handleSpeak` or `handleAsk` adds to the same port while its request runs.

| Item | Payload keys | Invariant | Blast radius | Source (sent → handled) |
|---|---|---|---|---|
| `summarize` | `url`, `videoId`, `refresh` (bool), `page` (see "The `summarize` message's `page` payload"), `transcribe` (bool) | Sent only after the page read finishes, and only if the run is still live and still owns the port (`if (!requestActive \|\| port !== myPort) return;`). `refresh: true` skips the worker's cache (`cache.has(videoId) && !refresh`). | Breaks every summary. `probe_checks.js:101-149` and the `render_checks.js:335-345` regexes match this message's shape. | sent `content.js:606-607`; handled `background.js:74` → `handleSummarize` `background.js:203-299` |
| `probeCache` | `url`, `videoId` | Sent first on an ordinary click, before the page is read. Refresh and an accepted transcription skip it (`content.js:676-677`). | If this goes away, every click reads YouTube's transcript panel, even for cached videos. Guarded by `probe_checks.js:93-101` and `render_checks.js:540-542`. | sent `content.js:677`; handled `background.js:75` → `handleProbe` `background.js:166-201` |
| `speak` | `videoId`, `voice`, `payload` (the whole result payload; the worker reads `title`, `channel`, `key_points` and `summary_md`), `stream` (bool: `mseSupported()`) | `stream: false` asks for one buffered clip. The worker sends `stream_audio: msg.stream !== false`. | Listen breaks. Firefox has no MSE `audio/mpeg` and relies on `stream: false` (`content.js:29-31`). | sent `content.js:1100-1104`; handled `background.js:76` → `handleSpeak` `background.js:316-411` |
| `stopSpeak` | none | The only thing that cancels synthesis. A plain disconnect deliberately does not (`background.js:331-337`). | If it is dropped, Stop and "Stop and discard" leave synthesis running for up to 600 s. Guarded by `render_checks.js:144-145`. | sent `content.js:366`, `:1113`; handled `background.js:335-337` |
| `getSegments` | `url` (`lastPayload.source_url`, falling back to `location.href`) | — | Play key moments breaks. | sent `content.js:2831-2834`; handled `background.js:77` → `handleSegments` `background.js:573-639` |
| `preview` | `voice` | The same type string is used in both directions (see the next table). | Voice preview in the panel breaks. | sent `content.js:1465`; handled `background.js:78` → `handlePreview` `background.js:525-563` |
| `ask` | `url` (`lastPayload.source_url`), `question`, `history` (`[{role, content}]`, without the new question: `chat.history.slice(0, -1)`) | `role` is `"user"` or `"assistant"` (`content.js:1338`, `:1362`). The transcript is never sent; the server already has it. | Follow-up questions break. | sent `content.js:1382-1385`; handled `background.js:79` → `handleAsk` `background.js:441-512` |
| `stopAsk` | none | The only thing that cancels an answer. A plain disconnect lets it finish (`background.js:451-456`). | If it is dropped, Stop and "Stop and discard" leave the model call running. Guarded by `render_checks.js:144-145`. | sent `content.js:367`, `:1395`; handled `background.js:454-456` |
| `stopTranscribe` | `url` | Travels over the live summarize port. The worker sends nothing back. It needs a token and a `url`, and otherwise does nothing (`background.js:303`). | A transcription can't be stopped, from the Stop button or from "Stop and discard". Guarded by `render_checks.js:395-404`. | sent `content.js:860` (called from `:371`, `:719`); handled `background.js:80` → `handleStopTranscribe` `background.js:301-314` |
| `align` | `url` (`location.href`), `windows` (`[{start_ms, end_ms}]`, whole numbers) | Sent once per toggle-on, on the mute session's **own** port (`alignPort`), so closing the panel doesn't drop it. Never carries the word list. The extension never retries on its own (T-020). | Word timing for "Cover listed words". | sent `content.js:2422-2423`; handled `background.js:81` → `handleAlign` `background.js:93-158` |
| `ping` | none | **Ignored on purpose** (`background.js:81`). It is sent every 20 s on the summarize, speak, ask and key-moments ports (the preview port has no ping and no panel timer, `content.js:1448-1465`), and it is what keeps the MV3 worker alive through a long fetch. A background close must leave it running (`content.js:353-356`). | If the pings stop, Chrome suspends the worker mid-fetch and the work is lost. Guarded by `render_checks.js:132-148`. | sent `content.js:681`, `:1075`, `:1347`, `:2786`; ignored `background.js:81` |

### Background → content port messages

The worker posts everything through `safePost` (`background.js:641-643`), which swallows errors from a port that has already closed.

On the **summarize port**, the content listener (`content.js:609-663`) treats **any type it doesn't recognise as the end of the run**. It falls through to `requestActive = false` (`content.js:653-655`) and renders nothing. So a new type on that port needs a handler above `content.js:653`. `render_checks.js:287` and `:546` guard the order for `partial` and `probe`.

On the other ports, an unknown type is ignored.

| Item | Port | Payload keys | Invariant | Blast radius | Source (sent → handled) |
|---|---|---|---|---|---|
| `probe` | summarize | `hit` (bool); `payload` when `hit` is true | Any failure is a miss: a 404 from an older server, `{"type":"miss"}`, a network error, a timeout or non-JSON. A hit without a `payload` is not trusted (`probeOutcome`, `content.js:580-584`). | If a miss became an error, a new extension would stop working against an older server. Guarded by `probe_checks.js:152-187` and `render_checks.js:528-551`. | sent `background.js:95`, `:191`, `:200`; handled `content.js:611-615` |
| `progress` | summarize | `message`, `percent`, `creep` | Remembered as `lastProgress` so the bar can be replayed after the panel is re-opened (`content.js:617`). | The loading bar. | sent `background.js:251-252`; handled `content.js:616-621` |
| `meta` | summarize | `meta`: `video_id`, `title`, `channel`, `source_url`, `original_length` | Starts the partial view. The panel renders `channel`, `original_length` and `title`, all escaped (`content.js:913-914`). | The streamed partial summary. | sent `background.js:255-257`; handled `content.js:622` → `onPartial` `content.js:933-950` |
| `partial` | summarize | `kind` (`key_point` or `paragraph`), `text` | Other `kind` values are dropped (`content.js:937-939`). Text is escaped (`content.js:926`, `:929`). | The streamed partial summary. | sent `background.js:272`; handled `content.js:622` → `onPartial` / `appendPartial` `content.js:923-950` |
| `transcribing` | summarize | `videoId`, `estimateS` (the panel reads neither; it uses only the type) | Adds the Stop control and swaps in the 2,460,000 ms safety timer (`content.js:623-638`). | If it is lost, a transcription can't be stopped, and the 160 s panel timer fires part-way through it. Guarded by `render_checks.js:379-387`. | sent `background.js:259-260`; handled `content.js:623-638` |
| `needsTranscription` | summarize | `offer`: `video_id`, `title`, `channel`, `duration_ms`, `estimate_s`, `model`, `installed`, `install_hint` | Ends this request. Accepting the offer sends a new `summarize` with `transcribe: true` (`content.js:851`). The panel reads `installed`, `install_hint`, `estimate_s` and `model` (`content.js:819-843`). | The no-captions flow. Guarded by `render_checks.js:367-374`, `:388-392`, `:417-420` and `:473-525`. | sent `background.js:266-270`; handled `content.js:639-652` → `showTranscribeOffer` `content.js:814-854` |
| `result` | summarize | `payload` (the server's `result` event, unchanged), `cached` (bool) | Also **built locally** from a probe hit: `{ type: "result", payload: m.payload, cached: true }` at `content.js:582`. `cached: true` drives the `" (cached)"` heading. | Every summary view. The keys the panel reads from `payload` are listed in "Result payload keys the panel reads". | sent `background.js:206` (worker cache hit, `cached: true`), `:277` (`cached: !!ev.cached`); built `content.js:582`; handled `content.js:656-658` |
| `error` | summarize | `error` (the user-facing text) | The text already says what to do next (see "User-facing message contracts"). Shown through `showError`, or through a toast when the panel was closed. | Every summarize failure. | sent `background.js:175`, `:211`, `:233`, `:280`, `:285`, `:289-291`, `:293-294`; handled `content.js:659-662` |
| `speakProgress` | speak | `message`, `percent` | — | Listen status. | sent `background.js:373`; handled `content.js:1079` |
| `audioChunk` | speak | `b64`, `first` (bool) | `first` starts a new MediaSource (`content.js:1164-1183`). | Streaming playback. | sent `background.js:377`; handled `content.js:1080` |
| `audioEnd` | speak | `dataUrl` (`data:audio/mpeg;base64,…`) | Ends the stream and enables the download button (`content.js:1086`). | The mp3 download, and the clip being restored on re-open. Guarded by `render_checks.js:241-242`. | sent `background.js:383`; handled `content.js:1081-1086` |
| `audio` | speak | `dataUrl`; `cached: true` when it came from `audioCache` (the panel ignores `cached`) | The single-clip path: the worker's audio cache, an older server, or `stream: false`. | Listen on Firefox, and replays. | sent `background.js:320`, `:388`; handled `content.js:1087-1088` |
| `speakError` | speak | `error` | Shown with `textContent` in `.audiostatus`, so backticks appear literally (see "Backtick rendering"). | Listen failures. | sent `background.js:325`, `:352`, `:391`, `:396`, `:402-406`; handled `content.js:1089-1090` |
| `askProgress` | ask | `message` | — | Ask status line. | sent `background.js:485`; handled `content.js:1351` |
| `askDelta` | ask | `text` | Appended, then re-rendered through `renderAnswer`, which escapes it (`content.js:1353-1357`). | Streamed answers. | sent `background.js:487`; handled `content.js:1352-1358` |
| `askDone` | ask | none | Pushes the answer into `chat.history` (`content.js:1362`). | Conversation history. | sent `background.js:490`; handled `content.js:1360-1364` |
| `askError` | ask | `error` | A partial answer is kept and an empty bubble is dropped (`content.js:1367-1368`). | Ask failures. | sent `background.js:445`, `:467`, `:493`, `:498`, `:504`, `:506-507`; handled `content.js:1365-1372` |
| `preview` | preview | `voice`, `dataUrl` | The panel plays the clip only if `m.voice === voice` (`content.js:1455`). | Voice preview. | sent `background.js:528`, `:554`; handled `content.js:1455-1458` |
| `previewError` | preview | `voice`, `error` | The panel does not check `voice` here (`content.js:1459`). | Voice preview failures. | sent `background.js:533`, `:548`, `:556-559`; handled `content.js:1459-1462` |
| `segProgress` | key moments | `message`, `percent` | — | Key-moments status. | sent `background.js:611`; handled `content.js:2791` |
| `segmentAdded` | key moments | `segment` (the panel reads `start` and `end`, in seconds; the server also sends `label`, `core.py:132-136`) | The first one starts playback and closes the modal with `keepSeg: true`, so the port stays open (`content.js:2866-2885`). | Streaming skip playback. | sent `background.js:613`; handled `content.js:2792-2798` |
| `segmentsDone` | key moments | `title`, `channel`, `source_url` (the panel reads none of them) | Sets `segsComplete` and patches the `"trimmed video"` note (`content.js:2801-2802`). | The end of skip playback. | sent `background.js:616-617`; handled `content.js:2800-2813` |
| `segments` | key moments | `segments` (each with `start` and `end`), `title` (unread) | The batch, cache or prefetch path: every clip at once (`content.js:2815-2818`). | Skip playback. | sent `background.js:620`; handled `content.js:2815-2818` |
| `segError` | key moments | `error` | Shown with `textContent`, so backticks appear literally. | Key-moments failures. | sent `background.js:577`, `:593`, `:623`, `:628`, `:632`, `:634`; handled `content.js:2819` |

| `alignProgress` | align | `message` | Not shown (the panel shows its own "Timed to the word: K of N."). | — | sent `background.js:135`; ignored by `content.js` |
| `alignWindow` | align | `i`, `words` (`[{s, e, w}]`), `cached` | Dropped unless the session, its port and the page's video id are all still the live ones. Words only feed `refineHit`; they never reach the DOM (tested). | Word ranges. | sent `background.js:137`; handled `content.js:2371-2385` |
| `alignDone` | align | `timed`, `cached`, `failed` | Ends the request; picks the "done" status. | The done statuses. | sent `background.js:141`; handled `content.js:2386-2390` |
| `alignError` | align | `status` (0 for a worker-side failure), `reason`, `error`, `installHint` | Ends the request. The panel picks its text by `reason`, then by `status` (a `404` with no reason is an older server). The worker adds `token` (no token, before any fetch), `unreachable`, `timeout` (its 660 s `ALIGN_TIMEOUT_MS` timer, or an abort) and `other` (a stream that ended with no `result` or `error`). Nothing is posted after a terminal event. A port disconnect before `alignDone` counts as `other` on the content side. | Every failure status. | sent `background.js:94-96` (the helper), `:105` (token), `:117` (plain JSON), `:145` (in-stream), `:149` (no terminal event), `:152-153` (abort, network); handled `content.js:2393-2394` |

`preview` is the one type name used in **both directions**. Content→background, it is `{type, voice}` (`content.js:1465`). Background→content, it is `{type, voice, dataUrl}` (`background.js:528`, `:554`).

### Background → content via `tabs.sendMessage`

These are sent through `send()` (`background.js:54-56`), which swallows rejections. Both go out right after `scripting.executeScript` injects `content.js` (`background.js:61`). The `window.__tldwInit` guard keeps `content.js` from installing its listener twice when it is injected again (`content.js:6-7`).

| Item | Payload keys | Invariant | Blast radius | Source (sent → handled) |
|---|---|---|---|---|
| `TLDW_INVOKE` | `url`, `videoId` | Sent only when `videoIdFromUrl(tab.url)` finds an 11-character id (`background.js:28-41`). The handler has three branches: re-show the stored `lastPayload` for the same video, re-attach to a run already in flight for it, or start a new run (`content.js:3205-3230`). | The toolbar button stops doing anything. | sent `background.js:67`; handled `content.js:3205-3230` |
| `TLDW_ERROR` | `error` | Sent when the tab is not a `/watch` or `youtu.be` URL. Rendered by `showError`. | The "open a video first" guidance. | sent `background.js:63-64`; handled `content.js:3231` |

### The `summarize` message's `page` payload

Built by `collectPageTranscript` (`content.js:538-565`) and `pageMeta` (`content.js:501-520`). The worker forwards it unchanged (`background.js:227`). The server reads it in `_page_transcript` (`server.py:785-834`) and logs `status` (`server.py:1246-1248`).

| Item | Shape | Invariant | Blast radius | Source |
|---|---|---|---|---|
| `title` | string | From `meta[name="title"]`, then the page `h1`. The server keeps the first 300 characters and falls back to `"(untitled)"`. | Summary title and file names. | `content.js:512-513`; `server.py:831` |
| `channel` | string | From the author `link`, then the channel-name link. The server keeps the first 200 characters and falls back to `"(unknown)"`. | Summary byline and file names. | `content.js:514-515`; `server.py:832` |
| `duration_ms` | integer, **sent only when it parses** | From `meta[itemprop="duration"]` (ISO 8601 `PT#H#M#S`). The server accepts only a number with 0 < value < 24 h. Without it, the last cue's end stands in for the length. | The last cue's end time, and therefore the duration. | `content.js:438-443`, `:517-518`; `server.py:805-808`, `:830` |
| `cues` | `[{start_ms, text}]`, **only on success** | Rows without a clock-like start or without text are dropped on the client (`content.js:479`). The server re-validates every row (`transcript.py:104-127`). | Whether the server can summarize without contacting YouTube. Guarded by `page_transcript_checks.js:180-232`. | `content.js:474-482`, `:561`; `server.py:809-815` |
| `status` | `no-transcript-button`, `panel-empty`, `ok-panel (N cues)` or `error-<exception name>` | Always present. The `error-…` form carries **no** `title`, `channel` or `duration_ms` (`content.js:563`). The server only logs it. | Diagnosis only. Its strings are checked in `page_transcript_checks.js:184`, `:212`, `:217` and `:232`. | `content.js:544`, `:559`, `:561`, `:563`; `server.py:1246-1248` |

The server also accepts a legacy `captions` / `captions_format` pair (`server.py:816-821`). **The current extension does not send it.**

### Result payload keys the panel reads

This is the server's `result` event, used by `showResult`, `toMarkdown`, `speak` and the key-moments code. It is listed here because a rename on the server breaks these reads without any error.

| Item | Read at | Use |
|---|---|---|
| `video_id` | `content.js:988`, `:1025`, `:1083`, `:1101`, `:1258`, `:2767`, `:3207` | Session caches and re-open matching |
| `title` | `content.js:963`, `:3133`, `:3199`; `background.js:344` | Heading, mp3 file name, Copy, speech |
| `channel` | `content.js:960`, `:3133`, `:3199`; `background.js:344` | Byline, mp3 file name, Copy, speech |
| `original_length` | `content.js:960`, `:3199` | Byline |
| `length_label` | `content.js:960`, `:3199` | Byline |
| `source_url` | `content.js:961`, `:988`, `:1383`, `:2833`, `:3201` | The "original" link, regenerate, ask, key moments |
| `transcript_source` | `content.js:3191-3193` | "transcribed from audio by Whisper (…)" note, shown when the value starts with `audio:` |
| `key_points` | `content.js:958`, `:3197`; `background.js:345` | Key points list, speech |
| `summary_md` | `content.js:966`, `:3201`; `background.js:345` (sent to the server as `summary`) | Summary body, speech |
| `rationale` | `content.js:967` | Footer note |
| `stats` (`videos`, `saved_hours`) | `content.js:1002`, `:1221-1230` | Sponsor ask threshold |
| `cached` | `background.js:277` | `" (cached)"` heading |

### Server NDJSON events read by background.js

These are **server** event types, read as `ev.type` in `background.js`. They are not port messages; the worker turns each one into the port message shown. The server's contract for them is in the NDJSON section of this file.

| Item | Stream | Fields read | Forwarded as | Source |
|---|---|---|---|---|
| `progress` | `/summarize/stream` | `message`, `percent`, `creep` | `progress` | `background.js:250-252` |
| `meta` | `/summarize/stream` | `video_id`, `title`, `channel`, `source_url`, `original_length` | `meta` | `background.js:253-257` |
| `transcribing` | `/summarize/stream` | `video_id`, `estimate_s` | `transcribing` | `background.js:258-260` |
| `needs_transcription` | `/summarize/stream` | `video_id`, `title`, `channel`, `duration_ms`, `estimate_s`, `model`, `installed`, `install_hint` | `needsTranscription` (ends the stream) | `background.js:261-270` |
| `partial` | `/summarize/stream` | `kind`, `text` | `partial` | `background.js:271-272` |
| `result` | `/summarize/stream`; also the JSON body of `/summarize/cached` | the whole event, plus `cached` | `result` / `probe` | `background.js:189`, `:273-277` |
| `error` | all four streams | `status`, `error` | `error`, `speakError`, `askError` or `segError`, through the mapping functions | `background.js:278-280`, `:389-391`, `:491-493`, `:621-623` |
| `progress` | `/speak/stream` | `message`, `percent` | `speakProgress` | `background.js:372-373` |
| `audio_chunk` | `/speak/stream` | `mp3_base64` | `audioChunk` | `background.js:374-378` |
| `audio_end` | `/speak/stream` | none (the clip is joined from the chunks, as bytes) | `audioEnd` | `background.js:379-383`, `:415-429` |
| `audio` | `/speak/stream` | `mp3_base64` | `audio` | `background.js:384-388` |
| `progress` | `/ask/stream` | `message` | `askProgress` | `background.js:484-485` |
| `delta` | `/ask/stream` | `text` | `askDelta` | `background.js:486-487` |
| `answer_done` | `/ask/stream` | none | `askDone` | `background.js:488-490` |
| `progress` | `/segments/stream` | `message`, `percent` | `segProgress` | `background.js:610-611` |
| `segment_added` | `/segments/stream` | `segment` | `segmentAdded` | `background.js:612-613` |
| `segments_done` | `/segments/stream` | `title`, `channel`, `source_url` | `segmentsDone` | `background.js:614-617` |
| `segments` | `/segments/stream` | `segments`, `title` | `segments` | `background.js:618-620` |
| `progress` | `/align/stream` | `message` | `alignProgress` | `background.js:134-135` |
| `window` | `/align/stream` | `i`, `words`, `cached` | `alignWindow` | `background.js:136-138` |
| `result` | `/align/stream` | `timed`, `cached`, `failed` | `alignDone` | `background.js:139-142` |
| `error` | `/align/stream` | `status`, `reason`, `error`, `install_hint` | `alignError` | `background.js:143-145` |

The worker ignores any other `ev.type`, and any line that isn't valid JSON (`background.js:249`, `:371`, `:483`, `:609`). If a stream ends with no terminal event, the worker posts the "ended unexpectedly" error for that feature (`background.js:284-286`, `:395-397`, `:497-499`, `:627-629`).

### Matching timeouts between panel and worker

The panel keeps its own safety timer, so it can still say something if the worker goes quiet. The invariant is that **the worker's budget runs out first**, so the worker's more specific message is the one the user sees.

| Item | Worker budget | Panel timer | Holds today? | Source |
|---|---|---|---|---|
| `CLIENT_TIMEOUT_MS` (summarize) | 150,000 ms | 160,000 ms | Yes, except with `whisper: "always"`. The worker's budget is then `TRANSCRIBE_TIMEOUT_MS` (`background.js:218-219`), but the panel stays at 160,000 ms until a `transcribing` event arrives (`content.js:623-636`, `:691`), so the panel fires first and says to check `tldw serve` while the worker is still working (follow-up 10) | `background.js:16`; `content.js:691` |
| `TRANSCRIBE_TIMEOUT_MS` | 2,400,000 ms | 2,460,000 ms | Yes; guarded by `render_checks.js:405-406` and `:410-415` | `background.js:20`, `:218-219`; `content.js:636`, `:691` |
| `ASK_TIMEOUT_MS` | 300,000 ms (total) | 300,000 ms (idle, re-armed on every message) | Equal, not greater | `background.js:22`; `content.js:1419` |
| `SEG_TIMEOUT_MS` | 320,000 ms | 320,000 ms (not re-armed) | Equal, not greater | `background.js:21`; `content.js:2830` |
| `SPEAK_TIMEOUT_MS` | 600,000 ms (total; also used for `/preview`) | 185,000 ms (idle, re-armed on every message) | Different meaning: the panel's timer is an idle watchdog | `background.js:23`, `:538`; `content.js:1135-1142` |
| `/preview` (panel) | 600,000 ms (`SPEAK_TIMEOUT_MS`) | none: the panel's idle watchdog is armed only on the speak port, and the preview button resets only on a reply or a disconnect | No panel timer | `background.js:538`; `content.js:1078`, `:1135-1142`, `:1464` |
| `PROBE_TIMEOUT_MS` | 5,000 ms | covered by the summarize timer | — | `background.js:85` |
| `ALIGN_TIMEOUT_MS` (word timing) | 660,000 ms (the server's 600 s whole-request budget plus a minute) | 660,000 ms (`MUTE_SAFETY_MS`, not re-armed) | Equal, not greater. The server ends within about 630 s, so neither normally fires | `background.js:91`; `content.js:1873` |

## Extension → server HTTP calls

Every call builds its URL as `serverUrl.replace(/\/+$/, "") + route`. `serverUrl` is read from storage and never validated beyond "non-empty, or else the default" (`background.js:47`, `options.js:52`, `:70`, `:92-93`). Requests with a body send `Content-Type: application/json`.

**Server routes the extension never calls:** `/summarize` and `/speak` (the non-streaming forms). The CLI and older clients use them; see the routes table.

| Item | Method | Auth header | Caller | Request body keys | Response fields read | Invariant | Blast radius | Source |
|---|---|---|---|---|---|---|---|---|
| `/summarize/cached` | `POST` | `Authorization: Bearer <your-token>` | `background.js` `handleProbe` | `url`, `model`, `segment_ratio` | `resp.ok`; JSON `type === "result"` (the whole object becomes the payload) | Anything else is a miss, including a `404` from an older server and `{"type":"miss"}`. The 5 s abort is a miss too. `model` must be sent because the summary cache key depends on it (the backend fingerprint). `segment_ratio` is sent so that, on a hit, the key-moments prefetch checks its cache under the *segment* ratio (`_run_cached`: validation and key at `server.py:1350-1359`, prefetch at `:1364-1367`). The summary key uses the body's `ratio`, which the extension never sends, so it is `auto` (`server.py:748-770`). | Cache-before-read. Guarded by `probe_checks.js:152-187`. | `background.js:181-201` |
| `/summarize/stream` | `POST` | `Bearer` | `background.js` `handleSummarize` | `url`, `model`, `segment_ratio`, `refresh`, `page`, `whisper`, `transcribe` | when not ok: JSON `error`; otherwise the NDJSON events above | `transcribe === true` or `whisper === "always"` selects the 40-minute budget. | Every summary. | `background.js:218-234` |
| `/transcribe/stop` | `POST` | `Bearer` | `background.js` `handleStopTranscribe` | `url` | nothing (best effort, no timeout) | Skipped without a token or a `url`. | Stopping a transcription. | `background.js:301-314` |
| `/speak/stream` | `POST` | `Bearer` | `background.js` `handleSpeak` | `title`, `channel`, `key_points`, `summary` (from `payload.summary_md`), `voice`, `stream_audio` | when not ok: JSON `error`; otherwise the NDJSON events above | `model` is not sent. `stream_audio: false` means one `audio` event. | Listen. | `background.js:340-353` |
| `/ask/stream` | `POST` | `Bearer` | `background.js` `handleAsk` | `url`, `question`, `history`, `model` | when not ok: JSON `error`; otherwise the NDJSON events above | `segment_ratio` is read from settings (`background.js:443`) but not sent. | Follow-up questions. | `background.js:459-468` |
| `/preview` | `POST` | `Bearer` | `background.js` `handlePreview` | `voice` | when not ok: JSON `error`; otherwise the bytes (`arrayBuffer`), wrapped as `data:audio/mpeg;base64,…` | Cached per voice in the worker's memory. | Voice preview in the panel. | `background.js:540-554` |
| `/segments/stream` | `POST` | `Bearer` | `background.js` `handleSegments` | `url`, `model`, `ratio` (from the `segmentRatio` setting; **not** `segment_ratio`) | when not ok: JSON `error`; otherwise the NDJSON events above | `ratio` is `null` for `"auto"`. | Play key moments. | `background.js:585-594` |
| `/align/stream` | `POST` | `Bearer` | `background.js` `handleAlign` | `url`, `whisper`, `windows` | when not ok: JSON `error`, `reason`, `install_hint`; otherwise the NDJSON events above | Skipped without a token (`token`). Aborted on port disconnect and after 660 s. | Word timing. | `background.js:93-158` |
| `/health` | `GET` | **none** | `options.js` `load` | none | `backend.kind`. If it is set and isn't `"claude"`, the model picker is disabled and `#modelNote` explains why. | Every failure is ignored. There is no timeout. | Model picker state. | `options.js:51-66` |
| `/voices` | `GET` | **none** | `options.js` `load` | none | `resp.ok`; `voices` (an array of `{id, label}`), used only when it is a non-empty array | **Inserted with `innerHTML` and not escaped** (`options.js:30`). If it fails, `VOICE_FALLBACK` is used. | The Options voice list. | `options.js:68-75`, `:28-32` |
| `/preview` | `POST` | `Bearer`, using the token **typed in the field, saved or not** | `options.js` `preview` | `voice` | `resp.ok`, `resp.status` (`401`); when not ok: JSON `error`; otherwise `resp.blob()`, as an object URL | It uses the server URL in the field too, so a voice can be auditioned before saving. Cached per voice for the page's lifetime. | Options voice preview. | `options.js:83-120` |

## User-facing message contracts

The worker turns an HTTP status (from a non-ok response, or from a stream `error` event's `status`) into the text the user reads. It does this in three functions:

- **`httpError(status, detail)`**, for `/summarize/stream` and `/segments/stream`;
- **`askError(status, detail)`**, for `/ask/stream`;
- **`speakError(status, detail)`**, for `/speak/stream` and `/preview`.

`detail` is the server's `error` string. "Pass-through" below means the server's text is shown as-is when it is present. For those statuses, the server's `error` strings are interface text; they are listed in the server section of this file.

**The signature `(status, detail)` is a test contract.** `probe_checks.js:194-203` cuts each function out by `function <name>(status, detail)` and checks that a `429` shows the server's reason.

### httpError status mapping

| Item | Message text (exact) | Server detail | Invariant | Blast radius | Source |
|---|---|---|---|---|---|
| `401` | `Token mismatch. Re-check the token in the extension's Options.` | ignored | Names the fix. | Summary and key-moments token errors. | `background.js:646` |
| `413` | `This transcript is too long for the browser; use the tldw CLI.` | pass-through | — | — | `background.js:647` |
| `422` | `This video has no captions/transcript to summarize.` | pass-through | **Names no next step** (follow-up). | — | `background.js:648` |
| `429` | `Server busy, try again in a moment.` | pass-through: the server says which limit was hit | Guarded by `probe_checks.js:194-203`. | Busy messages. | `background.js:651` |
| `502` | ``The model call failed. If you're on the Claude CLI, make sure `claude` is logged in; otherwise check your configured model command.`` | ignored | — | — | `background.js:652` |
| `504` | `Summarizing timed out on the server. Try again, or use the CLI for long videos.` | ignored | Also shown for a **key-moments** timeout, where "Summarizing" is the wrong word (follow-up). | — | `background.js:653` |
| any other status | `Server error (<status>).` | pass-through | The fallback **names no next step** (follow-up). | — | `background.js:654` |

### askError status mapping

| Item | Message text (exact) | Server detail | Invariant | Blast radius | Source |
|---|---|---|---|---|---|
| `401` | `Token mismatch. Re-check the token in the extension's Options.` | ignored | — | — | `background.js:515` |
| `429` | `Server busy, try again in a moment.` | pass-through | Guarded by `probe_checks.js:194-203`. | — | `background.js:516` |
| `413` | `That question is too long.` | ignored | **Names no next step** in words (follow-up). | — | `background.js:517` |
| `422` | `This video has no transcript to answer from.` | pass-through | **Names no next step** (follow-up). | — | `background.js:518` |
| `504` | `That question took too long. Try again.` | ignored | — | — | `background.js:519` |
| any other status | `Couldn't answer (server error <status>).` | pass-through | The fallback **names no next step** (follow-up). | — | `background.js:520` |

### speakError status mapping

`speakError` also serves `/preview` (`background.js:548`).

| Item | Message text (exact) | Server detail | Invariant | Blast radius | Source |
|---|---|---|---|---|---|
| `401` | `Token mismatch. Re-check the token in the extension's Options.` | ignored | — | — | `background.js:566` |
| `429` | `Server busy, try again in a moment.` | pass-through | Guarded by `probe_checks.js:194-203`. | — | `background.js:567` |
| `503` | `Audio isn't available — Piper TTS isn't installed on the server.` | pass-through | **Names no next step** (no install command; follow-up). | — | `background.js:568` |
| `504` | `Audio generation timed out. Try again.` | ignored | — | — | `background.js:569` |
| any other status | `Audio failed (server error <status>).` | pass-through | The fallback **names no next step** (follow-up). | — | `background.js:570` |

### options.js status mapping

| Item | Message text (exact) | Server detail | Source |
|---|---|---|---|
| `401` | ``Token mismatch — check the token from `tldw serve`.`` | ignored | `options.js:105-106` |
| any other status | `Preview failed (<status>).` | pass-through | `options.js:107` |

`404` is never mapped to text. On `/summarize/cached` it means a miss (`background.js:187-200`, guarded by `probe_checks.js:155-158`).

### Fixed messages from the worker

| Item | Message text (exact) | Source |
|---|---|---|
| No token (probe, summarize, speak, ask, preview, segments) | ``No server token set. Open the extension's Options and paste the token from `tldw serve`.`` | `background.js:176`, `:212`, `:326`, `:446`, `:534`, `:578` |
| Not a video page (`TLDW_ERROR`) | `Open a YouTube video (a /watch page) first, then click TL;DW.` | `background.js:64` |
| Summary stream ended without a terminal event | `The summary stream ended unexpectedly. Try again.` | `background.js:285` |
| Audio stream ended without a terminal event | `Audio stream ended unexpectedly. Try again.` | `background.js:396` |
| Answer stream ended without a terminal event | `The answer stream ended unexpectedly. Try again.` | `background.js:498` |
| Key-moments stream ended without a terminal event | `The segment stream ended unexpectedly. Try again.` | `background.js:628` |
| Transcribe budget aborted | `Transcribing timed out. The transcript may still finish on the server — try again in a few minutes.` | `background.js:290` |
| Summarize budget aborted | `Summarizing timed out (over 150s). Try again, or use the tldw CLI for long videos.` | `background.js:291` |
| Speak budget aborted | `Audio generation timed out. Try again (first use downloads the voice).` | `background.js:403` |
| Ask budget aborted | `That question took too long. Try again.` | `background.js:504` |
| Preview budget aborted | `Voice preview timed out (first use downloads the voice).` (**names no next step**) | `background.js:558` |
| Key-moments budget aborted | `Finding key moments timed out. Try again.` | `background.js:632` |
| Network failure (summarize) | `Can't reach the tldw server. Is it running? In a terminal:  tldw serve` (two spaces before `tldw`) | `background.js:294` |
| Network failure (speak, ask, preview, segments) | `Can't reach the tldw server. Is it running?  tldw serve` (two spaces before `tldw`) | `background.js:406`, `:507`, `:559`, `:634` |

### Fixed messages from the panel and Options

| Item | Message text (exact) | Surface | Source |
|---|---|---|---|
| Summarize port lost | `Lost connection to the extension worker. Click TL;DW to try again.` | `showError` or toast | `content.js:667` |
| Panel safety timer, transcription | `Transcribing is taking longer than expected. It may still finish on the server — try again in a few minutes.` | `showError` or toast | `content.js:632-633` |
| Panel safety timer, summarize | ``This is taking too long. Make sure `tldw serve` is running, then try again.`` | `showError` or toast | `content.js:688` |
| No captions, Whisper not installed | `This video has no subtitles or auto-captions. TL;DW can transcribe the audio locally instead, but that needs Whisper, which isn't installed yet.` / ``Install it, then restart `tldw serve` and click TL;DW again:``, then the server's `install_hint` (fallback `pipx inject youtube-tldw faster-whisper`), then the `pip install faster-whisper` alternative | `.status.err` with `role="status" aria-live="polite"`, written directly into `.content` (not through `showError`); the hint is escaped with `esc()` | `content.js:819-829` |
| Speak or key-moments port lost | `Lost connection to the worker. Try again.` | `.audiostatus` | `content.js:1096`, `:2824` |
| Ask port lost | `Lost connection to the worker. Try again.` | `.askstatus` | `content.js:1379` |
| Audio idle watchdog | ``Audio is taking too long. Make sure `tldw serve` is running.`` | `.audiostatus` | `content.js:1140` |
| Ask safety timer | ``That question is taking too long. Is `tldw serve` running?`` | `.askstatus` | `content.js:1418` |
| Key-moments safety timer | ``Finding key moments is taking too long. Is `tldw serve` running?`` | `.audiostatus` | `content.js:2829` |
| No clips came back | `No key moments found.` (**names no next step**) | `.audiostatus` | `content.js:2806` |
| No player found | `Couldn't find the YouTube player to control.` (**names no next step**) | `.audiostatus` | `content.js:2848`, `:2869` |
| Listen stopped | `Stopped. Pick another voice and try again.` | `.audiostatus` | `content.js:1118` |
| Ask stopped | `Stopped.` (a confirmation, not an error) | `.askstatus` | `content.js:1403` |
| Background summary finished | `TL;DW summary ready — click to read` | toast | `content.js:657`, `:785` |
| Background offer waiting | `TL;DW needs an answer about this video — click to read` | toast | `content.js:647`, `:785` |
| Options: no token for preview | `Paste the token first.` | `#previewStatus` | `options.js:90` |
| Options: preview network failure | ``Can't reach the server. Is `tldw serve` running?`` | `#previewStatus` | `options.js:115` |
| Options: custom backend | `Your server is configured to use a custom model command, so this setting doesn't apply. Change the model where that command is configured (tldw config llm_cmd ... or TLDW_LLM_CMD).` (begins with a space). **The command it names doesn't work:** `config` accepts only `set`, `get` and `unset`, and anything else prints the usage line and exits 1 (`cli.py:349`, `cli.py:365-367`). The working form is `tldw config set llm_cmd …` (→ [T-012](../tasks/T-012.md)) | `#modelNote` | `options.js:60-63` |
| Options: saved | `Saved. Videos where Cover listed words is on use the new settings right away.` (cleared after 4 s; T-020 rev 5.6, AC18: the user is in this tab when a running Cover listed words session picks the change up) | `#status` | `options.js:185-186` |

### Backtick rendering

| Item | Invariant | Blast radius | Source |
|---|---|---|---|
| `showError` backtick → `<code>` | The message is **escaped first** with `esc()`, and only then is `` `x` `` turned into `<code>x</code>`. So a server `error` string can't inject markup. Only `showError` does this. | Every message shown in the panel's main error view: `error` port messages, `TLDW_ERROR`, and the panel's own safety and disconnect messages. | `content.js:863-868` |
| Every other surface uses `textContent` | Backticks show **literally** in `.audiostatus` (`content.js:3184`), `.askstatus` (`content.js:1435`), the toast (`content.js:785`) and the Options notes (`options.js:90`, `:105-107`, `:115`). So the no-token message and the `502` message show raw backticks when they arrive as `speakError`, `askError`, `previewError` or `segError`, or as a toast. | How those messages look. | as cited |

### Text the code depends on

| Item | Invariant | Blast radius | Source |
|---|---|---|---|
| `"trimmed video"` | `trimmedNote()` produces `` ` · <dur> trimmed video` ``, plus ` (<pct>%)` when the percentage is known. `refreshTrimmedNote()` inserts the note only if the header doesn't already contain `"trimmed video"`. Both strings must change together. | If they differ, the note is inserted again every time key moments finish. | `content.js:2962`, `:2976` |
| `" (cached)"` | Appended to the `#tldw-h` heading when `cached` is true. Nothing reads it back. It does become part of the dialog's accessible name, through `aria-labelledby="tldw-h"`. `cached` is true for the worker's cache (`background.js:206`), a server cache hit (`background.js:277`), a probe hit (`content.js:582`), and a re-open from a toast or a toolbar click (`content.js:794`, `:3207`). So it means "not freshly generated for this click". | The heading, and screen-reader announcements. | `content.js:956` |

### Every message names a next step

**Invariant:** every message the user reads names what to do next: retry, re-check the token, run `tldw serve`, use the CLI, or install something.

**Exceptions today**, recorded as facts with a follow-up:

- `httpError`: `422` and the fallback;
- `askError`: `413`, `422` and the fallback;
- `speakError`: `503` and the fallback;
- the preview timeout, `background.js:558`;
- `No key moments found.`, `content.js:2806`;
- `Couldn't find the YouTube player to control.`, `content.js:2848` and `:2869`;
- the Options custom-backend note (`options.js:60-63`), which names `tldw config llm_cmd`, a form that exits 1 (→ T-012);
- any pass-through server `detail` that names no next step itself, such as `internal error`, which the server emits as a stream error (`server.py:1307`).

## Word timing (`/align/stream`, T-020) and "Cover listed words"

Everything the hybrid caption-and-Whisper feature adds to the server's and the panel's contracts, in one place. The narrative is in [`api.md`](./api.md) (the route), [`data-model.md`](./data-model.md) (store 13 and the two storage keys) and [`ux.md`](./ux.md) (the journey). **Every string below is quoted verbatim and checked:** `test_contracts_md_quotes_every_status_string_verbatim` reads them from the code (between the `// T-020 status text: BEGIN`/`END` markers in `content.js`, the `# T-020 align text: BEGIN`/`END` markers in `align.py`, and `#whisperCoverHint` in `options.html`) and fails if one is missing here.

### The order of checks in `_run_align`

1. **Before the 200, as plain JSON:** auth (`401`) and the body (`_read_body`: `400`/`411`/`413`/`415`); the body is an object; `canonical_video_id(url)` (`400` `bad video url`); `parse_windows` (`400` naming the field); `_whisper_policy` (`400` for an unknown value; `503` `whisper_off` for `off`); `is_available()` and a readable fingerprint (`align.fingerprint()`, called `ALIGN_FINGERPRINT` in these docs; `503` `whisper_missing`, with `install_hint`); any window past `_max_transcribe_ms()` (`413` `too_long`). An oversized body is also a `413`, **with no `reason`**.
2. **Then the 200 NDJSON headers.** Exact duplicate windows are collapsed; every window found in store 13 is sent at once (`cached: true`). If none miss: `result`, done, with no ffmpeg, no lock, no permit and no YouTube call.
3. **Only if some windows miss, each as an in-stream `error`:** `shutil.which("ffmpeg")` (`503` `ffmpeg_missing`); the model on disk, `model_on_disk` (`503` `model_missing`, before any download; `503` `whisper_missing` if faster-whisper can't even be imported); `_align_lock` (`429` `busy`); a running whole-video transcription in `_tx_jobs` (`429` `busy`); the cooldown (`429` `rate_limited`); the download (`429` `rate_limited` and the cooldown set; `504` `timeout`; `502` `download_failed`); the permit, waited for at most 20 s (`429` `busy`); the model load (`503` `whisper_missing` on `ImportError`, `503` `model_missing` otherwise); then the windows, each cut with ffmpeg and transcribed with `word_timestamps=True`. A window that fails is counted `failed`, never cached, and the next one runs. An unexpected exception ends the stream with `500` `other`.

**Shared-state rules:** the lock and the permit are released in `finally` only if this request took them; the workdir (a `youtube-tldw-audio-` temp dir, so the stale-audio sweep covers a crash) is always removed; store 13 is pruned once, in that `finally`. Requests that never take the lock never prune. **A client that goes away** is noticed at the next write (a `progress` line follows the download and every failed window), and the loop stops after the current window, which stays cached. **A second tab** on a partly cached video gets its cached windows, then `429` `busy`. Long synchronous work in a request is accepted here because it streams, follows `/summarize/stream`'s precedent, and stops when the client goes.

### Every `reason`

Carried by plain-JSON refusals and in-stream `error` events alike, and logged as `align: <reason> <video_id>` (words and windows are never logged; tested per reason). The panel picks its text by `reason`, then by `status`.

| `reason` | Status | Where | Server text | Panel text |
|---|---|---|---|---|
| `whisper_off` | `503` | plain JSON | `Whisper is turned off.`, or when the operator pinned it, `Whisper is turned off for tldw serve on this machine (tldw config unset whisper turns it back on).` | `{verb} {n} by caption line, because Whisper is turned off. To time them to the word, set 'Videos with no subtitles' in TL;DW's options to either transcribe choice.`; a pinned `off` shows `{verb} {n} by caption line: {detail}` with the server's text |
| `whisper_missing` | `503` | plain JSON (not installed, or a library version unreadable) or in-stream (installed but can't load: `Whisper is installed but could not be loaded. Try reinstalling it: {hint}, then restart tldw serve.`) | `Whisper isn't installed for tldw serve.`, with `install_hint` | `{verb} {n} by caption line. To time them to the word, install Whisper for tldw serve: {hint}, then restart tldw serve and turn this off and on again.`, `{hint}` from `install_hint` (default `pipx inject youtube-tldw faster-whisper`) |
| `too_long` | `413` | plain JSON | `This video is longer than tldw serve's Whisper limit. To time words, run tldw config set whisper-max-minutes <minutes>, then turn this off and on again.` | `{verb} {n} by caption line: {detail}` |
| `ffmpeg_missing` | `503` | in-stream | `Word timing needs ffmpeg, and tldw serve can't find it. Install ffmpeg, restart tldw serve, then turn this off and on again.` | `{verb} {n} by caption line: {detail}` |
| `model_missing` | `503` | in-stream | `Whisper's model isn't downloaded yet.` | `{verb} {n} by caption line: Whisper's model isn't downloaded yet. Transcribe one video that has no subtitles to fetch it, then turn this off and on again.` |
| `busy` | `429` | in-stream | `Already timing words for another video. Try again when it finishes.` (the lock) or `Whisper is busy transcribing another video. Try again when it finishes.` (a transcription, or the 20 s permit wait) | the server's text, then ` Until then, spots are covered by caption line.` |
| `rate_limited` | `429` | in-stream | `YouTube is rate-limiting tldw serve, so it couldn't get the audio. Turn this off and on again in a few minutes; the wait is at most ten.`, for a fresh YouTube 429 and for the cooldown alike | the server's text, then ` Until then, spots are covered by caption line.` |
| `download_failed` | `502` | in-stream | `YouTube didn't give this video's length, or it's longer than tldw serve's Whisper limit, so its audio can't be fetched. If it's the limit, run tldw config set whisper-max-minutes <minutes>, then turn this off and on again.` when yt-dlp's match filter skipped the video (no duration, or over the ceiling; it can't say which), else `Couldn't fetch this video's audio. Turn this off and on again to retry.` | `{verb} {n} by caption line: {detail}` |
| `timeout` | `504` | in-stream, or the worker's own | `Fetching the video's audio timed out. Turn this off and on again to retry.`; the worker's 660 s timer sends none | `{verb} {n} by caption line: {detail}`, or `{verb} {n} by caption line: timing them to the word failed. Turn this off and on again to retry.` with no detail |
| `other` | `500` | in-stream (`Timing the words failed unexpectedly. Turn this off and on again to retry.`), or the worker's own (a stream with no terminal event; a port lost before `alignDone`) | as said | `{verb} {n} by caption line: {detail}`, or `{verb} {n} by caption line: timing them to the word failed. Turn this off and on again to retry.` |
| `token` | `0` | the worker: no token set | — | `{verb} {n} by caption line: TL;DW's token for tldw serve is missing or wrong. Paste the token tldw serve prints into TL;DW's options, then turn this off and on again.`; a `401` with no `reason` shows the same |
| `unreachable` | `0` | the worker: the fetch failed | — | `{verb} {n} by caption line: couldn't reach tldw serve. Start it, then turn this off and on again.` |

A `404` with no `reason` is a server too old to have the route: `{verb} {n} by caption line: this tldw serve can't time words. Update tldw, then restart tldw serve and turn this off and on again.`. A `503` with an `install_hint` and no `reason` gets `{verb} {n} by caption line. To time them to the word, install Whisper for tldw serve: {hint}, then restart tldw serve and turn this off and on again.`. Anything else gets `{verb} {n} by caption line: {detail}` when the server sent text, else `{verb} {n} by caption line: timing them to the word failed. Turn this off and on again to retry.`. Server text is always set with `textContent`, never parsed; no server text that reaches the panel carries backticks (tested).

### The panel's statuses

`{verb}` is `Bleeping`, `Muting` or `Mooing over` (`{verbLower}` the same in lower case); `{n}` is `1 spot` or `N spots`; `{s}` is whole seconds of covered audio; `{k}` and `{total}` count hits. **(A)** marks a status announced through a live region: the panel's (`.coverlive`) while the panel is open, otherwise the pill's, filled on a later tick (100 ms). Every status also goes on the panel's visible, non-live line (`.coverstatus`). Statuses never go through `showError`.

| When | Text (exact) | (A)? | Source |
|---|---|---|---|
| Reading the captions | `Reading the captions…` | yes | `content.js` `start` |
| The list is empty (`""`) | `No words are listed. Add some in TL;DW's extension options, then try again.` | yes | `load` |
| No caption line matches; the toggle stays off | `None of this video's caption lines contain your listed words, so there's nothing to cover.` | yes | `load` |
| On | `{verb} {n} from the captions (about {s} seconds of audio). Timing them to the word…` | yes | `load` |
| The sound was changed in Options while on (rev 5.6); the spot playing now stays silent | `Sound changed: now {verbLower} listed words, starting at the next covered spot.` | yes | `setSound` |
| The word list was changed in Options while on (rev 5.6); the progress and done statuses follow as usual | `Word list updated: {verb} {n} from the captions (about {s} seconds of audio).` | yes | `rebuild` |
| The list was emptied in Options while on; the session ends as Stop does, without the "off" status | `No words are listed, so covering is off. Add some in TL;DW's extension options, then turn it on again.` | yes | `rebuild` |
| A new list matches no caption line while on; the session ends as Stop does | `None of this video's caption lines contain your listed words, so covering is off.` | yes | `rebuild` |
| Each window that arrives | `Timed to the word: {k} of {total}.` | no | `onAlign` |
| Done, every hit word-timed | `{verb} {n}, all timed to the word (about {s} seconds of audio).` | yes | `alignDoneText` |
| Done, some | `{verb} {n}: {k} timed to the word, the rest by caption line (about {s} seconds of audio).` | yes | `alignDoneText` |
| Done, none (and fewer windows failed than were sent) | `{verb} {n} by caption line: Whisper didn't find the words in the audio.` | yes | `alignDoneText` |
| Every window failed | `{verb} {n} by caption line: timing them to the word failed. Turn this off and on again to retry.` | yes | `alignDoneText` |
| A failure | the "Panel text" column above | yes | `alignFailureText` |
| The `AudioContext` isn't running, or `resume()` rejected; once per session | `The browser blocked the sound, so listed words are muted instead. Turn this off and on again to allow sound.` | yes | `startSound` |
| Off | `Bleeping is off.`, `Muting is off.` or `Mooing is off.` | yes | `stop` |
| The captions couldn't be read | `Couldn't read this video's captions, so no words will be covered. Open the video's transcript and try again, or try another video.` | yes | `load` |
| No `<video>` on the page | `Couldn't find the YouTube player. Reload the page, then try again.` | yes | `start` |
| A live stream (`duration === Infinity`) | `Covering words isn't available on live streams. Try again after the stream ends.` | yes | `start` |
| The toggle pressed during an ad | `An ad is playing. Try again once the video starts.` | yes | `start` |
| An ad starts while covering is on; when it ends, the status from before it (or one that arrived during it) returns to the visible line, unannounced | `An ad is playing, so covering is paused. It picks up again when the video comes back.` | yes | `tick` |

**The pill** (`#tldw-mute-pill`): its label, not a live region, is `Bleeping listed words`, `Muting listed words` or `Mooing over listed words`, plus ` · now` while a range is active, after an `aria-hidden` emoji (🔔, 🔇 or 🐄). Its Stop button reads `Stop` and is named `Stop bleeping`, `Stop muting` or `Stop mooing`. A sound changed in Options while on updates the emoji, the label and the Stop button's name in place (`setMode`, rev 5.6); the pill is never rebuilt.

**Options** (`options.html`): the `#whisper` select's hint gains `It also controls word timing for "Cover listed words": unless this is set to "Don't transcribe", turning that on fetches the video's audio once and runs Whisper on a few seconds around each listed word, without asking. With "Don't transcribe", each word is covered for its whole caption line instead.` The word-list label is `Words to cover (for 'Cover listed words')`, its hint `Separate with commas or new lines. Whole words, any case. Leave empty to turn off Cover listed words.`, the button `Restore default list`, and the sound select `Sound for listed words` (`Bleep (default)`, `Mute`, `Moo`) with the hint `Every choice silences the video during the word. Bleep and Moo also play a short sound over it.`

### Invariants

| Invariant | Blast radius | Source |
|---|---|---|
| **The word list never leaves the browser.** Matching runs in `content.js`; the request carries only `url`, `whisper` and `windows`. The list itself is in no log, cache or key, but store 13 holds every word heard around each hit, so whoever can read that directory can narrow each window to a few likely words. | Server logs and caches would hold the list | `content.js` `startAlign`; `background.js:93-158` (tested) |
| **Server words never reach the DOM.** They are only compared with the list's pattern. | A word typed into the DOM is a markup path from the server | `content.js` `onAlign` (tested) |
| **The feature unmutes only a mute it set.** A `volumechange` releases ownership only when `muted` differs from what the feature last set; a `.ytp-mute-button` click or an `m` key (no Ctrl, Meta or Alt, not in an editable target, read through `composedPath()`) releases it and stops the sound. A video already muted when a range begins gets no ownership and no sound. | The user's own mute | `muteStep`; `content.js` `attach` (tested) |
| **Sound only in the first 1.5 s of a range** (`MUTE_SOUND_CAP_MS`), at most 0.15 (bleep) or 0.25 (moo) × `video.volume`. Mute mode never creates an `AudioContext`. A `ratechange` stops a playing sound and starts it again for the time left under the cap, at the new rate. | Loudness | `soundLengthMs`, `soundPlan` |
| **Teardown removes every listener** (with its capture flag) on Stop, a new video id, a detached `<video>` and `pagehide`. `emptied`/`loadstart` with the same id give up the mute and sound but keep the session. | A muted video after the feature is off | `content.js` `stop` (tested) |
| **One alignment at a time, process-wide; it never queues behind a transcription** (`429` `busy` after 20 s), and never downloads a Whisper model. | CPU, YouTube rate limit | `server.py:72`, `whisper_transcribe._load_model(local_only=True)` |
| **The word list in the default** lives only in `extension/mute_words.js`: never in docs, tasks, reviews, commits or fixtures (which use made-up words). | Profanity in the repo's prose | `extension/mute_words.js` |

## chrome.storage.local keys

The defaults are defined in **three places**, and they differ:

1. **`background.js` `DEFAULTS`** (`background.js:10-11`) has no `closeAction`.
2. **`options.js` `DEFAULTS`** (`options.js:3-5`) has all seven settings.
3. **`content.js`'s inline defaults** in each `storage.local.get` call (`content.js:47`, `:1035`, `:1222`).

**The token is stored in plain text** in `chrome.storage.local`. The Options field is `type="password"` (`options.html:32`), which only masks it on screen. Whatever `serverUrl` says, the token is sent there as `Bearer`; nothing checks the host (see `architecture.md`).

**The only `storage.onChanged` listener** is in `content.js:50-70` (between its "settings listener" markers). It acts only in the `local` area, on `closeAction`, `muteSound` and `muteWords`. For the two T-020 keys it passes only their `newValue`s to the running "Cover listed words" session (`settingsChanged`); nothing else from `changes` is read or passed on (rev 5.6). Every other key is read again at the moment it is used: the worker calls `getSettings()` at the start of each handler (`background.js:43-52`), the panel reads `voice` when it renders a result (`content.js:1035`), and it reads the sponsor keys in `maybeThank` (`content.js:1222`). So a change made in Options applies to the next request; requests already running, and an open panel's chosen voice, keep the value they started with. **Cover listed words applies `muteSound` and `muteWords` while on** (`tests/js/settings_listener_checks.js`, `mute_wiring_checks.js` group `live`):
- **A sound change:** the playing sound stops and the spot playing now stays silent (`quietCur`); the next covered spot uses the new sound. To `mute` closes the `AudioContext`; leaving `mute` opens one outside a gesture, which falls back to muted-only with the `soundBlocked` status if it won't run.
- **A list change:** the captions read at toggle-on (kept in the tab's memory only, dropped on every end) are re-scanned in place. Hits inside windows Whisper already returned this session are timed locally; only the rest are sent on a new port, and the old port is ended first, so its late messages are dropped. An unchanged list (after normalising) does nothing. An empty list or no match ends the session with its own status.
- **A busy refusal on a rebuilt session** is retried up to 3 times, after 5, 10 and 20 s, each on a fresh port, but only if it came before any `alignProgress` on that port (that is, before a download began). Every other reason, and a busy on the first toggle-on, is not retried.
- **Save fires up to two events** (the `set`, which may carry `muteSound`, then `muteWords`'s `set` or `remove`): at most one rebuild per Save. During loading, a live value wins over the stored read still pending.

| Item | Default (`background.js`) | Default (`options.js`) | Default (`content.js`) | Written by | What the user sees it change | Invariant | Blast radius | Source |
|---|---|---|---|---|---|---|---|---|
| `serverUrl` | `"http://127.0.0.1:8765"` | `"http://127.0.0.1:8765"` | — | `options.js:176` | Where every request goes. The Options "Server URL" field. | Empty falls back to the default. Trailing `/` characters are stripped. The default matches `host_permissions` (see "Manifests"). | Every server call; and where the token is sent. | `background.js:10`, `:47`; `options.js:3`, `:36`, `:125`, `:137` |
| `token` | `""` | `""` | — | `options.js:176` | When empty, every feature shows the "No server token set" message. | Sent as `Authorization: Bearer <your-token>` on every `POST`. Never on `/health` or `/voices`. | Auth for every authenticated route. | `background.js:10`, `:47`; `options.js:3`, `:37`, `:126`, `:137` |
| `voice` | `"amy"` (**read but never used** by the worker: `getSettings` doesn't return it) | `"amy"` | `"amy"` (`content.js:1035`) | `options.js:176` | Which voice is preselected in the panel's picker and in the Options picker. Picking a voice in the panel is **not saved**. | Applied in the panel only if it is one of `VOICE_OPTIONS` (`content.js:1036`). | The default Listen voice. | `background.js:10`; `options.js:3`, `:47`, `:127`, `:137`; `content.js:1035-1037` |
| `model` | `"opus"` | `"opus"` | — | `options.js:176` | "Thorough — Opus" or "Fast — Sonnet". Sent on summarize, cached, segments and ask, but **not** on speak or preview. | Must be one of `MODELS`, or it falls back to the default (`background.js:48`, `options.js:168-169`). The server answers `400` to anything outside its allowlist. | The summary cache key, and every model call. | `background.js:10-13`, `:48`; `options.js:4`, `:9`, `:45-46`, `:130-131`, `:137` |
| `segmentRatio` | `"auto"` | `"auto"` | — | `options.js:176` | How long Play key moments runs ("Auto", "Tight", "Balanced" or "Generous"). Sent as `segment_ratio` (summarize, cached) and as `ratio` (segments). | Stored as a **string**. The worker parses it with `parseFloat` and keeps it only if 0 < r ≤ 1, otherwise `null`; `"auto"` becomes `null`. Options keeps only the values in `RATIOS`. | Key-moments length, and the key-moments prefetch, on a cached summary (`server.py:1236-1242`, `:1364-1367`) and after a fresh one (`:1319-1323`). It is not the summary cache key, which uses `ratio` (`:748-770`). | `background.js:11`, `:46`, `:51`; `options.js:4`, `:8`, `:42-44`, `:132-133`, `:137` |
| `whisper` | `"ask"` | `"ask"` | — | `options.js:176` | Videos with no captions: offer to transcribe, transcribe without asking, or say there is no transcript. `"always"` also selects the 40-minute worker budget. | Must be one of `WHISPER_POLICIES`, or it falls back to the default. It is sent **only** on `/summarize/stream`. | The no-captions flow. | `background.js:11`, `:15`, `:49-50`, `:218`, `:227`; `options.js:5`, `:7`, `:40-41`, `:134-137` |
| `closeAction` | — (absent) | `"continue"` | `"continue"` (`content.js:47`) | `options.js:176` | Closing the panel mid-run either keeps the work running in the background (`continue`) or stops and discards it (`abort`). | Anything other than `"abort"` means `"continue"` (`content.js:48`, `:53`; `closePolicy`, `content.js:73-78`). The live `storage.onChanged` listener keeps it in sync, because `close()` must decide synchronously. | Background work, keepalive pings, stopping transcriptions. Guarded by `render_checks.js:117-130`. | `options.js:4`, `:38-39`, `:128-129`, `:137`; `content.js:42`, `:47-56` |
| `sponsorDismissed` | — | — | `false` (`content.js:1222`) | `content.js:1239`, `:1243` | Hides the sponsor ask for good, after "No thanks" or after following the link. | Once true, it is never set false again. There is no Options control for it. | The sponsor box. Guarded by `render_checks.js:245-262`. | `content.js:1222-1223`, `:1239`, `:1243` |
| `sponsorShown` | — | — | `0` (`content.js:1222`) | `content.js:1245` | After `SPONSOR_MAX_SHOWS` (3) showings, the ask stops appearing. | Goes up by one each time the box is shown, and only after `stats.videos` ≥ `SPONSOR_AFTER_VIDEOS` (25). | The sponsor box. Guarded by `render_checks.js:245-262`. | `content.js:40-41`, `:1221-1223`, `:1245` |

| `muteWords` | — | absent means the shipped default, `TLDW_MUTE_DEFAULTS` in `mute_words.js` | read with `storage.local.get("muteWords")` at toggle-on, and applied live while on through the one `onChanged` listener (rev 5.6; this supersedes "at toggle-on only") | `options.js:181` (`set`), `:180` (`remove`) | The "Words to cover (for 'Cover listed words')" textarea. `""` turns the feature off. | **Absent** means "use the shipped default", so a later change to the default reaches the user. Save **removes** the key when the normalised box equals the normalised default, saves `""` for an empty box, and otherwise saves the normalised list (one entry per line). Both sides normalise with the single `TLDW_normaliseMuteList` from `mute_words.js`. "Restore default list" refills the box and writes nothing. **Never sent** to the worker or the server. The separate `set` and `remove` aren't atomic; the worst case is the default list. Because of that, one Save can fire two `onChanged` events; the second carries `muteWords`. | "Cover listed words" (T-020). | `options.js:28-54`, `:76-79`, `:157-160`, `:175-181`; `content.js:2746` (read), `:50-70` (live) |
| `muteSound` | — | `"bleep"` when absent or unknown | read with `storage.local.get("muteSound")` at toggle-on, and applied live while on through the one `onChanged` listener (rev 5.6; this supersedes "at toggle-on only") | `options.js:177` | "Sound for listed words": `bleep` (the default), `mute` or `moo`. | Only a value in `MUTE_SOUND_CHOICES` is saved or used; anything else reads as `bleep`. Edits take effect right away, from the next covered spot (rev 5.6). | Which sound covers a word. | `options.js:32-37`, `:79`, `:175-177`; `content.js:2747` (read), `:2475` (resolved), `:50-70` (live) |

`options.js` writes the seven settings, plus `muteSound`, with **shorthand properties** in one `set`, then writes or removes `muteWords` (T-020). The original line:

`options.js:176-177` writes the seven settings with **shorthand properties**: `{ serverUrl, token, voice, closeAction, model, segmentRatio, whisper }`. A storage-key extractor that reads only `key:` pairs misses them.

## Extension in-memory state (not persisted)

These are listed so no one mistakes them for storage. They all disappear when the worker is suspended, the extension reloads, or the page navigates. **None of the maps has an eviction policy.**

| Item | Holds | Source |
|---|---|---|
| `cache` (worker) | videoId → result event | `background.js:25` |
| `audioCache` (worker) | `videoId` and `voice`, joined by a pipe character → mp3 `data:` URL | `background.js:26` |
| `previewCache` (worker) | voice → `data:` URL | `background.js:523` |
| `previewCache` (Options) | voice → `blob:` object URL | `options.js:81` |
| `lastPayload`, `lastAudio`, `lastSegments`, `chat` (panel) | the last result, the clip, the clips and the Q&A history for this page session | `content.js:27-28`, `:35`, `:86` |
| `muteCover` (panel, T-020) | The page's one "Cover listed words" session (`createMuteCover`): the hits and active ranges, the ownership state (`owned`, `lastSet`), queued refinements, `alignPort` and its ping and 660 s safety timers, the boundary timer, the `AudioContext` and the playing sound, the pill, and the last status. It belongs to the page, not the panel, so closing the panel leaves it running; each tab is independent. | `content.js:2118-2693` (`createMuteCover`), `:2735` (the instance) |

## Manifests

There are two files: `extension/manifest.json` (Chrome and other Chromium browsers) and `extension/manifest.firefox.json` (copied to `dist-firefox/manifest.json` by `build-firefox.sh`). The JS files are shared through the `globalThis.browser ?? globalThis.chrome` shim (`background.js:8`, `content.js:9`, `options.js:1`).

### Manifest permissions

| Item | Invariant | Blast radius | Source |
|---|---|---|---|
| `activeTab` | Gives temporary access to the tab when the toolbar button is clicked. That is what lets `scripting.executeScript` inject `content.js` (`background.js:61`) and lets the worker read `tab.url` (`background.js:60`) **without a YouTube host permission**. The panel reads YouTube's transcript from inside the page for the same reason (`content.js:408-410`). | If removed, a click can't inject the panel. | `manifest.json:6`; `manifest.firefox.json:6` |
| `scripting` | Needed for `scripting.executeScript`. | The same as `activeTab`. | `manifest.json:6`; `manifest.firefox.json:6`; `background.js:61` |
| `storage` | Needed for `chrome.storage.local` (see "chrome.storage.local keys"). | Settings, the token, the sponsor state. | `manifest.json:6`; `manifest.firefox.json:6` |
| `http://127.0.0.1:8765/*` (host permission) | **Coupled to the default `serverUrl`** (`background.js:10`, `options.js:3`) and to the server's default port 8765. It covers that exact origin only: not `localhost`, and not any other port. When `serverUrl` points elsewhere, the worker's fetches are outside this grant and depend on the server's CORS answer, which allows only `chrome-extension://` origins unless one is pinned (`server.py:918-924`, `:931-938`). **Whether a custom `serverUrl` then works is `UNKNOWN — needs human confirmation`.** `deployment.md`'s isolated-server test is planned to settle it. In Firefox, host access is opt-in for the user (`extension/README.md`, Setup step 2). | Every call from the worker and from Options. | `manifest.json:7`; `manifest.firefox.json:7` |

### Manifest keys

| Item | Chrome (`manifest.json`) | Firefox (`manifest.firefox.json`) | Invariant | Source |
|---|---|---|---|---|
| `manifest_version` | `3` | `3` | — | `manifest.json:2`; `manifest.firefox.json:2` |
| `name` | `TL;DW — Hackathon` | the same | The hackathon fork's name, so it can be told apart from the normal tldw extension when both are installed in one Chrome (T-019). Guarded by `tests/test_docs_contracts.py`. | `:3` in both |
| `version` | `1.3.0` | `1.3.0` | **Kept equal in both manifests**, and equal to `pyproject.toml:7` and `src/youtube_tldw/__init__.py:3`. Nothing checks this. | `:4` in both |
| `description` | the same in both | the same in both | — | `:5` in both |
| `background` | `{ "service_worker": "background.js" }` | `{ "scripts": ["background.js"] }` (an event page) | The file name is shared. The port keepalive (`ping`) exists because of the MV3 service worker's lifetime. | `manifest.json:8`; `manifest.firefox.json:8` |
| `browser_specific_settings` | absent | `gecko.id` `tldw@drewcurley`, `gecko.strict_min_version` `115.0` | Firefox only. | `manifest.firefox.json:9-11` |
| `action` | `default_title` `TL;DW this video`; `default_icon` 16/48/128 | the same | A click on the action is the only entry point (`background.js:58`). | `manifest.json:9-12`; `manifest.firefox.json:12-15` |
| `options_page` / `options_ui` | `"options_page": "options.html"` | `"options_ui": { "page": "options.html", "open_in_tab": true }` | The same page in both. | `manifest.json:13`; `manifest.firefox.json:16` |
| `icons` | `icons/icon16.png`, `icons/icon48.png`, `icons/icon128.png` | the same | The files must exist under `extension/icons/`. | `manifest.json:14`; `manifest.firefox.json:17` |

### Absent manifest keys

| Item | Status |
|---|---|
| `content_scripts` | N/A. Nothing is injected automatically. `content.js` is injected only on a toolbar click, by `scripting.executeScript` (`background.js:61`), and guarded against double-install by `window.__tldwInit` (`content.js:6-7`). |
| `content_security_policy` | N/A. The browser's default MV3 extension CSP applies. The extension loads no remote code: its only script tag is `<script src="options.js">` (`options.html:83`), and the panel is built inline in a shadow root (`content.js:150-281`). |
| `web_accessible_resources` | N/A. The page never loads an extension file. The panel's markup and styles are inline in `content.js`, and audio plays from `data:` and `blob:` URLs (`background.js:381`, `:552`; `content.js:1169`, `:3140`). The icons are used only by the browser's own UI. |
| `externally_connectable` | N/A. No web page or other extension can `connect` to the worker, which is what makes the `port.name` check at `background.js:71` enough (`architecture.md` relies on this absence). |
| `commands` | N/A. There is no keyboard shortcut for the action. The panel's keys are handled in the page (see "Keyboard and accessibility baseline"). |

### build-firefox.sh copy list

| Item | Invariant | Blast radius | Source |
|---|---|---|---|
| `background.js`, `content.js`, `mute_words.js`, `options.js`, `options.html` | Copied into `dist-firefox/`. A new extension file must be added to this line. `test_firefox_build_copies_every_file_the_extension_loads` runs the script on a copy and fails if any file a manifest, an `executeScript` `files` array or an `options.html` `<script src>` names is missing, with a negative self-test (T-020). | The Firefox build. | `extension/build-firefox.sh:10`; `tests/test_extension_render.py` |

### Injected files

| Item | Invariant | Blast radius | Source |
|---|---|---|---|
| `mute_words.js` | Injected **first** by `executeScript({ files: ["mute_words.js", "content.js"] })`, and loaded first by `options.html` (`<script src="mute_words.js">` before `options.js`). It declares nothing at top level, only `globalThis.TLDW_MUTE_DEFAULTS ??= Object.freeze([...])` and `globalThis.TLDW_normaliseMuteList ??= function …`, because every toolbar click re-runs it and a top-level `const` would throw the second time. The default words appear **only** in this file. | Without it, the defaults are missing and an absent `muteWords` means an empty list, which is off. | `background.js:61`; `options.html:98`; `extension/mute_words.js` |
| `content.js` | Injected second, so it can read the two globals. Guarded by `window.__tldwInit`. | Every panel feature. | `background.js:61` |

`test_the_injected_files_are_the_word_list_then_content` pins both orders, and the `injected_files` extractor in `tests/test_docs_contracts.py` checks this table.
| `icons/*.png` | Copied into `dist-firefox/icons/`. | Firefox icons. | `extension/build-firefox.sh:9`, `:11` |
| `manifest.firefox.json` → `manifest.json` | Renamed on copy. `dist-firefox/` is deleted first (`rm -rf`) and is gitignored (`.gitignore:9`). | The Firefox build. | `extension/build-firefox.sh:7-8`, `:12` |

**Firefox runtime differences in the JS:** Firefox's MSE has no `audio/mpeg`, so the panel asks for one buffered clip (`content.js:29-31`, `:1103`, `:1146-1150`). The server's default Origin allowlist accepts only `chrome-extension://` (`server.py:924`). The Firefox `moz-extension://` origin's behaviour is recorded as `UNKNOWN — needs human confirmation` in `architecture.md`.

## YouTube page contract

`content.js` reads YouTube's page from inside the page, through the DOM only. It never reads `ytInitialPlayerResponse` and never injects into the MAIN world (`content.js:396-410`; no `ytInitialPlayerResponse` appears anywhere in `extension/`).

Selectors called on `root`, `c` or `box` (the panel's own shadow root and its children) are **excluded** from this inventory. They select tldw's own markup, not YouTube's. So are `e.target.closest(".ts")` (`content.js:1277`) and `el.querySelector("a")` (`content.js:2963`), whose receivers are inside the shadow root.

### Page selectors

| Item | Kind | Invariant | Blast radius | Source |
|---|---|---|---|---|
| `transcript-segment-view-model, ytd-transcript-segment-renderer` | `SEGMENT_ROWS`: the transcript rows (current and older renderers) | Rows are read **by structure, not class name**. The start is the leaf whose text matches `CLOCK` (`/^\d{1,2}(?::\d{2}){1,2}$/`). The words are the other children, minus any child whose class contains `a11y` (the English-only screen-reader twin). | If YouTube renames the elements, `status` becomes `panel-empty` and the server falls back to fetching. Guarded by `page_transcript_checks.js:104-118` and `:173-206`. | `content.js:412-413`, `:450-482`, `:492` |
| `ytd-engagement-panel-section-list-renderer` | `PANEL`: the engagement panel holding the transcript | Chosen in this order: the panel containing rows; then one whose `target-id` matches `/transcript/i` **and** whose `visibility` contains `EXPANDED`; then any whose `target-id` matches. | If the panel can't be found, it can't be closed again, and is left open on the viewer's page. | `content.js:414`, `:487-496` |
| `ytd-video-description-transcript-section-renderer button` | `TRANSCRIPT_BUTTON`: "Show transcript" in the description | Clicked even while the description is collapsed. If it is missing, `status` is `no-transcript-button`. | Reading the page's transcript. Guarded by `page_transcript_checks.js:216-217`. | `content.js:415`, `:543-547` |
| `#visibility-button button` | The panel's own close button | Clicked to put the panel back, but only if tldw opened it. | If it can't be found, the transcript panel stays open. Guarded by `page_transcript_checks.js:191-201`, `:214`. | `content.js:572` |
| `.segment-timestamp` | The older row's timestamp | Used first when present. | Row start times. | `content.js:451` |
| `*` | Every descendant of a row, scanned for the clock leaf | — | Row start times. | `content.js:453` |
| `.segment-text` | The older row's words | Used first when present. | Row text. | `content.js:462` |
| `meta[name="title"]` | `attr(…, "content")`: the title | The first choice for `page.title`. | The summary title. Guarded by `page_transcript_checks.js:188`. | `content.js:512` |
| `#above-the-fold h1, ytd-watch-metadata h1` | `text(…)`: the title, as a fallback | — | The summary title. | `content.js:513` |
| `span[itemprop="author"] link[itemprop="name"]` | `attr(…, "content")`: the channel | The first choice for `page.channel`. | The byline. Guarded by `page_transcript_checks.js:189`. | `content.js:514` |
| `#owner #channel-name a, ytd-channel-name a` | `text(…)`: the channel, as a fallback | — | The byline. | `content.js:515` |
| `meta[itemprop="duration"]` | `attr(…, "content")`: ISO 8601 duration | Parsed by `isoDurationMs`, `^PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?$`. When it doesn't parse, `duration_ms` is left out. | Last-cue end and duration. Guarded by `page_transcript_checks.js:166-169`, `:190`, `:219-222`. | `content.js:438-443`, `:517-518` |
| `video.html5-main-video` | The YouTube player | Tried first, with `video` as the fallback. The code uses `currentTime`, `play()`, `pause()`, `duration` and the `timeupdate` event. | Citation seeks, Play key moments, and the trimmed-length note. | `content.js:1323`, `:2845`, `:2867`, `:2972` |
| `video` | Fallback player | — | The same. | `content.js:1324`, `:2846`, `:2868`, `:2973` |
| `tldw-toast` | `getElementById`: tldw's own toast (`role="status"`) on the page | Only one at a time: an old toast is removed first. | Background notices. | `content.js:775-779` |
| `tldw-pill` | `getElementById`: tldw's own skip-playback pill | — | Skip controls. | `content.js:2946`, `:3022`, `:3057`, `:3085` |
| `tldw-pill-prev` | `getElementById`: the pill's previous button | — | Skip controls. | `content.js:3040`, `:3078` |
| `tldw-pill-next` | `getElementById`: the pill's next button | — | Skip controls. | `content.js:3045`, `:3080` |
| `.pill-label` | `pill.querySelector`: the pill's text | — | Skip status text. | `content.js:2948`, `:3059` |
| `#movie_player` | The YouTube player element (T-020) | Read with `doc.querySelector("#movie_player")`; its `.matches(".ad-showing")` is the ad state, and it takes focus after Stop on the mute pill. | If renamed, ads are never detected (the feature would mute or sound during an ad) and Stop's focus move does nothing. | `content.js:2145-2146`, `:2548-2549` |
| `.ad-showing` | The class `#movie_player` carries while an ad plays (T-020) | Read with `player.matches(".ad-showing")`. During an ad the feature gives up any mute it set, stops its sound and does nothing until the ad ends. | The same. | `content.js:2146` |
| `.ytp-mute-button` | YouTube's mute button (T-020) | A capture-phase `click` on `document` whose `e.target.closest(".ytp-mute-button")` matches hands mute control back to the user. | If renamed, a mute-button click inside a range is seen only through `volumechange`, so a mute made while the feature already has the video muted is missed. | `content.js:2323-2327` |
| `tldw-mute-pill` | `getElementById`: tldw's own "Cover listed words" pill, in its own shadow root, at `bottom: 136px` (T-020) | Built with `textContent` only; appended to `document.fullscreenElement \|\| document.body` and moved on `fullscreenchange`. `stopSkip`/`removePill` never touch it. A fading pill is removed when the feature is started again. | The covering controls. | `content.js:2061-2116` (`buildMutePill`), `:2444-2445` |
| `video.duration === Infinity` | The live-stream signal at toggle-on (T-020). `NaN` (metadata not loaded) is **not** live. | A live stream gets the live status and no listeners. | — | `content.js:2450` |

Other page-level contracts:

- The panel host (`#tldw-host`) and the pill are appended to **`document.fullscreenElement || document.body`**, so they show in fullscreen too (`content.js:148-149`, `:282`, `:3053`). The toast goes to `document.body` (`content.js:796`).
- `window.__tldwInit` is the once-per-page guard (`content.js:6-7`).
- `location.href` is the fallback URL for key moments (`content.js:2833`).

### Watch-page URL recognition (worker)

| Item | Invariant | Blast radius | Source |
|---|---|---|---|
| `/watch` | The page counts only if the hostname **ends with** `youtube.com`, the path is `/watch`, and `v` matches `^[A-Za-z0-9_-]{11}$`. Otherwise the tab gets `TLDW_ERROR`. `content.js` has already been injected by then, whatever the page (`background.js:61-62`). | Which tabs can be summarized. | `background.js:28-34` |
| `youtu.be` | The hostname must equal `youtu.be`, and the first path segment must match the same 11-character pattern. | The same. | `background.js:35-38` |

### Open, read, close: the transcript panel flow

This is `collectPageTranscript` (`content.js:538-565`). It **always resolves**; it never throws.

1. Read `pageMeta()` (`content.js:540`).
2. If rows are already on the page, the viewer had the transcript open. Read it, and **leave it open** (`content.js:541`, `:555`).
3. Otherwise, find `TRANSCRIPT_BUTTON`. If it isn't there, return `status: "no-transcript-button"` (`content.js:543-544`).
4. Click it, then poll every `PANEL_POLL_MS` (200 ms), for up to `PANEL_WAIT_MS` (8,000 ms), until the first row appears (`content.js:547-551`).
5. Wait until the row count stops growing, for up to `PANEL_SETTLE_MS` (5,000 ms) (`settledRows`, `content.js:524-534`, `:553`).
6. Read the rows. If tldw opened the panel, close it with `#visibility-button button` (`restoreTranscriptPanel`, `content.js:569-575`).
7. If there are no rows, return `panel-empty`. Otherwise return `ok-panel (N cues)` with `cues`. If anything throws, return `error-<name>` (`content.js:556-564`).

Invariants:

- The flow runs **only after a cache miss** from `probeCache`, or on a refresh or an accepted transcription (`content.js:601-608`, `:676-677`).
- A page that tldw opened is always put back the way it was found.
- `page_transcript_checks.js:95-98` rewrites the three timing constants by name (see "Source text tests depend on").

**Blast radius:** if this degrades, the server fetches the transcript itself. The `page` payload doc-comment at `server.py:788-792` says why that matters: a server request to YouTube can get a `429`.

## Keyboard and accessibility baseline

**Invariant:** every control in the panel can be reached and used from the keyboard. Keystrokes typed in the panel never reach YouTube's shortcuts. The panel is announced as a modal dialog.

Facts that fall short of this invariant are recorded in the follow-ups at the end of this section.

### Keys

| Item | Behaviour | Invariant | Blast radius | Source |
|---|---|---|---|---|
| `Escape` | Closes the panel, under the `closeAction` policy. It calls `stopPropagation`, even when the key event did not come from the modal. | Works whenever the panel is mounted. | The way out of the panel. | `content.js:321` |
| `Enter` | In the ask box (a `TEXTAREA` or `INPUT` in the modal), Enter without Shift calls `submitQuestion()`. It is handled in the window-capture `onKey`, because the shield stops the textarea's own `keydown` from ever running. | The chat input must not get a `keydown` handler of its own. Guarded by `render_checks.js:112-114`. | Sending questions. | `content.js:323-330` |
| `Shift+Enter` | In the ask box: the default action, a newline. | — | Multi-line questions. | `content.js:329` |
| `Tab` / `Shift+Tab` | Wraps focus between the first and last element matched by `button:not([hidden]):not([disabled]), select:not([disabled]), textarea:not([disabled]), audio` in the shadow root. **Never trapped while typing** in a text box. | Focus stays inside the modal. | Keyboard users. | `content.js:328-340` |
| Capture-phase hotkey shield | `keydown` → `onKey`; `keyup` and `keypress` → `shieldKey`, all on **`window`, in the capture phase**. They are added in `mount()` and removed in `unmount()`. `fromModal()` uses `composedPath()`, falling back to `host.contains`, because shadow retargeting hides the real target. Only events from the modal are stopped. | Must stay on `window` in the capture phase: YouTube binds space and the arrow keys as capture listeners on `document`, and a window capture listener is the only one that runs earlier. | If the shield moves, a space typed in the ask box pauses the video. Guarded by `render_checks.js:84-114`. | `content.js:289-291`, `:295-315`, `:387-389` |

### Dialog semantics and focus

| Item | Invariant | Blast radius | Source |
|---|---|---|---|
| `role="dialog"` | On `.panel`. | Screen readers announce the panel as a dialog. | `content.js:270` |
| `aria-modal="true"` | On `.panel`. | — | `content.js:270` |
| `aria-labelledby="tldw-h"` | Points at `<h1 id="tldw-h">TL;DW</h1>`, whose text becomes `TL;DW (cached)` for cached results. | The dialog's accessible name. | `content.js:270`, `:272`, `:956` |
| Focus on open, and restored on close | `mount()` saves `document.activeElement` and focuses `.panel` (`tabindex="-1"`). `unmount()` restores focus to the saved element. | Keyboard users land in the panel and return to where they were. | `content.js:147`, `:270`, `:288`, `:393` |
| Other focus moves | A new audio clip takes focus, but not when it is restored on re-open (`content.js:3177`). Opening the ask panel by click focuses the input (`content.js:1270`, `:1285`). | — | as cited |
| `aria-expanded` | On `.asktoggle`, kept in sync with the ask panel. | — | `content.js:970`, `:1283`, `:1289` |

### aria-live regions

| Item | Element | Source |
|---|---|---|
| Transcription offer, Whisper not installed | `.status.err` with `role="status" aria-live="polite"` | `content.js:822` |
| Transcription offer | `.status` with `role="status" aria-live="polite"` | `content.js:837` |
| Audio and key-moments status | `.audiostatus` with `aria-live="polite"` | `content.js:889` |
| Conversation log | `.asklog` with `role="log" aria-live="polite" aria-label="Conversation"` | `content.js:972` |
| Ask status | `.askstatus` with `aria-live="polite"` | `content.js:978` |
| Background toast | `#tldw-toast` with `role="status"` (implicitly polite) | `content.js:779` |
| "Cover listed words" announcements, panel open | `.coverlive` (visually hidden) with `role="status" aria-live="polite"`; cleared, then filled 100 ms later | `content.js` `audioRowHtml`, `coverUi` |
| "Cover listed words" announcements, panel closed | the mute pill's own visually hidden `role="status" aria-live="polite"` region, created **empty** and filled on a later tick. Each status is announced in one region only. | `content.js` `buildMutePill`, `say` |

**Not live regions:**

- the loading view's progress text, `content.js:697-702`;
- the main error view, `showError`, `content.js:867`;
- the partial-summary view, `content.js:912-918`;
- the skip pill's label, `content.js:3041-3043`;
- the "Cover listed words" status line `.coverstatus` and the mute pill's label (T-020);
- the Options page's `#status` and `#previewStatus`, `options.html:77-78`.

### Accessible names for icon and control buttons

| Item | Visible text | Accessible name | Source |
|---|---|---|---|
| `.close` | `✕` | `aria-label="Close"` | `content.js:276` |
| `.regen` | `↻` | **No `aria-label`; only `title`** ("Summarize again, ignoring the cached summary (keeps the transcript)") | `content.js:274` |
| `.copy` | `Copy` / `Copied` | its text | `content.js:275`, `:995` |
| `.stoptx` | `⏹ Stop transcribing` | `aria-label="Stop transcribing this video's audio"` (guarded by `render_checks.js:401-402`) | `content.js:713-715` |
| `.transcribe` | `Transcribe the audio` | `aria-label="Transcribe this video's audio locally"` | `content.js:845` |
| `.playkey` | `⏭ Play key moments` | `aria-label="Play just the key moments in the YouTube player"` | `content.js:877` |
| `.listen` | `🔊 Listen to summary`, `⏹ Stop` or `🔊 Regenerate` | `aria-label="Generate spoken audio of this summary"`, or `"Stop generating audio and choose a different voice"` while generating | `content.js:878`, `:1047-1051` |
| `.voice` (select) | — | `aria-label="Voice"` | `content.js:880` |
| `.vpreview` | `▶ Preview` / `…` | `aria-label="Preview the selected voice"` | `content.js:881-882` |
| `.circ` (progress ring) | — | `aria-hidden="true"` | `content.js:885` |
| `audio` (player) | — | `aria-label="Spoken summary"` | `content.js:3159` |
| `.dl` | `⬇` | `aria-label="Download this summary as an mp3"` | `content.js:3165-3166` |
| `.asktoggle` | `💬 Ask about this video` / `💬 Hide questions` | its text | `content.js:970`, `:1284`, `:1290` |
| `.askinput` (textarea) | — | `aria-label="Ask a question about this video"` | `content.js:974` |
| `.asksend` | `Send` / `⏹ Stop` | its text | `content.js:976`, `:1427` |
| `.ts` (citation link) | `12:34`-style label | its text, plus `title="Jump to … in the video"`, `role="button"`, `tabindex="0"` | `content.js:1316-1317` |
| `.sdismiss` | `No thanks — don't ask again` | its text | `content.js:1234` |
| Pill previous | `‹` | `aria-label="Previous key moment"` | `content.js:3039` |
| Pill next | `›` | `aria-label="Next key moment"` | `content.js:3044` |
| Pill stop | `✕` | `aria-label="Stop skipping"` | `content.js:3046` |
| `.covertoggle` (T-020) | `Cover listed words`, with a `✓ ` before it while pressed (CSS, in an `aria-hidden` span, so the state isn't shown by colour alone) | its text; `aria-pressed="true"/"false"`. While the captions are read it is `aria-disabled="true"` (not `disabled`, so it keeps focus) and `.coverwrap` is `aria-busy`; a second press stops the session | `content.js` `audioRowHtml`, `coverUi` |
| Mute pill Stop (T-020) | `Stop` | `aria-label` `Stop bleeping`, `Stop muting` or `Stop mooing`; a real `<button type="button">` at least 44×44 px with a visible focus ring, in the pill's own shadow root. Pressing it moves focus to `#movie_player`, puts the "off" status in the pill's live region, and removes the pill 1,000 ms later | `content.js` `buildMutePill`, `stop` |
| Options `#preview` | `▶ Preview` | its text, plus `title`; every Options input and select has a `<label for>` | `options.html:29-72`, `:36` |

### Gaps against the baseline, found by reading the code

None of these has been checked in a browser.

- `.regen` (`↻`) has a `title` but no `aria-label` (`content.js:274`).
- `.ts` citation links take keyboard focus (`tabindex="0"`, `role="button"`), but they have no `href` and no key handler. Only a mouse `click` on `.asklog` seeks (`content.js:1276-1279`). So, as the code reads, Enter or Space on a citation does nothing.
- **The Tab trap's selector can pick an unfocusable last element.** `:not([hidden])` tests only the element itself. While `.askpanel` is `hidden` (`content.js:971`), `.askinput` and `.asksend` still match, so `last` is `.asksend`, which can't take focus. Tab from the real last control is then not wrapped (`content.js:333-339`).
- The Tab trap's selector has no `a`, so links (`original`, `.ts`, the sponsor link) never count as first or last.
- The panel itself takes focus on open (`tabindex="-1"`, `content.js:288`) but is not `first`, so Shift+Tab from the panel leaves the modal (`content.js:338`).
- The skip pill's buttons use `all:unset` (`content.js:3034`), which removes the focus ring: they can be reached by keyboard but show no visible focus.
- The main error view and the loading progress are not live regions (see above).

## Source text tests depend on

The extension tests run the extension's **real functions**. They cut them out of the source by literal text and run them in node: through `eval` in `render_checks.js` and `page_transcript_checks.js`, and through `vm` in `probe_checks.js`. Each marker below must stay **byte-for-byte**, including leading spaces, comments and `\n`. Renaming or rewording one breaks the suite.

Markers read through `grab()` or `between()` throw when missing (`render_checks.js:7-15`, `page_transcript_checks.js:10-16`, `probe_checks.js:19-25`). **Markers read through a bare `src.indexOf()` do not**: a missing one gives `-1`, and a check that asserts something is *absent* can then pass without testing anything (see the follow-ups).

The driver is `tests/test_extension_render.py`. It runs `node --check` on all three scripts (`:45-50`) and passes the file paths in this order:

- `render_checks.js`: content, background, options (`:22-28`);
- `page_transcript_checks.js`: content (`:34-42`);
- `probe_checks.js`: content, background (`:56-65`).

### Extraction markers

| Item (start marker → end marker) | File cut | Invariant | Blast radius | Source |
|---|---|---|---|---|
| `const esc = (s) =>` → `function renderSummary` | `content.js` | — | The escaping and markdown tests, and the offer test, which hoists `esc` to a global. | `render_checks.js:16`, `:495-496` |
| `function renderSummary` → `\n\n  // --- ` | `content.js` | `renderSummary` must be followed by a blank line and a `  // --- ` section comment. | The markdown tests. | `render_checks.js:17` |
| `  function renderAnswer(text)` → `\n  function seekTo` | `content.js` | — | The citation and escaping tests. | `render_checks.js:18` |
| `  function fromModal(e)` → `\n  // Swallow every key` | `content.js` | The comment above `shieldKey` is a marker. | The key-shield tests. | `render_checks.js:90` |
| `  function closePolicy(opts, pref)` → `\n  let askLive` | `content.js` | The slice must contain only `closePolicy` and the `txLive`/`pendingOffer` declarations. | The close-policy tests. | `render_checks.js:119` |
| `  function close(opts)` → `  function unmount()` (bare `indexOf`) | `content.js` | Inside the slice, the literals `policy.keepSeg`, `stopSpeak`, `stopAsk`, `!policy.background && creepTimer`, `if (txLive) { stopTranscribe(` and `if (policy.background)` are searched for. | The keepalive and abort-path tests. | `render_checks.js:136-148`, `:176-178`, `:398-400` |
| `  function mount()` → `  function showLoading()` (bare `indexOf`), then `host = document.createElement` | `content.js` | The prologue before `host = document.createElement` must call `unmount()` and not `close()`. | The re-open regression test. | `render_checks.js:155-162` |
| `  function startSummarize(` → `  function endBackground(` (bare `indexOf`) | `content.js` | `m.type === "partial"` must appear before `requestActive = false`. | The re-attach and partial-ordering tests. | `render_checks.js:165-168`, `:284-287` |
| `  function sanitizeField(value, maxLen)` → `\n  // Only offered once` | `content.js` | The slice holds `sanitizeField` and `audioFileName`. | The mp3 file-name tests. | `render_checks.js:191` |
| `  function b64ToBytes(b64)` → `\n  function pushAudioChunk` | `content.js` | — | The download decode test. | `render_checks.js:217` |
| `  function enableDownload(` → `  function renderAudio(` (bare `indexOf`) | `content.js` | — | The download-button tests. | `render_checks.js:234-238` |
| `  function maybeThank(` → `  // --- Follow-up Q&A` (bare `indexOf`) | `content.js` | — | The sponsor tests. | `render_checks.js:247-262` |
| `  function audioRowHtml(pending)` → `\n  // --- The summary as it's written` | `content.js` | — | The partial-view layout tests. | `render_checks.js:266` |
| `  function showPartial()` → `  function showResult(` (bare `indexOf`) | `content.js` | — | The partial escaping tests. | `render_checks.js:274-281` |
| `  function clipTotals(` → `\n  function fmtDur(s)` | `content.js` | — | The clip totals tests. | `render_checks.js:294` |
| `  function fmtDur(s)` → `\n  function fmtTime(s)` | `content.js` | — | The duration format tests. | `render_checks.js:295` |
| `  function updatePill()` → `  function removePill()` (bare `indexOf`) | `content.js` | — | The pill tally tests. | `render_checks.js:314-321` |
| `  function showResult(` → `  function setupVoiceSelect` (bare `indexOf`) | `content.js` | — | The trimmed-note tests. | `render_checks.js:323-327` |
| `  function fmtEstimate(seconds)` → `\n  // A video with no captions` | `content.js` | — | The estimate wording tests. | `render_checks.js:358` |
| `  function fmtEstimate(seconds)` → `\n  // Stop a transcription` | `content.js` | The slice must hold exactly `fmtEstimate` and `showTranscribeOffer`, each starting with `  function `; the test rewrites them to globals. | The offer test, run against a stub DOM. | `render_checks.js:497-498` |
| `  function transcribedNote(p)` → `\n  function toMarkdown` | `content.js` | — | The provenance label tests. | `render_checks.js:442` |
| `  function toMarkdown(p)` → `\n\n` | `content.js` | `toMarkdown` must contain no blank line. | The saved-markdown test. | `render_checks.js:457` |
| `  function probeOutcome(m)` → `\n  function startSummarize` | `content.js` | — | The probe outcome tests. | `render_checks.js:529` |
| `  const CLOCK = ` → `\n  function startSummarize` | `content.js` | The slice must define `clockToMs`, `isoDurationMs`, `readTranscriptRows`, `pageMeta`, `collectPageTranscript`, `rowText` and `rowStartMs` (exported at `page_transcript_checks.js:100-101`). It must also contain `PANEL_WAIT_MS = <digits>`, `PANEL_SETTLE_MS = <digits>` and `PANEL_POLL_MS = <digits>`, which the test rewrites. A renamed constant is not an error: the rewrite silently does nothing and the test runs at the real waits. | Every page-reading test. | `page_transcript_checks.js:95-101` |
| `  function probeOutcome(m)` → `\n  function showLoading()` | `content.js` | Run in a `vm` whose only globals are the fakes listed at `probe_checks.js:32-60`. A new collaborator called from `startSummarize` must be added there. | The probe flow tests. | `probe_checks.js:29`, `:31-64` |
| `const PROBE_TIMEOUT_MS` → `\nasync function handleSummarize(` | `background.js` | `handleProbe` must sit between them. | The worker probe tests. | `probe_checks.js:70` |
| `function httpError(status, detail)` → `\n}\n` | `background.js` | The signature must stay exact, and the function must end with a `}` alone on its line. | The `429` pass-through test. | `probe_checks.js:194-203` |
| `function speakError(status, detail)` → `\n}\n` | `background.js` | The same. | The same. | `probe_checks.js:194-203` |
| `function askError(status, detail)` → `\n}\n` | `background.js` | The same. | The same. | `probe_checks.js:194-203` |
| `// --- T-020 cover listed words: pure logic (BEGIN)` → `// --- T-020 cover listed words: pure logic (END)` | `content.js` | Pure: no DOM, no `api`, no timers. Run in a `vm` with `mute_words.js` loaded first. | `tests/js/mute_checks.js` (9 groups, floors in `test_extension_render.py`). | `mute_checks.js:12-24` |
| `// --- T-020 cover listed words: pure logic (BEGIN)` → `// --- T-020 cover listed words: wiring (END)` | `content.js` | The wiring takes everything it touches through `deps`; the block between the two must stay self-contained. | `tests/js/mute_wiring_checks.js` (10 groups). | `mute_wiring_checks.js:12-26` |
| `// --- T-020 cover listed words: wiring (BEGIN)` | `content.js` | Must exist (the harness checks all three markers before slicing) and sit between the pure block's END and the wiring's END. | The same harness. | `mute_wiring_checks.js:15-17` |
| `// T-020 status text: BEGIN` → `// T-020 status text: END` | `content.js` | One `key: "text",` per line. | The verbatim-strings check in `tests/test_docs_contracts.py`. | `tests/test_docs_contracts.py` `status_strings` |
| `# T-020 align text: BEGIN` → `# T-020 align text: END` | `align.py` | Plain string constants only. | The verbatim-strings check and `test_no_panel_text_carries_backticks`. | `tests/test_align.py` |
| `const ALIGN_TIMEOUT_MS` → `const PROBE_TIMEOUT_MS` | `background.js` | `handleAlign` must sit between them. | `tests/js/align_bg_checks.js`. | `align_bg_checks.js:9-11` |
| `const MODELS = [` | `background.js`, `options.js` | Matched by `re.search(r"const MODELS = \[([^\]]*)\]", src)`. The list's quoted strings must equal `claude_client.MODELS`. | `test_extension_model_lists_match_the_server_allowlist`. | `tests/test_claude_client.py:284-294` |

### Literal source patterns the checks match

These are matched as regexes or with `indexOf` against the source, after `stripComments` removes `//` comments. Rewording the code they match fails the test, or for an "absent" check, passes it.

| Item | File | Source |
|---|---|---|
| `window.addEventListener("keydown", onKey, true)`, and the same for `keyup`/`keypress` with `shieldKey`; `window.removeEventListener("keydown", onKey, true)`; no `addEventListener("keydown"` within 400 characters after `askinput` | `content.js` | `render_checks.js:105-114` |
| `const carried = progressPct;` … `progressPct = carried;`; `lastProgress = m;`; `backgrounded = false;` … `updateProgress(lastProgress.message` | `content.js` | `render_checks.js:172-185` |
| `dl.hidden = true;`, `enableDownload(m.dataUrl)`, `createObjectURL(new Blob(`, `revokeObjectURL` | `content.js` | `render_checks.js:236-242` |
| `stats.videos \|\| 0) < SPONSOR_AFTER_VIDEOS`, `sponsorDismissed: true`, `sponsorShown >= SPONSOR_MAX_SHOWS`, `` sbtn").onclick `` … `sponsorDismissed: true`, `SPONSOR_AFTER_VIDEOS = <n ≥ 10>`, `!stats \|\|`, `esc(String(stats.videos))` | `content.js` | `render_checks.js:249-261` |
| `<li>${esc(text)}</li>`, `renderSummary(text)`, `if (backgrounded) return;`, `partialState && partialState.meta) {` … `showPartial()` | `content.js` | `render_checks.js:276-289` |
| `clipTotals(skipSegs, src)`, `fmtDur(kept)`, `segsComplete ? "" : "so far "`, `trimmedNote()`, `esc(trimmedNote())` | `content.js` | `render_checks.js:316-327` |
| `startSummarize(lastPayload.source_url, lastPayload.video_id, true)`, `type: "summarize", url, videoId, refresh: !!refresh` | `content.js` | `render_checks.js:335-340` |
| `cache.has(videoId) && !refresh`, `refresh: !!refresh` | `background.js` | `render_checks.js:342-344` |
| `startSummarize(url, videoId, false, true)`, `transcribe: !!transcribe`, `offer.installed === false`, `install_hint`, `esc(hint)`, `m.type === "transcribing"`, `txLive = { url }` … `addStopControl(url)`, `if (txLive) addStopControl(txLive.url)`, `safetyTimer = setTimeout`, `if (backgrounded) {` … `endBackground(` … `return;`, `pendingOffer = { offer: m.offer, url, videoId }`, `if (txLive) { stopTranscribe(txLive.url); txLive = null; }`, `stoptx` … `setAttribute("aria-label"`, `port.postMessage({ type: "stopTranscribe", url })`, `transcribe ? 24<5 digits> : 160000` | `content.js` | `render_checks.js:367-406` |
| `transcribe \|\| whisper === "always"` … `TRANSCRIBE_TIMEOUT_MS`, `TRANSCRIBE_TIMEOUT_MS = <n ≥ 600000>`, `whisper,`, `needs_transcription`, `needsTranscription`, `needs_transcription") {` … `gotTerminal = true` | `background.js` | `render_checks.js:410-420` |
| `WHISPER_POLICIES = [` … `]` (the bracket contents, compared with quotes and spaces removed) | `background.js`, `options.js` | `render_checks.js:422-432` |
| `esc(transcribedNote(p))` | `content.js` | `render_checks.js:452-453` |
| `if (refresh \|\| transcribe) summarizeFromPage();` then `else port.postMessage({ type: "probeCache"`; `if (!hit) { summarizeFromPage(); return; }`; `m.type === "probe"` before `requestActive = false;\n      partialState = null;\n      txLive = null;` | `content.js` | `render_checks.js:540-547` |
| `msg.type === "probeCache") handleProbe`; `catch (_) {` … `} finally` … `safePost(port, { type: "probe", hit: false })` | `background.js` | `render_checks.js:550-553` |

## Lists mirrored from the server

The server refuses anything outside its own list, so a list in the extension that drifts from the server's gives the user a `400` or an "Unknown voice" error. Some copies are checked by a test, and some are not.

| Item | Extension copies | Server source of truth | Test-guarded? | Invariant | Blast radius |
|---|---|---|---|---|---|
| `MODELS` | `background.js:13`, `options.js:9`; **also the hard-coded `<option>` values in `options.html:40-43`** | `claude_client.MODELS`, `src/youtube_tldw/claude_client.py:164` | **Yes** for `background.js` and `options.js`, by `tests/test_claude_client.py:284-294`. **No** for `options.html`. | The three JS-visible lists hold the same names. | A model missing from the server gets a `400`. A model missing from `options.html` can't be picked. |
| `WHISPER_POLICIES` | `background.js:15`, `options.js:7`; **also the `<option>` values in `options.html:59-61`** | `WHISPER_POLICIES`, `src/youtube_tldw/server.py:66` | **Partly.** `render_checks.js:422-432` checks that worker and Options agree. **Nothing checks them against the server**, and nothing checks `options.html`. | The same three values everywhere. | A drifted value gets a `400` from the server, or falls back to `"ask"` on the client. |
| `VOICE_OPTIONS` | `content.js:92-106` (the panel's voice picker; it never asks the server) | `audio.VOICES`, `src/youtube_tldw/audio.py:44-58` (served by `/voices`, `audio.py:73-74`) | **No.** | Ids and labels match `audio.VOICES`. They match today: the same 13 ids and labels, in order. A voice added on the server **never appears in the panel**. | The panel's voice list. An id the server doesn't know gets "Unknown voice". |
| `VOICE_FALLBACK` | `options.js:12-26` (used only when `/voices` fails) | `audio.VOICES` | **No.** | The same as above. They match today. | The Options voice list when offline. |
| `RATIOS` | `options.js:8`, and the `<option>` values in `options.html:49-52` | none (the worker sends any 0 < r ≤ 1, `background.js:51`) | **No.** | `options.js` and `options.html` list the same values. | A value missing from `RATIOS` is saved as `"auto"`. |
| `sanitizeField` | `content.js:3109-3126` (the mp3 download name) | `naming.sanitize_field` (per the comment at `content.js:3109-3111`) | **Partly**: `render_checks.js:187-211` checks fixed expected outputs, not the server function. | The same file name as the server's own naming. | The names of downloaded files. |

## Data stores

tldw has no database. Everything it keeps is a file under the user's home directory or temp directory, or a value in a process's memory. Record shapes, examples and the safe-change procedure live in [`data-model.md`](data-model.md); this table is the inventory and its blast radius. `chrome.storage.local` is inventoried in the extension storage section (Frontend), not here.

Every home-relative path below is a module constant evaluated at import time. `tests/test_isolation.py:18-27` fails the build when a new `NAME = Path.home() / …` constant appears in `src/` without a matching entry in `HOME_PATHS` (`tests/conftest.py:26-37`), so a new store must be added there and here together.

### On-disk stores tldw writes

| Store | Writer → readers | Invariant | Blast radius | Source |
|---|---|---|---|---|
| `~/.cache/youtube-tldw/transcripts/` · `CACHE_DIR` | Server `_cache_put` (`server.py:309-317`) → `txcache.get` via `_cache_get` / `_cache_source` (`server.py:338`, `server.py:356`); `tldw cache` sizes and cleans it (`cli.py:390-398`) | One file per video, `<video_id>.json`, holding top-level `saved_at`, `meta`, `cues`, and an optional `source` (`txcache.py:75-82`). Reads are tolerant: `source` defaults to `captions`, and a missing, truncated or older-shape file is a miss, never an error (`txcache.py:39-58`). TTL 14 days (`TTL_SECONDS`, `txcache.py:26`); cap 500 files, oldest mtime pruned on every write (`MAX_ENTRIES`, `txcache.py:27`, `txcache.py:95-99`). | Every route that needs a transcript: `/summarize`, `/summarize/stream`, `/summarize/cached`, `/segments/stream`, `/ask/stream`. A miss means a fresh yt-dlp fetch (rate-limit exposure) or, for an `audio:<model>` entry, minutes of Whisper CPU. A key change orphans every entry. | `txcache.py:21`, `txcache.py:30-99` |
| `~/.cache/youtube-tldw/summaries/` · `SUMMARY_DIR` | `put_summary` from `_run_stream` (`server.py:1326-1327`) → `get_summary` from `_run_stream` and `_run_cached` (`server.py:1225`, `server.py:1359`) | File `<video_id>-<16 hex>.json`, named by `_summary_key` (`server.py:748-770`) from the video id, `backend_fingerprint()`, the ratio, `PROMPT_FINGERPRINT` and the transcript source; the exact composition, and what `PROMPT_FINGERPRINT` does and doesn't cover, is owned by `data-model.md` § 2. Holds top-level `saved_at` and `payload`; `payload` must be a dict or the read is a miss (`txcache.py:108-117`). The payload is the `_to_payload` dict (`server.py:1904-1921`) and is sent to the extension verbatim with `cached: true` (`server.py:1233`, `server.py:1368`). TTL and cap: `TTL_SECONDS` and `MAX_ENTRIES`, values in `data-model.md` § 2; own directory so key-moment writes never push summaries out (`txcache.py:23-25`). | The extension's cached-summary path (`/summarize/cached`, and the cache-hit branch of `/summarize/stream`). A payload key added to `_to_payload` is absent from entries written before it for up to 14 days, because no fingerprint covers the payload shape. | `txcache.py:22`, `txcache.py:108-130` |
| `~/.cache/youtube-tldw/segments/` · `SEGMENT_DIR` | `_seg_cache_put` from the prefetch thread and `/segments/stream` (`server.py:401-405`, `server.py:511`, `server.py:1628`) → `_seg_cache_get` (`server.py:408-419`) | File `<video_id>-<16 hex>.json`, named from the `_seg_key` tuple (`server.py:376-398`), which includes `SEGMENT_FINGERPRINT`, `backend_fingerprint()` and the transcript source; the exact composition is owned by `data-model.md` § 3. Holds top-level `saved_at`, `meta`, `segments` (a list, else a miss) (`txcache.py:140-164`). A request with `max_length` builds no key and is never cached (`server.py:1554-1558`). TTL and cap: `TTL_SECONDS` and `MAX_ENTRIES`, values in `data-model.md` § 3. | `/segments/stream` (instant replay of key moments) and the background prefetch. A miss costs one model call (most of the spend, per `usage.py:59-62`). | `txcache.py:25`, `txcache.py:140-164` |
| `~/.config/youtube-tldw/usage.jsonl` · `USAGE_FILE` | `usage.record`, `note_served`, `note_video` (`usage.py:132-205`) → `read_rows` / `stats` (`usage.py:208-245`), `tldw usage` (`cli.py:457-573`), and the `stats` key of every streamed or probed summary `result` (`server.py:1234`, `server.py:1328`, `server.py:1369`; the buffered `/summarize` has none, `server.py:1188`) | Append-only JSON Lines, one object per line, appended under a process-local lock and chmod 0600 after every append (`usage.py:184-189`). Rows are told apart by `kind`; `video` and `segments_served` rows are not model calls and are excluded from call counts (`cli.py:488`). Bad lines are skipped on read (`usage.py:227-245`), but a wrong-typed value is not: a non-numeric `video_ms` or `read_ms` makes `stats()` raise (`usage.py:218-219`), uncaught at all three callers. A field's type never changes. No cap, no rotation, no cleanup. | `tldw usage`, and the "hours saved" `stats` the panel shows. A renamed field silently zeroes a column in `tldw usage`, since readers use `.get(...) or 0`. | `usage.py:26`, `usage.py:132-245` |
| `~/.config/youtube-tldw/config.json` · `CONFIG_FILE` | `config.set_value` / `config.unset` from `tldw config` (`config.py:27-41`, `cli.py:349-364`) → `config.get`, which re-reads the file on every call (`config.py:15-24`) | A JSON object; anything else, or invalid JSON, reads as `{}` (`config.py:15-20`). Every value is a string (the CLI writes only strings, `cli.py:351`); a hand-edited non-string is not tolerated by every reader (config-keys table, and `data-model.md` § 5). Written with plain `write_text`: not atomic, no temp file, no mode change (`config.py:31`, `config.py:40`). Because the server re-reads it per call, `tldw config set` takes effect on a running server without a restart. | Every config key in the config-keys table below. A torn or invalid file reads as `{}`, so every setting silently reverts to its default, and the next `set` rewrites the file holding only that one key. | `config.py:12-41` |
| `~/.config/youtube-tldw/token` · `TOKEN_FILE` | `load_or_create_token` at `tldw serve` start (`server.py:692-708`) → the same function on the next start | Plain text, the token with no newline; created only when no explicit token is given and the file is missing or empty; `chmod 0600` runs after the write, not at creation (`server.py:704-707`, `config.py:44-56`). Never rotated by tldw. | Every authenticated route. A changed token means every installed extension gets 401 until it is reconfigured. | `server.py:689-708` |
| `~/.cache/youtube-tldw/voices/` · `VOICE_DIR` | `piper.download_voices` subprocess (`audio.py:248-249`); `mark_voice_spoken` sets an `.onnx` file's mtime (`hygiene.py:148-154`); `ensure_voice_tracking` creates `.spoken-tracking-since` only if it is missing and never re-touches it (`hygiene.py:138-145`, `hygiene.py:142-143`) → `PiperVoice.load` (`audio.py:28-39`); hygiene rules | Files `<model>.onnx` and `<model>.onnx.json`, the model name taken only from the `VOICES` allowlist (`audio.py:44-58`). The `.onnx` mtime means "last spoken with", and previews don't update it (`audio.py:300-303`, `server.py:1885-1887`). Files whose stem is not in the allowlist are deleted at every `tldw serve` start (`server.py:104-107`). | `/speak`, `/speak/stream`, `/preview`, CLI `--render-audio`. Renaming a model in `VOICES` deletes the old files on the next start. Touching mtimes (restoring a backup, copying files) changes which voices `tldw cache clean` judges idle. | `audio.py:61`, `hygiene.py:114-190` |
| `~/.cache/youtube-tldw/whisper/` · `_CACHE_DIR` | faster-whisper's `WhisperModel(download_root=…)` (`whisper_transcribe.py:199-210`) → the same | The layout inside is faster-whisper's and Hugging Face's, not tldw's; tldw only creates the directory and measures its size (`whisper_transcribe.py:200`, `cli.py:422`). Never deleted by tldw (`hygiene.py:11-14`, `cli.py:437-439`). Sizes per model run from 75MB to 3.1GB (`whisper_transcribe.py:32-38`). | Transcription of captionless videos. Removing it costs a re-download on next use. A model changed in `whisper_model` adds a second model; nothing removes the first. | `whisper_transcribe.py:23` |
| `~/.cache/youtube-tldw/claude/` · `CLAUDE_DIR` | Created with mode 0700 before every stock `claude` call (`claude_client.py:147-152`) | The working directory for tldw's own `claude` calls, so the CLI files Q&A sessions under a project of its own rather than the user's (`claude_client.py:113-119`). If it can't be created, calls run from the server's working directory instead (`claude_client.py:150-151`). Whether the CLI writes any file into it is `UNKNOWN — needs human confirmation`. Custom backends never use it (`claude_client.py:155-159`). | Where Q&A sessions land (next row). | `claude_client.py:119-152` |
| `~/.claude/projects/<encoded tldw claude dir>/` | The `claude` CLI itself, only for persisted calls. Only Q&A persists (`stream_text` passes `persist=True`, `claude_client.py:576-577`); every other call passes `--no-session-persistence` (`claude_client.py:200-201`) | Owned and formatted by the Claude CLI; tldw never reads, writes or deletes these files. It keeps only the session id: in memory for 4 hours (`_ask_sessions`, `server.py:422-431`) and in every `usage.jsonl` model-call row (`usage.py:42`). `--resume` finds a session by id from any directory (`claude_client.py:117-118`). A stale id falls back to a full call (`ask.py:133-139`). Retention is the CLI's own: `UNKNOWN — needs human confirmation`. | `/ask/stream` follow-ups. Moving `CLAUDE_DIR` strands existing sessions under the old project, but resume still works by id. | `claude_client.py:113-119`, `claude_client.py:189-202` |
| Output dir, default `~/Downloads/youtube-tldw/tldws` · `DEFAULT_OUTPUT` | CLI only (`tldw <url>`): `textmode.write_markdown`, and `shutil.move` for mp4/mp3 (`cli.py:153-273`) | Base is resolved as `--output-dir` > `TLDW_OUTPUT_DIR` > config `output_dir` > default (`cli.py:20-32`, `cli.py:82`). Files land in `text/`, `video/` or `audio/` (`naming.py:65`), named `{channel} - {title} - tl;dw - {length}.{ext}` after sanitizing (`naming.py:52-55`); a containment check refuses anything outside the subdirectory (`naming.py:66-69`); existing names get ` (2)`, ` (3)` (`naming.py:74-84`). The server never writes here. | The user's own files. tldw never cleans or lists them. A change to the name template changes where new files land, nothing else. | `cli.py:20`, `naming.py:52-84` |
| Temp dir `youtube-tldw-` | `tempfile.mkdtemp` in the CLI run (`cli.py:288`) | Holds the subtitle files, downloaded video or audio, clips and render intermediates for one CLI run; removed in `finally` (`cli.py:294-295`). Not matched by the startup sweep's glob, so a run killed before its `finally` leaves it behind. | Disk only. | `cli.py:288-295` |
| Temp dir `youtube-tldw-tx-` | `tempfile.mkdtemp` in `core.fetch_transcript` (`core.py:65`) | Holds a yt-dlp subtitle download for one transcript fetch; removed in `finally` (`core.py:86-87`). Not swept at startup either. | Disk only. | `core.py:65-87` |
| Temp dir `youtube-tldw-audio-` · `AUDIO_TMP_PREFIX` | `tempfile.mkdtemp` in the transcription thread (`server.py:236`) | Holds one video's downloaded audio for Whisper (capped by `--max-filesize`, `whisper_transcribe.py:85`, `whisper_transcribe.py:127`); removed in `finally` (`server.py:282`). Anything older than `_stale_audio_age()` (at least 6 hours, `server.py:77-86`) is swept at `tldw serve` start and by `tldw cache clean`, by age, never by "no server running" (`hygiene.py:83-111`). | A second `tldw serve` on another port must not lose a live job's download, which is why the rule is age only. | `hygiene.py:27`, `server.py:236` |
| `~/.cache/youtube-tldw/align/` · `ALIGN_DIR` (store 13, "Alignment cache", T-020) | `align.put_window` from `_run_align` → `align.get_window` from `_run_align`; `tldw cache` reports it as "word timings" and `tldw cache clean` removes expired entries, the cleanable areas chosen **by name** (`cli.py:391-399`) | One file per window, `<video_id>-<16 hex>.json`, named from `align.cache_key`: `video_id\|start_ms\|end_ms\|whisper_model\|lang=auto\|ALIGN_FINGERPRINT`. The fingerprint (lazy, memoised; none when a library version can't be read) covers every input that shapes the result; the composition is owned by `data-model.md` § 13. **The word list is not in the key**: matching happens in the browser. Holds `saved_at`, `video_id`, `start_ms`, `end_ms` and `words` (`[[s, e, w], …]`, absolute ms). **Validated on read**: anything off, including a mismatched video or window, is a miss. TTL 14 days (`TTL_SECONDS`); its own cap `ALIGN_MAX_ENTRIES` (5,000), pruned once per request by `txcache._prune(…, cap=…)`. Atomic `.tmp` then `replace`. | `/align/stream`: a hit means no YouTube call and no CPU. A key change re-times every window once. | `align.py:28`, `align.py:143-239` |
| `*.tmp` in the three cache directories | `txcache.put*` writes `<name>.tmp`, then `replace`s it onto `<name>.json` (`txcache.py:84-86`, `txcache.py:124-127`, `txcache.py:158-161`) | `.tmp` is reserved for in-flight writes. Pruning counts only `*.json` (`txcache.py:97`). `tldw cache clean` deletes a `.tmp` older than 1 hour (`STRAY_TMP_AGE_S`, `hygiene.py:28`, `hygiene.py:208-211`). The temp name is per key, not per writer, so two concurrent writes of the same key share one `.tmp`. | Readers never see a half-written `.json`; a race between two writers costs at most a miss. | `txcache.py:84-86` |

### On-disk stores tldw only reads

| Store | Reader | Invariant | Blast radius | Source |
|---|---|---|---|---|
| Hugging Face hub cache: `$HF_HUB_CACHE`, else `$HF_HOME/hub`, else `~/.cache/huggingface/hub` | `hygiene.huggingface_whisper_models` from `tldw cache` / `tldw cache clean` (`cli.py:443-454`) | Read only: `models--Systran--faster-whisper-*` directories are listed and sized, with an `rm -rf` hint, and never deleted (`hygiene.py:224-237`). The path is computed inside a function, so it is not a `HOME_PATHS` constant; tests clear `HF_HOME` / `HF_HUB_CACHE` and redirect `HOME` instead (`tests/conftest.py:54`, `tests/conftest.py:69-72`). | Output of `tldw cache` only. | `hygiene.py:224-237` |
| System fonts: `/System/Library/Fonts/Supplemental/Arial Bold.ttf`, `/System/Library/Fonts/Supplemental/Arial.ttf`, `/System/Library/Fonts/Helvetica.ttc` | `overlays._font` for video-mode overlays | Read only, with a fallback to Pillow's bundled font when none exists (`overlays.py:24-35`). | CLI video mode's badge, banner, timestamps and end card. | `overlays.py:17-19` |

### Third-party stores tldw's subprocesses use

Not tldw's, and never read, cleaned or reported by it (`cli.py:390-392`). Detail is in `data-model.md`, "Third-party stores tldw's subprocesses use".

| Store | Used by | Invariant | Blast radius | Source |
|---|---|---|---|---|
| yt-dlp's own cache: location and layout `UNKNOWN — needs human confirmation` | Every `yt-dlp` call tldw makes (`metadata.py:82`, `metadata.py:213`, `metadata.py:245`, `whisper_transcribe.py:125`) | tldw never passes `--no-cache-dir` (grep over `src/youtube_tldw/`: no hits), so whatever yt-dlp caches by default is written on every fetch. Contents and retention are `UNKNOWN — needs human confirmation`. | Disk only, outside `tldw cache`'s view. | `metadata.py:82-83` |
| The browser's cookie database: location and layout `UNKNOWN — needs human confirmation`. Class **secret** | `yt-dlp`, only on metadata and subtitle calls, only when `TLDW_YTDLP_COOKIES` or `ytdlp_cookies` names a browser in `COOKIE_BROWSERS` (`metadata.py:48-55`, used at `metadata.py:83`, `metadata.py:215`) | Read by yt-dlp, never by tldw code; tldw passes only the browser's name. Whether yt-dlp keeps a copy is `UNKNOWN — needs human confirmation`. | The user's signed-in browser sessions are exposed to the `yt-dlp` process. | `metadata.py:48-55` |

### In-memory stores

All server registries live in the `tldw serve` process, are shared by the per-request threads of `ThreadingHTTPServer` (`server.py:1924-1925`), are guarded by `_cache_lock` (`server.py:37`) unless noted, and vanish on restart. TTLs use `time.monotonic()`. `_prune` drops expired entries, then the soonest-to-expire, down to 64 (`server.py:298-306`). `tests/conftest.py:107-113` clears the registries around each test.

| Store | Key → value | TTL / cap | Invariant | Blast radius | Source |
|---|---|---|---|---|---|
| `_transcript_cache` | video id → `(expires_at, meta, cues, source)` (four items; the comment at `server.py:38` still says three) | 4h (`_CACHE_TTL`, `server.py:35`), renewed on a touch read; cap 64 (`server.py:36`) | Write-through to the disk transcript cache (`server.py:317`); a memory miss falls back to disk and refills memory (`server.py:337-342`). | Q&A keeps its transcript alive by touching it (`server.py:1796`). | `server.py:38`, `server.py:309-357` |
| `_seg_cache` | `_seg_key` tuple → `(expires_at, meta, segments)` | 4h; cap 64 | Write-through to the disk segment cache (`server.py:405`). | `/segments/stream`, prefetch dedupe (`server.py:455-461`). | `server.py:39`, `server.py:401-419` |
| `_seg_prefetch_events` | `_seg_key` → `threading.Event` while a selection runs | Removed when the selection ends (`server.py:481-484`, `server.py:1640-1644`) | At most one selection per key at a time, prefetch or live. | A key left registered would keep `/segments/stream` waiting until `SEGMENTS_TIMEOUT`. | `server.py:42` |
| `_seg_progress` | `_seg_key` → `{found, pos_ms, duration_ms}` | Removed with the event (`server.py:483`) | Published by the prefetch for waiters' progress lines. | `/segments/stream` progress messages. | `server.py:49`, `server.py:489-497` |
| `_ask_sessions` | video id → `(expires_at, session_id)` | 4h; cap 64 | The only link from a video to its Claude CLI session; never persisted. | `/ask/stream` follow-ups re-send the whole transcript after expiry or a restart. | `server.py:45`, `server.py:422-431` |
| `_tx_jobs` | `(video_id, model)` → job dict (the comment at `server.py:64` says video id only) | While the job runs; popped in `finally` (`server.py:284-285`) | One transcription per video and model; a second caller joins the first job unless it was cancelled or failed (`server.py:203-209`). | `/summarize/stream` with `whisper`, `/transcribe/stop`. | `server.py:64`, `server.py:187-295` |
| `_captionless_meta` | video id → `(expires_at, NoTranscriptError with .meta)` | 30 min (`_CAPTIONLESS_TTL`, `server.py:853`); cap 64 | Only an answer is cached, never a failure; a remembered answer is not re-stored, so the clock isn't renewed (`server.py:1288-1292`). | Saves one yt-dlp call when an offer to transcribe is accepted. | `server.py:848-871` |
| `_preview_cache` | voice model → mp3 bytes | No TTL; bounded by the 13 allowlisted voices; own `_preview_lock` (`server.py:552`) | Filled only after a successful synthesis. | `/preview`. | `server.py:551`, `server.py:1872-1898` |
| `audio._voice_cache` | model path → loaded `PiperVoice` (LRU) | At most 2 loaded (`_MAX_LOADED_VOICES`); own `_voice_lock` | Bounded so previewing every voice can't hold all 13 models in memory. | Speech latency (a load takes 1-2s). | `audio.py:19-39` |
| `audio._piper_state` | `None` / `True` / reason string | Process lifetime | The Piper import check runs once per process. | Every speech route's 503 or error text. | `audio.py:184-216` |
| `videomode._filter_cache` | ffmpeg filter name → bool | Process lifetime | One `ffmpeg -filters` probe per name. | CLI caption style. | `videomode.py:27-39` |
| `_claude_slots`, `_speech_slots`, `_tx_permit` | Counted pools (6 claude, 2 speech, 1 transcription) | Process lifetime | Counts are recorded in the concurrency section (Backend). | Every slot-taking route. | `server.py:556-651`, `server.py:65` |
| `usage._local`, `claude_client._local` | Thread-local current interaction / pinned model | Per thread | A new thread must re-enter both scopes itself, which is why the prefetch thread does (`server.py:499-501`). | Usage attribution, cache keys built from `backend_fingerprint()` (`server.py:385-388`). | `usage.py:28`, `claude_client.py:165` |
| Extension `cache`, `audioCache`, `previewCache` (background); `previewCache` (options) | video id → summary payload; `videoId|voice` → `data:` URL; voice → `data:` URL / object URL | No TTL, no cap, no delete; live until the service worker or page goes away | The summary map is keyed by video id only, not model or ratio, so it serves its copy until a `refresh` (`background.js:205`). Detail is Frontend's. | The panel's instant re-open. | `background.js:25-26`, `background.js:523`, `options.js:81` |

## Environment variables

Every key read from the environment by `src/youtube_tldw/`. tldw reads no `.env` file. All are optional, and none is required to start.

| Variable | Controls | Default | Invariant | Blast radius | Source |
|---|---|---|---|---|---|
| `TLDW_LLM_CMD` | The model backend command, `shlex.split`, reading the prompt on stdin. It outranks config `llm_cmd`, which outranks the `claude` CLI. `--llm-cmd` writes it into the process environment (`cli.py:278-279`, `cli.py:599-600`). | Unset: the `claude` CLI | Operator config only, never request-controlled (`claude_client.py:9-10`). It is part of `backend_fingerprint()`, so changing it changes every summary and segment key (`claude_client.py:227-235`). | Every model call; the summary and segment caches; `/health` `backend`. | `claude_client.py:210`, `cli.py:281`, `cli.py:346` |
| `TLDW_OUTPUT_DIR` | The CLI's output base dir, `expanduser`'d. | Unset: config `output_dir`, else `~/Downloads/youtube-tldw/tldws` | `--output-dir` still wins. | CLI output location only. | `cli.py:26-28` |
| `TLDW_TOKEN` | The server's bearer token when `--token` isn't given. An explicit token is never written to the token file (`server.py:698-699`). | Unset: the token file, else a new one | — | Every authenticated route. | `cli.py:603` |
| `TLDW_YTDLP_COOKIES` | Adds `--cookies-from-browser <name>` to yt-dlp metadata and subtitle calls; it outranks config `ytdlp_cookies`. | Unset: no cookies | Only a name in `COOKIE_BROWSERS` (`chrome`, `chromium`, `brave`, `edge`, `firefox`, `safari`, `opera`, `vivaldi`) reaches argv; anything else is silently ignored (`metadata.py:48-55`). | yt-dlp's authentication to YouTube (it reads that browser's cookies). | `metadata.py:53` |
| `HF_HUB_CACHE` | Where `tldw cache` looks for Hugging Face's faster-whisper models. | Unset: `$HF_HOME/hub` | Read only. | `tldw cache` report only. | `hygiene.py:228` |
| `HF_HOME` | The same, one level up. | Unset: `~/.cache/huggingface` | Read only. | `tldw cache` report only. | `hygiene.py:232` |
| `HF_HUB_DOWNLOAD_TIMEOUT` | The per-request timeout of the faster-whisper model download. tldw **writes** it with `setdefault("…", "60")` into the server's own process environment before the first model load, so every later subprocess inherits it. | `60` if unset | A user-set value is kept. | A stalled download would otherwise hold the single transcription permit forever (`whisper_transcribe.py:195-198`). | `whisper_transcribe.py:199` |
| `DISABLE_PROMPT_CACHING` | Not read for its value. tldw sets it to `1` in a copy of the environment for one-shot `claude` calls, and removes every key starting with that name for Q&A (`claude_client.py:135-142`). | — | Passed per call, never by changing `os.environ`, because Q&A runs alongside summaries (`claude_client.py:132-133`). | Model cost: one-shot caching off ($0.122 → $0.070 per selection, `claude_client.py:125-128`); Q&A caching on. | `claude_client.py:139-142` |
| `CLAUDE_CODE_OAUTH_TOKEN` | Presence only, printed by name at `tldw serve` start; the value is never read or printed. | — | Names only, never values (`server.py:660`). | Startup advice line. | `server.py:666` |
| `ANTHROPIC_API_KEY` | Presence only, as above, with a warning that calls may bill the API. | — | Names only. | Startup advice line. | `server.py:667` |

**Inherited, not read by tldw code:** stock `claude` calls get a full copy of the environment (`claude_client.py:135`), and every other subprocess inherits it unchanged (`proc.py:82-90`, `proc.py:123-126`). That is how the CLI's own login variables reach `claude`. **Implicit (standard library, not an `os.environ` call in tldw):** the home directory behind every `Path.home()` constant, the temp directory behind `tempfile.mkdtemp` / `gettempdir` (`hygiene.py:107`), and the executable search path behind `shutil.which` (`proc.py:29`, `proc.py:52`). Their names are left out of the table on purpose, so the reverse check doesn't treat them as tldw keys.

## Config keys

Stored in `~/.config/youtube-tldw/config.json`. `tldw config set <key> <value>` turns `-` into `_` and accepts **any** key; only the four in `keymap` have a spelling map, and only three are validated (`cli.py:318-337`, `cli.py:349-360`). Values set from the CLI are always strings (`cli.py:351`). An unknown key is stored and never read.

| Key (`tldw config` spelling) | Controls | Default | Validation | Invariant | Blast radius | Source |
|---|---|---|---|---|---|---|
| `output_dir` (`output-dir`) | CLI output base dir | `~/Downloads/youtube-tldw/tldws` | Stored `expanduser().resolve()`d (`cli.py:356-357`); a hand-edited non-string raises `TypeError` at `Path(saved)` (`cli.py:31`) | Below `--output-dir` and `TLDW_OUTPUT_DIR` | CLI output only | `cli.py:29` |
| `whisper_model` (`whisper-model`) | faster-whisper model size | `small` (`whisper_transcribe.py:52`) | Must be in `VALID_MODELS` (`cli.py:327-329`); an invalid stored string or a number falls back to `small` (`whisper_transcribe.py:76-81`), but a non-empty list or object raises `TypeError` at `saved in VALID_MODELS` (`whisper_transcribe.py:79`) | Part of the transcript source `audio:<model>`, so changing it makes cached audio transcripts and their summaries miss (`server.py:878-890`) | Captionless videos; summary keys; stale-audio sweep age | `whisper_transcribe.py:78` |
| `whisper` (`whisper`) | Pins the captionless-video policy for every request | Unset: the request's `whisper` field, else `off` | `off`, `ask`, `always` (`cli.py:319-320`); a stored value outside `WHISPER_POLICIES` is ignored (`server.py:152-154`) | Outranks whatever the extension sends (`server.py:148-150`) | `/summarize/stream` | `server.py:152` |
| `whisper_max_minutes` (`whisper-max-minutes`) | Longest video that may be transcribed | 120 minutes (`MAX_TRANSCRIBE_MS`, `server.py:70`) | Positive whole number (`cli.py:321-326`); a bool or non-integer stored value falls back to the default (`server.py:125-132`); a stored value of 0 or below is clamped to 1 minute, not defaulted (`max(1, minutes)`, `server.py:133`) | Checked against yt-dlp's duration, never the request's (`server.py:1382-1385`) | 413 on long captionless videos; `_stale_audio_age()` | `server.py:126` |
| `llm_cmd` (`llm-cmd`, generic mapping) | Model backend command, below `TLDW_LLM_CMD` | Unset: `claude` CLI | None; a hand-edited non-string raises `AttributeError` at `.strip()` when `TLDW_LLM_CMD` is unset (`claude_client.py:210`) | Same as `TLDW_LLM_CMD`; printed by `tldw config get` (`cli.py:340-347`) | Every model call; cache keys | `claude_client.py:210`, `cli.py:281`, `cli.py:346` |
| `ytdlp_cookies` (`ytdlp-cookies`, generic mapping) | Browser whose cookies yt-dlp uses, below `TLDW_YTDLP_COOKIES` | Unset: none | None at set time; ignored at use unless in `COOKIE_BROWSERS`; a hand-edited non-string raises `AttributeError` at `.strip()` when `TLDW_YTDLP_COOKIES` is unset (`metadata.py:53-54`) | Same as `TLDW_YTDLP_COOKIES` | yt-dlp metadata and subtitle calls | `metadata.py:54` |

## Feature flags

N/A — tldw has no feature-flag system: no flag store, no rollout percentages, no remote config. Behaviour that a flag would normally gate is chosen by the environment variables and config keys above (`TLDW_LLM_CMD` / `llm_cmd` picks the backend; `whisper` pins transcription to `off`, `ask` or `always`; `TLDW_YTDLP_COOKIES` / `ytdlp_cookies` turns on browser cookies) and by opt-in request fields, which are in the request-keys section (Backend).

## Third-party integrations and subprocesses

Every subprocess goes through `proc.run` or `proc.stream_filter` (argv list, `shell=False`, untrusted data only as discrete argv items or stdin; `proc.py:1-6`), except the streaming `claude` call, which uses `subprocess.Popen` directly with the same rules (`claude_client.py:333-343`). A missing binary is `TldrError` ("not installed or not on PATH"); a timeout is `TldrTimeoutError`; a non-zero exit is `TldrError` with the last 3 stderr lines (`proc.py:91-101`). The server maps `ClaudeError` to 502 and `TldrTimeoutError` to 504 (`server.py:711-717`). The CLI checks `yt-dlp`, `ffmpeg`, `ffprobe` (and `claude` for the default backend) up front (`cli.py:280-283`); the server checks nothing at start.

### Subprocess executables

| Executable | What, where | Timeout | Failure expectation | Invariant | Blast radius |
|---|---|---|---|---|---|
| `claude` | Every model call on the default backend: buffered `ask_json` / `ask_text` (`claude_client.py:587-646`) and streaming `_stream_deltas` (`claude_client.py:304-495`). argv in the model I/O section below. | Per caller: 120s server summary (`REQUEST_TIMEOUT`, `server.py:682`), 300s segments and Q&A (`server.py:683`, `server.py:549`), 600s CLI (`cli.py:37`), 300s default (`claude_client.py:34`); streaming uses a kill timer plus a 5s exit grace (`claude_client.py:301`, `claude_client.py:390-392`) | Not installed → 502 on buffered calls (`ask_json`/`ask_text`, `claude_client.py:598-599`, `:634-635`); on streamed calls (`/summarize/stream`, `/segments/stream`, `/ask/stream`, prefetch), `Popen` raises `FileNotFoundError` (`claude_client.py:333-343`), which nothing catches, so the result is `500` `internal error` (derived from the code; see `api.md` § 11); timeout → 504; non-zero exit or an `is_error` result → 502 with the CLI's own message (`claude_client.py:79-97`, `claude_client.py:409-416`); invalid JSON → one repair retry, then 502 (`claude_client.py:621-646`) | Prompt and transcript on stdin only; `--model` only from the `MODELS` allowlist (`claude_client.py:164`); runs with no tools, no MCP and no settings sources (`claude_client.py:108-111`) | All summarize, segment and ask routes; the CLI |
| `TLDW_LLM_CMD` / `llm_cmd` command | Replaces `claude` for every call (`claude_client.py:205-213`); streamed through `stream_filter` (`claude_client.py:272-291`) | Same as `claude` | Empty stdout → 502 (`claude_client.py:58-63`); non-zero exit → 502 | No `cwd` or environment change: it runs in the server's directory with its environment (`claude_client.py:155-159`); no usage is recorded (`claude_client.py:66-70`). A command whose first word is `claude` is treated as the stock CLI by `is_claude_cli()` (`claude_client.py:251-254`), so streaming calls rebuild the stock argv and ignore the rest of the command | Same as `claude` |
| `yt-dlp` | Metadata `-J` (`metadata.py:79-100`); subtitle download (`metadata.py:205-234`); video download, CLI video mode only (`metadata.py:237-260`); audio download for Whisper (`whisper_transcribe.py:102-151`) | 120s metadata and subtitles; 1800s video; 600s audio, or what's left of the job budget with a 30s floor (`whisper_transcribe.py:287-289`) | 429 text is rewritten as a "rate-limiting this machine" message (`metadata.py:60-76`); no output file → `TldrError` | The URL is always `watch_url(<validated 11-char id>)` (`metadata.py:25-26`). Only the audio download passes `--ignore-config`, `-f bestaudio`, `!is_live` and `--max-filesize` (`whisper_transcribe.py:124-130`); the metadata and subtitle calls add `_RETRY` and cookies (`metadata.py:56-57`, `metadata.py:82-83`, `metadata.py:215`) | Every transcript fetch not supplied by the page; Whisper; CLI video |
| `ffmpeg` | mp3 extraction (`audio.py:219-227`); streaming PCM → mp3 encoder for speech (`audio.py:274-278`, `audio.py:316`); filter probe, clips, end card, concat and caption passes in CLI video mode (`videomode.py:33`, `videomode.py:64-91`, `videomode.py:225-233`, `videomode.py:258-368`) | 1800s extraction and clips; 600s speech (`SPEAK_TIMEOUT`, `server.py:687`) and end card; 3600s concat passes; 30s probe | Non-zero exit → `TldrError`; a missing output file → `TldrError` | Every argv item is a server-side constant or a path tldw built (`audio.py:275`) | `/speak`, `/speak/stream`, `/preview`; CLI audio and video |
| `ffprobe` | Duration of a rendered file (`videomode.py:98-109`) | 60s | Unparseable output → `TldrError` | — | CLI video mode and `--render-audio` naming (`cli.py:184`) |
| `sys.executable` running `piper.download_voices` | Downloads a Piper voice into `VOICE_DIR` on first use (`audio.py:230-264`). There is no `piper` executable: synthesis is the in-process `piper.PiperVoice` library (`audio.py:33-34`). | 180s per attempt, 3 attempts, 1.5s apart; partial `<model>.onnx*` files deleted between tries (`audio.py:244-259`) | After 3 failures: `TldrError` "Couldn't download the voice…" (`audio.py:260-264`) | The model name comes only from the `VOICES` allowlist (`audio.py:41-43`) | First use of each voice |

### In-process libraries that reach the network

| Library call | What, where | Timeout | Failure expectation | Invariant | Blast radius |
|---|---|---|---|---|---|
| `WhisperModel` (faster-whisper) | Built only in `_load_model` (`whisper_transcribe.py:184-213`), which `transcribe` and `/align/stream` both call. It loads, and for a transcription on first use downloads, the model into `~/.cache/youtube-tldw/whisper/` from Hugging Face. `/align/stream` passes `local_only=True` (`local_files_only=True`), and `model_on_disk` asks `download_model(…, local_files_only=True)`, so word timing **never** downloads a model (T-020) | `HF_HUB_DOWNLOAD_TIMEOUT` (60s default) per request; the job deadline covers transcription, not the download (`whisper_transcribe.py:195-198`) | An `ImportError` is reported with the reinstall hint (`server.py:267-273`); other errors fail the job | CPU only, int8, half the cores (`whisper_transcribe.py:201-210`) | Captionless videos |
| `piper.PiperVoice` | Local synthesis; no network | Covered by the ffmpeg encoder's timeout | Unusable native phonemizer → a clear `TldrError` (`audio.py:187-216`) | — | Speech routes |

### Outbound network not via a subprocess

| Call site | What | Allowlist / limits | Timeout | Failure expectation | Blast radius | Source |
|---|---|---|---|---|---|---|
| `fetch_caption_text` → `urllib.request.urlopen` | GETs a caption-track URL that yt-dlp's metadata already returned, instead of a second yt-dlp run (`metadata.py:157-163`) | `https` only; host `youtube.com`, `googlevideo.com`, `ytimg.com` or a subdomain; URL at most 4096 chars (`is_safe_caption_url`, `metadata.py:129-139`); body read capped at `MAX_CAPTION_BYTES` (8MB) and refused if larger (`metadata.py:42`, `metadata.py:173`, `metadata.py:180-181`); fixed browser User-Agent header (`metadata.py:166-170`). The allowlist checks the URL tldw requests; `urlopen` follows redirects, and the redirect target is not re-checked. | 120s, from `subtitle_text`'s default (`metadata.py:186`, `metadata.py:197`) | Any failure falls back to the yt-dlp subtitle download (`metadata.py:194-202`); 429 is translated (`metadata.py:174-176`) | Transcript fetches with no page transcript | `metadata.py:172` |

No other `urllib` / `urlopen` call exists in `src/` (`urls.py:8` imports only `parse_qs` and `urlparse`).

## Model I/O contract

**Backend resolution:** `TLDW_LLM_CMD`, then config `llm_cmd`, then `claude` (`claude_client.py:205-213`). "Is it the stock CLI" means `argv[0] == "claude"` (`claude_client.py:251-254`).

**Stock argv**, built only by `_claude_argv` (`claude_client.py:189-202`), in this order:
1. `claude -p`
2. the mode flags: `--output-format json` for buffered calls (`claude_client.py:213`); `--output-format stream-json --verbose --include-partial-messages` for streaming (`claude_client.py:324-325`; `--verbose` is required by the CLI with `stream-json`, and `--include-partial-messages` is what yields token deltas, `claude_client.py:321-323`)
3. the lean flags `--tools "" --strict-mcp-config --setting-sources "" --system-prompt "<_SYSTEM_PROMPT>"` (`claude_client.py:108-111`); the system prompt must stay free of `& | < > ^` for the Windows `.cmd` shim (`claude_client.py:106-107`, `proc.py:38-63`)
4. `--model opus` or `--model sonnet`, only when the thread has pinned an allowlisted model (`claude_client.py:197-199`)
5. `--no-session-persistence` unless the call persists (only Q&A persists) (`claude_client.py:200-201`)
6. streaming Q&A follow-ups only: `--resume <session id from an earlier run>` (`claude_client.py:326-330`)

Process options for stock calls: environment copy with the `DISABLE_PROMPT_CACHING` handling above; `cwd` = `CLAUDE_DIR` (`claude_client.py:122-152`).

**stdin, every backend:** `prompt + "\n\n" + payload` (`claude_client.py:279`, `claude_client.py:331`, `claude_client.py:594`, `claude_client.py:622`). The buffered JSON retry appends one fixed "IMPORTANT: … ONLY the raw JSON object" paragraph (`claude_client.py:623-628`). Payloads: summary = metadata header + transcript text (`summarize.py:316-335`); selection = header + numbered cue listing (`summarize.py:374-375`); Q&A = `TITLE`, `CHANNEL`, `TRANSCRIPT`, optional `CONVERSATION SO FAR`, `QUESTION` blocks, or just `QUESTION` on a resumed session (`ask.py:73-90`).

**stdout, buffered stock CLI** (`--output-format json`): one JSON envelope. Read: `is_error`, `result` (the model's text; must be a non-empty string), `subtype` (`claude_client.py:37-55`); for usage, `usage.input_tokens`, `usage.cache_creation_input_tokens`, `usage.cache_read_input_tokens`, `usage.output_tokens`, `total_cost_usd`, `duration_ms`, `session_id`, `message.model`, `modelUsage` (keys joined as the model), `model` (`usage.py:79-101`). On a non-zero exit, `result` or `error` from a JSON envelope on stdout or stderr is the reported message (`claude_client.py:79-97`).

**stdout, streaming stock CLI** (`stream-json`, one JSON object per line; non-JSON lines skipped, `claude_client.py:400-404`):
- `stream_event` wrapping `event`; inside, `content_block_delta` whose `delta.type` is `text_delta` gives `delta.text` (`claude_client.py:257-269`). Older CLIs emit `content_block_delta` at the top level, which is also accepted.
- `assistant`: `message.content[]` blocks of `type: "text"` are joined; used only when no delta has streamed (`claude_client.py:429-435`).
- `system` with `session_id`: handed to the caller early (`claude_client.py:420-421`).
- `result`: usage recorded; `is_error` → the `result` or `subtype` text becomes a `ClaudeError`, so an error is never cached as an answer (`claude_client.py:405-419`, `claude_client.py:482-484`). Reading stops at `result` (`claude_client.py:436-440`).

**stdout, custom backend:** the model's text verbatim. Buffered: stripped, and must not be empty (`claude_client.py:58-63`). Streaming: bytes decoded incrementally as UTF-8 with replacement (`claude_client.py:272-291`). No envelope, no usage.

**The JSON tldw parses out of the model's text:**
- Buffered summary (`ask_json` + `_validate_text`): fences stripped, then the outermost `{…}` taken (`claude_client.py:216-224`); `key_points` (list of strings), `summary` (non-empty string) required; `chosen_ratio`, `rationale` optional (`summarize.py:188-202`).
- Buffered selection (`_make_video_validator`): `segments` (non-empty list of objects with integer `first_cue`, `last_cue`, clamped into range); `chosen_ratio`, `rationale` optional (`summarize.py:205-231`).
- Streaming summary lines (`stream_ndjson`, one object per line; the last line is parsed even without a newline, `claude_client.py:530-532`): `key_point`, `heading` (stored as `## <text>`), `paragraph`, and a closing line with `chosen_ratio` / `rationale` (`summarize.py:281-304`). If nothing adds up to a summary, one buffered retry runs (`summarize.py:324-335`).
- Streaming selection lines: an object with `first_cue` is a segment; one with `chosen_ratio` or `rationale` closes (`claude_client.py:548-556`); if no segment parses, the batch call runs (`summarize.py:406-414`).
- Q&A: plain text, no JSON (`claude_client.py:559-584`).

Invariant: the backend command, model and flags never come from a request (`claude_client.py:9-10`, `claude_client.py:162-164`), and the transcript never goes in argv. Blast radius: every model-backed route and the CLI; a changed argv changes `backend_fingerprint()` and so every summary and segment cache key.

## Scheduled jobs and background work

N/A for cron, queues and recurring timers: nothing in tldw runs on a schedule. What runs in the background:

| Job | Trigger | What it does | Idempotency / safety | Source |
|---|---|---|---|---|
| `startup_hygiene` | Every `tldw serve` start, after the port is bound, contained so it can't stop the server (`server.py:1952-1958`) | Removes `youtube-tldw-audio-*` dirs older than `_stale_audio_age()`; removes voice files the allowlist can't select; creates the voice-tracking marker | Idempotent; touches nothing a running job could use (`server.py:93-96`) | `server.py:89-112` |
| `tldw cache clean` (`--dry-run`) | Manual | Expired and stray `.tmp` entries in the three caches; stale audio dirs; unlisted and idle voices | Idempotent; `--dry-run` deletes nothing; every delete goes through `remove_under` (inside the base, no symlinks, never the base itself, `hygiene.py:62-78`) | `cli.py:394-417` |
| Cache pruning on write | Every `txcache.put*` and every in-memory put | Disk: oldest-mtime `*.json` beyond 500; memory: expired, then soonest-expiring beyond 64 | Runs inside the best-effort `try` (`txcache.py:87-92`) | `txcache.py:95-99`, `server.py:298-306` |
| Transcription thread `tldw-transcribe-<video_id>` | `/summarize/stream` on a captionless video under `ask` (accepted) or `always` | Waits for the single permit, downloads audio, runs Whisper, writes the transcript cache **before** anything else, whether or not a client is still connected (`server.py:259-261`); removes its temp dir in `finally` | One job per `(video_id, model)`; a daemon thread, so a server exit can stop it before its `finally`, and the startup sweep removes the dir later | `server.py:233-289` |
| Key-moment prefetch | End of a summary, a cached-summary hit, or a `/summarize/cached` hit (`server.py:1241`, `server.py:1321`, `server.py:1366`) | Makes the segment selection the page will ask for and caches it | Skipped when cached, already running, or when taking a slot would break `PREFETCH_RESERVE` (`server.py:455-472`); daemon thread | `server.py:434-522` |
| Per-request handler threads | Every HTTP request | `ThreadingHTTPServer` with `daemon_threads = True`; all share the registries above under `_cache_lock` | — | `server.py:1924-1925` |
| Per-call helper threads (not jobs) | Inside one call | `claude` stdin writer, stderr drain, post-result stdout drain and deadline `Timer` (`_Timer`) (`claude_client.py:296`, `claude_client.py:355`, `claude_client.py:367`, `claude_client.py:390-392`, `claude_client.py:456`); `stream_filter` feed and stderr threads and watchdog `Timer` (`proc.py:158-165`) | End with their call | as listed |

## Cross-cutting data invariants

| Invariant | What breaks if violated | Source |
|---|---|---|
| **`saved_at` is a top-level float (epoch seconds) in every cache JSON file** (transcripts, summaries, segments). | Each `get*` treats a file without it as a miss (`txcache.py:49`, `txcache.py:112`, `txcache.py:144`). `hygiene.expired_entries` reads `["saved_at"]` from any `*.json` in those directories, and skips a file without one, so `tldw cache clean` would never expire it (`hygiene.py:212-218`). | `txcache.py:76`, `txcache.py:125`, `txcache.py:159` |
| **Cache file names are built only from a validated 11-character video id** (`^[A-Za-z0-9_-]{11}$`, `urls.py:19`), plus hex for summaries and segments. | The id is what keeps a name inside its directory (`txcache.py:31-32`) and makes the yt-dlp globs safe (`metadata.py:223-224`, `whisper_transcribe.py:119-120`). A name from any other request string could escape the directory. | `urls.py:19-60`, `server.py:397-398`, `server.py:770` |
| **Everything that shapes a cached result must be in its key**: today `PROMPT_FINGERPRINT` (`summarize.py:140-148`), `SEGMENT_FINGERPRINT` (`core.py:26-45`), `backend_fingerprint()` (`claude_client.py:227-235`), the ratio, and the transcript `source`; the composition is owned by `data-model.md` §§ 2-3. **Known gap:** `PROMPT_FINGERPRINT` covers only four prompt strings (`summarize.py:144`), so `_ratio_clause`, `_metadata_header` and `SINGLE_PASS_CHARS` shape summaries without being in the summary key; `SEGMENT_FINGERPRINT` also misses `_metadata_header` and `SINGLE_PASS_CHARS` (T-014). Keys are built in the request thread, under its model scope (`server.py:385-388`). | Leaving an input out serves output the current code wouldn't produce (`server.py:751-760`); building a key on another thread silently describes the default model and misses every time. | `server.py:376-398`, `server.py:748-770` |
| **Cache writes are temp-then-replace**; `.tmp` is reserved for in-flight writes. | Readers would see half-written JSON. `config.json` and the token file do **not** follow this (`config.py:31`, `server.py:706`); `usage.jsonl` is appended. | `txcache.py:84-86`, `txcache.py:124-127`, `txcache.py:158-161` |
| **Cache and usage writes are best effort**: a full disk, a read-only home or an odd cue shape never fails the request. | A finished request would fail because a speed-up failed. | `txcache.py:88-92`, `usage.py:153-154`, `usage.py:168-169` |
| **Cache reads are tolerant; usage and config reads tolerate bad lines and files, not wrong types**: any unreadable, expired or older-shape cache file is a miss; a non-JSON usage line is skipped; an invalid config file is `{}`. A wrong-typed value is not tolerated: a non-numeric `video_ms` / `read_ms` makes `usage.stats()` raise (`usage.py:218-219`), and `llm_cmd`, `ytdlp_cookies`, `whisper_model` and `output_dir` assume strings (`claude_client.py:210`, `metadata.py:53-54`, `whisper_transcribe.py:79`, `cli.py:31`). | A format change would become an outage instead of a miss; a wrong-typed usage row breaks every streamed and probed summary result (`server.py:1234`, `server.py:1328`, `server.py:1369`). | `txcache.py:57-58`, `usage.py:237-240`, `config.py:19-20` |
| **Never change a field's type** in a cache record, a `usage.jsonl` row or a config value. | Readers apply the old type's operations to old rows and files, and per the row above several of them raise rather than fall back. | `usage.py:218-219`, `cli.py:351` |
| **Owner-only modes**: the token file 0600 (`server.py:707`), `usage.jsonl` 0600 after every append (`usage.py:189`), `CLAUDE_DIR` 0700 (`claude_client.py:149`). Each is applied after the file or directory exists, and is a no-op on Windows (`config.py:44-56`). `config.json`, the caches, voices, Whisper models and output files keep the process umask's mode. | Other local users could read the token, the viewing history, or the Q&A sessions. | `config.py:44-56` |
| **Deletion only by named rule, inside a given base, never through a symlink, never the base**; Whisper models and anything tldw doesn't own are never deleted. | A cleanup could remove the user's files. | `hygiene.py:3-14`, `hygiene.py:62-78` |
| **Transcript provenance**: `source` is `captions` or `audio:<model>`; absent or non-string reads as `captions` (`txcache.py:52-54`, `server.py:346-357`). Page-supplied transcripts are stored as `captions` too (`server.py:1311-1316`). | Summary and segment keys include the source, and `_effective_source` uses it to re-run Whisper after `whisper_model` changes (`server.py:878-890`). | `txcache.py:36` |
| **`usage.jsonl` is append-only, and rows are told apart by `kind`.** | `tldw usage` counts every row whose `kind` isn't `video` or `segments_served` as a model call (`cli.py:488`); a new non-call row kind would inflate call counts unless it is added there. | `usage.py:146-152`, `usage.py:166-167`, `usage.py:201-203` |
| **Every `Path.home()` store is a module constant listed in `HOME_PATHS`.** | The suite would write into the real home directory (`tests/conftest.py:1-11`). | `tests/test_isolation.py:18-27` |

## How this was verified

Each part was drafted from the code by its owning agent and re-checked by `tests/test_docs_contracts.py`. The review rounds re-check it against the source (§ 6.0(5)); see `docs/reviews/T-001-tier-1/`.

### Server (HTTP, NDJSON, errors, CLI, public interfaces, threads) — drafted by the Backend Engineer

- **Routes.** Read `server.py` end to end. Every route literal was found by reading `do_GET`, `do_POST`, the tuple, the dispatch, `_body_limit`, `do_OPTIONS` and `_cors_headers`. Each was matched to a `fetch(` call in `extension/background.js` and `extension/options.js`. `git grep '"/summarize"'` and `git grep '"/speak"'` outside `tests/`, `docs/reviews/` and `tasks/` found no caller of the buffered routes.
- **Body keys, event types and payload keys.** Extracted with `grep -o 'body.get("…")'`, `'"type": "…"'` and `page.get(` over `server.py`, then reconciled against `_to_payload`, `_run_*` and the `ev.type ===` branches in `background.js`.
- **Error strings.** Read every `_send_json(` and `emit({"type": "error"` in `server.py`. Every `raise …Error(` in `urls.py`, `metadata.py`, `transcript.py`, `core.py`, `summarize.py`, `spans.py`, `claude_client.py`, `proc.py`, `audio.py`, `whisper_transcribe.py` and `timing.py` was read and traced to the routes that reach it. The mappers were read in `background.js:514-521`, `:565-571`, `:645-655` and `options.js:102-108`.
- **Behaviour.** Confirmed against `tests/test_server.py` (fixtures, the status mapping, body caps at `:151-174`, the per-route pool matrix at `:2509-2540`, preflight at `:186` and `:792`) and `tests/test_cli.py` (exit codes at `:238`, `:251`, `:332`).
- **Live probe.** A throwaway script in the scratchpad ran `server._Server` on loopback port 0, with `HOME` and `TMPDIR` pointed at a temp dir and no model calls. It observed:
  - `501` HTML with no CORS for `PUT`, `HEAD` and `DELETE`;
  - an unauthenticated unknown POST gets `404`;
  - `GET /health?x=1` gets `200`;
  - an empty body gets `413`;
  - `[1]` sent to `/speak` or `/speak/stream` closes the connection with no response;
  - an unknown `model` gets `400`;
  - `/transcribe/stop` with an unknown `model` gets `200`;
  - a 70 KB `/ask/stream` body gets `413`;
  - `whisper: "maybe"` gets an in-stream `400`;
  - a lowercase `bearer` gets `401`;
  - a web Origin gets `200` with no `Access-Control-Allow-Origin`;
  - `_Server(("::1", 0))` raises `gaierror`;
  - the stdlib request log line includes the query string.

  No output from it is reproduced here beyond these facts.
- **Not run:** the claude-CLI-missing path (`claude_client.py:333`) is derived from the code alone.

### Extension (message protocol, user-facing text, storage, manifests, page contract, accessibility) — drafted by the Frontend Engineer

- **Read in full:** `extension/background.js`, `content.js`, `options.js`, `options.html`, `manifest.json`, `manifest.firefox.json`, `build-firefox.sh`; `tests/js/render_checks.js`, `page_transcript_checks.js`, `probe_checks.js`; `tests/test_extension_render.py`; and the MODELS test in `tests/test_claude_client.py:284-294`.
- **Messages:** enumerated by reading every `postMessage`, `safePost`, `send(` and `.type ===` in `extension/`. Each sender was cross-checked against its handler by line, in both directions.
- **NDJSON fields:** taken from the `ev.` reads in the four stream loops.
- **Storage keys:** taken from both `DEFAULTS` objects and every `storage.local.get`/`set` literal, including the shorthand object at `options.js:176-177`.
- **Selectors:** listed with `grep -noE '…\.(querySelector|querySelectorAll|getElementById|closest)\(…\)'`, with the `root`/`c`/`box` receivers dropped, plus every `attr(`/`text(` call in `pageMeta`.
- **Test markers:** listed with `grep -nE 'grab\(|between\(|indexOf\("'` over `tests/js/*.js`, then read in context.
- **Voice lists:** compared programmatically with `audio.VOICES`. All 13 ids and labels match, in order.
- **Server-side reads of `page`:** checked in `server.py:785-834`, `:1246-1248` and `transcript.py:104-127`.
- **Server-side routes:** listed by grepping `server.py` for route string literals.
- **Versions:** checked in `pyproject.toml:7` and `__init__.py:3`.
- **Not run in a browser.** The keyboard and accessibility gaps come from reading the code.

### Data, configuration and integrations — drafted by the Data Engineer

- Read in full: `txcache.py`, `hygiene.py`, `usage.py`, `config.py`, `audio.py`, `whisper_transcribe.py`, `claude_client.py`, `metadata.py`, `core.py`, `naming.py`, `textmode.py`, `proc.py`, `ask.py`, and the relevant parts of `server.py` (lines 1-930, 1151-1462, 1485-1650, 1743-1971), `cli.py`, `summarize.py` and `videomode.py`; `tests/test_isolation.py`, `tests/conftest.py`.
- Grepped `src/youtube_tldw/` for `Path.home`, `environ`, `getenv`, `config.get`, `set_value`, `unset`, `mkdtemp`, `gettempdir`, `chmod`, `restrict_to_owner`, `urlopen`, `urllib`, `threading.Thread`, `Timer(`, `.tmp`, the executable names, `sys.executable`, `shutil.which` and `subprocess.`, and for module-level `dict`, `OrderedDict` and `threading.local` registries. Every hit is either in a table above or noted as not a store.
- Cross-checked the home-path list against `HOME_PATHS` (10 entries, all present), and the registries against the eight `_fresh_server_state` clears.
- Grepped for `encrypt`, `cipher`, `keyring`, `keychain`, `umask`, `os.open(` and `logging.`: no hits.
- Grepped `extension/` for `new Map()`, `localStorage`, `indexedDB` and `storage.session`: only the four Maps listed.

## Update this doc when

Any change to an entry above updates this file **in the same PR** (`AGENTS.md` § 7 and § 8). `tests/test_docs_contracts.py` fails the PR if it doesn't.

### Server (HTTP, NDJSON, errors, CLI, public interfaces, threads)

- `src/youtube_tldw/server.py` changes:
  - any of the definition sites (`do_GET`, `do_POST`, the tuple, the dispatch, `_body_limit`, `do_OPTIONS`, `_cors_headers`);
  - any `_send_json(`, `emit(` or `body.get(`;
  - `_to_payload`, `_STATUS`, `_make_pools`;
  - the limits, timeouts or `MAX_*` constants;
  - `serve`, `_authorized` or `_origin_allowed`;
  - any `threading.Thread` start.
- Any `TldrError` message changes in `urls.py`, `metadata.py`, `transcript.py`, `core.py`, `summarize.py`, `spans.py`, `claude_client.py`, `proc.py`, `audio.py` or `whisper_transcribe.py`.
- `src/youtube_tldw/cli.py` changes: `main`'s dispatch, any parser, `_run_config`'s keymap or checks, or any `return <code>`.
- A signature in the "Public module entry points" table changes.
- `extension/background.js` changes `httpError`, `askError`, `speakError` or a `fetch(` call; `extension/options.js` changes a `fetch(` call.
- `pyproject.toml` changes `[project.scripts]`.
- `_run_align`'s check order, a `reason`, an `ALIGN_*` constant, or any text between the `# T-020 align text` markers in `align.py` changes.

### Extension (message protocol, user-facing text, storage, manifests, page contract, accessibility)

- `extension/background.js` changes: any `safePost`/`send` call, the `onConnect` dispatcher, `getSettings`, `DEFAULTS`, `MODELS`, `WHISPER_POLICIES`, a timeout constant, a `fetch`, any `ev.type` branch, or `httpError`/`askError`/`speakError`.
- `extension/content.js` changes: any `runtime.connect`/`postMessage`, the port `onMessage` listeners, `runtime.onMessage`, `storage.local` or `storage.onChanged`, `SEGMENT_ROWS`/`PANEL`/`TRANSCRIPT_BUTTON`, `pageMeta`, `collectPageTranscript`, `restoreTranscriptPanel`, the `video` lookups, `showError`, `trimmedNote`/`refreshTrimmedNote`, `showResult`'s heading, `mount`/`onKey`/`shieldKey`/`fromModal`/`unmount`, any `aria-*` or `role` attribute, `VOICE_OPTIONS`, or `sanitizeField`.
- `extension/options.js` or `options.html` changes, especially `DEFAULTS`, a list, a `fetch`, the `storage.local.set` object, or an `<option>` value.
- `extension/manifest.json`, `manifest.firefox.json` or `build-firefox.sh` changes, or a file is added to `extension/`.
- `extension/content.js`'s "T-020 cover listed words" blocks change: a status between the `// T-020 status text` markers, a listener, a page selector, the pill, or the panel toggle; or `extension/mute_words.js` or the injected-files list changes.
- `tests/js/*.js` or `tests/test_extension_render.py` adds, moves or rewords a marker or source pattern; or `tests/test_claude_client.py`'s MODELS regex changes.
- On the server: `claude_client.MODELS`, `server.WHISPER_POLICIES`, `audio.VOICES`, `_page_transcript`, the `result` event's keys, any NDJSON event's fields, the Origin allowlist (`_origin_allowed`), or the default port changes.

### Data, configuration and integrations

- `txcache.py`, `hygiene.py`, `usage.py`, `config.py` change.
- Any `Path.home()`, `mkdtemp(prefix=…)`, `os.environ` / `getenv` or `config.get` is added or removed anywhere in `src/youtube_tldw/`.
- `_summary_key`, `_seg_key`, `_seg_file`, `_to_payload`, `load_or_create_token`, `startup_hygiene`, `_stale_audio_age`, or any module-level registry in `server.py` changes.
- `claude_client._claude_argv`, `_LEAN`, `_claude_process_opts`, `_backend`, `_stream_deltas`, `_extract_result_text`, or `usage.parse_usage` changes; or `summarize.py`'s validators or line formats.
- `audio.VOICES`, `VOICE_DIR`, `ensure_voice`, `whisper_transcribe._CACHE_DIR` / `download_audio` / `transcribe` / `_load_model` / `model_on_disk`, `align.py` (the store, the key, the fingerprint), `metadata.py`'s yt-dlp argv, `COOKIE_BROWSERS`, `_CAPTION_HOSTS` or `MAX_CAPTION_BYTES` changes.
- `cli._run_config` (the `keymap` or `_check`), `_run_cache`, `_run_usage` or `_default_output_dir` changes.
- `tests/conftest.py` `HOME_PATHS` or the registry list changes.

## Follow-ups found while drafting

These are recorded facts, not fixes. Each becomes a `proposed` task (T-001 follow-ups).

### Extension (message protocol, user-facing text, storage, manifests, page contract, accessibility)

Each is a fact, recorded with no code changed. Each is tracked in the `proposed` follow-up task named at the end of the item.

1. **`UNKNOWN — needs human confirmation`:** does a custom `serverUrl` (another port, or `localhost`) work from the worker and from Options, given `host_permissions` of `http://127.0.0.1:8765/*` and the server's `chrome-extension://`-only CORS? It needs a browser, so it can't be settled in this docs pass. `deployment.md` § 11 records the server side as measured (a non-default port serves, and answers a `chrome-extension://` preflight) and leaves the extension side `UNKNOWN`. → [T-015](../tasks/T-015.md)
2. `options.js:30` `fillVoices` puts `/voices` data into `innerHTML` without escaping it. → [T-006](../tasks/T-006.md)
3. The mirrored lists have no test against the server: `WHISPER_POLICIES` (against `server.py:66`), `VOICE_OPTIONS` and `VOICE_FALLBACK` (against `audio.VOICES`), and the `<option>` values in `options.html` for models, policies and ratios. → [T-010](../tasks/T-010.md) and [T-018](../tasks/T-018.md)
4. The panel's voice picker never asks `/voices`, so a voice added on the server can't be chosen in the panel (`content.js:1032`). → [T-014](../tasks/T-014.md)
5. "Names a next step" is not met by: `httpError` `422` and the fallback; `askError` `413`, `422` and the fallback; `speakError` `503` and the fallback; the preview timeout (`background.js:558`); `No key moments found.`; `Couldn't find the YouTube player to control.` → [T-008](../tasks/T-008.md)
6. `httpError` `504` says "Summarizing timed out" when key moments time out (`background.js:653`, used at `:593` and `:623`). → [T-008](../tasks/T-008.md)
7. Backtick spans show as raw backticks outside `showError` (in the audio, ask, toast and Options surfaces). The no-token and `502` messages contain them. → [T-008](../tasks/T-008.md)
8. Accessibility: `.regen` has no `aria-label`; `.ts` citation links can't be activated from the keyboard; the Tab trap's last element can be unfocusable while the ask panel is hidden; the trap leaves out links; the main error and progress views aren't live regions; Shift+Tab from the focused panel leaves the modal; the skip pill's buttons show no focus ring (`all:unset`, `content.js:3034`). All found by reading the code; none checked in a browser. → [T-009](../tasks/T-009.md)
9. Tests: the bare `src.indexOf()` slices in `render_checks.js` (`:136-137`, `:155-156`, `:165-166`, `:176-177`, `:234-235`, `:247-248`, `:274-275`, `:284-285`, `:314-315`, `:323-324`) don't fail when a marker is missing, so an "absent" check can pass without testing anything. Renaming the `PANEL_*_MS` constants silently disables the speed-up rewrite in `page_transcript_checks.js:96-98`. → [T-010](../tasks/T-010.md)
10. Panel and worker timeouts tie for ask (300,000 ms) and key moments (320,000 ms), so which message the user sees depends on timing. With `whisper: "always"`, the panel's 160,000 ms summarize timer fires before the worker's 2,400,000 ms budget (`background.js:218-219`, `content.js:691`). The preview port has no panel timer at all (`content.js:1448-1465`). → [T-008](../tasks/T-008.md)
11. `videoIdFromUrl` accepts any hostname that **ends with** `youtube.com` (`background.js:31`), and `content.js` is injected into whatever tab was clicked before that check runs (`background.js:61-62`). Raised for the Architect to assess. → [T-005](../tasks/T-005.md)
12. The token is kept in plain text in `chrome.storage.local`, and it is sent to whatever `serverUrl` holds (cross-reference `architecture.md`). → [T-004](../tasks/T-004.md)
13. The worker's `DEFAULTS.voice` is read but never used (`background.js:10`, `:47-51`). The worker's `DEFAULTS` has no `closeAction`, unlike `options.js`. → [T-014](../tasks/T-014.md)
14. No test covers `build-firefox.sh`'s copy list or the version being equal across the manifests. → [T-010](../tasks/T-010.md)
15. The worker's `cache`, `audioCache` and `previewCache` maps have no eviction (`background.js:25-26`, `:523`). → [T-014](../tasks/T-014.md)

### Data, configuration and integrations

Each is a fact, recorded with no code changed. The full list, with sources, is `data-model.md`'s "Recorded facts and weaknesses"; each item there names its task.

- Store robustness: `config.json` writes aren't atomic; the cache `.tmp` name is per key; no `lang` in any cache key; two temp-dir prefixes are never swept; `usage.jsonl` is unbounded; no fingerprint covers the payload or segment-item shape; the summary key misses `_ratio_clause`, `_metadata_header` and `SINGLE_PASS_CHARS`; wrong-typed usage and config values raise; `whisper_max_minutes` ≤ 0 clamps to 1 minute; `tldw cache clean` skips unreadable cache files; yt-dlp's cache and the browser cookie database are unrecorded third-party stores. → [T-014](../tasks/T-014.md)
- File modes applied after creation (token, `usage.jsonl`, `CLAUDE_DIR`). → [T-004](../tasks/T-004.md)
- Stale code comments (`server.py:38`, `:64`, `:962-963`). → [T-012](../tasks/T-012.md)
- `UNKNOWN — needs human confirmation`: the Claude CLI's session format, modes and retention, and whether it writes into `CLAUDE_DIR`; faster-whisper's layout. → [T-015](../tasks/T-015.md) (yt-dlp's cache and the cookie database's location are in T-014's item.)
