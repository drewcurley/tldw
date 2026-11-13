# Architecture

> **Status: descriptive.** This describes the code as it is on `hackathon/demo` at tldw 1.3.0 (`src/youtube_tldw/__init__.py:3`). Nothing here is a plan. Every claim cites the code; where the code doesn't settle a fact, it says `UNKNOWN — needs human confirmation`.
>
> **Companion docs.** `contracts.md` is the exhaustive inventory (every route, key, event, selector, env var). This doc is the map: the parts, how data moves between them, where trust changes hands, and what must stay true for the security model to hold. If this doc and `contracts.md` disagree on an inventory item, `contracts.md` wins and this doc is the one to fix.

## 1. What tldw is, in one paragraph

tldw is a **local-only** tool. Each user runs it on their own machine with their own Claude plan, and there is no hosted server (`README.md:433-435`, `extension/README.md:112-114`, commit `1dedf94`; ADR 0001). It has two front doors: a command-line tool (`tldw <url>`, `tldw config|usage|cache`) and a loopback HTTP server (`tldw serve`) that a browser extension calls. The server and CLI share one core (`core.py`) that validates a YouTube URL, gets a transcript (from the page, a cache, `yt-dlp`, or local Whisper transcription), and asks a model to summarize it, pick key moments, or answer questions. The model is the `claude` CLI by default, or any operator-configured command (`claude_client.py:1-10`, `:205-213`).

## 2. Components and responsibilities

### 2.1 Python package `src/youtube_tldw/` (one process per command)

| Component | Responsibility | Source |
|---|---|---|
| CLI entry `main` | Dispatches on `argv[0]`: `config`, `usage`, `cache`, `serve`; anything else is the default `<url>` mode with its own parser. Exit 130 on Ctrl-C. | `cli.py:589-619` |
| `tldw serve` | Parses `--host`, `--port`, `--token`, `--allow-origin`, `--llm-cmd`; reads `TLDW_TOKEN`; calls `server.serve`. | `cli.py:301-310`, `:596-607` |
| HTTP server | `ThreadingHTTPServer` subclass, one daemon thread per request; routes, auth, body limits, CORS, NDJSON streaming, concurrency pools, in-memory caches. | `server.py:927-1902`, `:1924-1971` |
| Core orchestration | Transcript fetch and summarize / select segments, shared by CLI and server. | `core.py` (e.g. `core.py:65` temp dir) |
| Model client | Builds the backend argv, pipes prompt + transcript on stdin, parses JSON / stream-json, watchdog timeouts, usage accounting. | `claude_client.py` |
| Prompts | Summary and segment prompts; Q&A prompt and history trimming. | `summarize.py`, `ask.py` |
| YouTube access | `yt-dlp` metadata and subtitle calls; direct caption fetch behind a host allowlist. | `metadata.py` |
| URL validation | Accepts only https YouTube hosts or a bare 11-character id. | `urls.py:12-60` |
| Transcripts | Caption parsing, and parsing of the page's transcript-panel rows. | `transcript.py` (`:99-100` caps) |
| Disk caches | Transcripts, summaries, segment selections as JSON files with TTL and caps. | `txcache.py` |
| Speech | Piper TTS (voice allowlist, voice download) piped into an `ffmpeg` mp3 encoder. | `audio.py` |
| Whisper fallback | `yt-dlp` audio download + faster-whisper transcription for captionless videos. | `whisper_transcribe.py` |
| Video mode (CLI only) | `yt-dlp` video download, `ffmpeg`/`ffprobe` cutting and rendering, Pillow overlays. | `videomode.py`, `spans.py`, `overlays.py`, `metadata.py:237-260` |
| Usage ledger | One JSONL row per model call. | `usage.py:26`, `:132-155`, `:185-189` |
| Config | `~/.config/youtube-tldw/config.json`, any key. | `config.py:12-41` |
| Disk hygiene | Startup sweep of stale audio temp dirs and unlisted voices; `tldw cache clean`. | `hygiene.py`, `server.py:89-112` |
| Subprocess chokepoint | Every external command runs here: argv list, no shell, timeouts. | `proc.py:1-186` |

### 2.2 Browser extension `extension/` (Manifest V3, plain JS, no build step for Chrome)

| Component | Responsibility | Source |
|---|---|---|
| Service worker `background.js` | On toolbar click, injects `content.js` into the active tab and sends `TLDW_INVOKE`. Accepts `runtime.connect` ports named `tldw` only, and makes **every** HTTP call to the server for the content script, holding the token. Keeps an in-memory summary, audio and preview cache. | `background.js:58-83`, `:25-26`, `:449` |
| Content script `content.js` | Injected on click (no `content_scripts` key). Renders the panel in a shadow root, reads the page's title/channel/duration and transcript panel, and talks to the worker over the port. Its code never reads `token`, but the storage area isn't restricted to trusted contexts (no `setAccessLevel` call), and its `onChanged` listener (`content.js:50-55`) is handed the new token whenever Options saves one (§ 7, item 13). | `content.js:1-3`, `:136`, `:480-499`, `:517-545`, `:577-586`; storage reads only at `:47`, `:1008`, `:1195` |
| Options page `options.html` / `options.js` | Server URL, token and preferences in `chrome.storage.local`; calls `/health` and `/voices` with no token and `/preview` with the typed token, directly. | `options.js:3-5`, `:52-53`, `:70`, `:97-101`, `:137-138` |
| Manifests | `permissions`: `activeTab`, `scripting`, `storage`; `host_permissions`: `http://127.0.0.1:8765/*`. Firefox uses `background.scripts` and a gecko id. | `manifest.json:6-8`, `manifest.firefox.json:6-11` |
| Firefox build | Copies a fixed file list into `dist-firefox/`. | `build-firefox.sh:10-12` |

### 2.3 External processes and services (exhaustive)

**Executables** (all through `proc.run` / `proc.stream_filter`, or `subprocess.Popen` in `claude_client._stream_deltas`):

| Executable | Used for | Where |
|---|---|---|
| `claude` | Default model backend: one-shot JSON, stream-json, Q&A sessions. | `claude_client.py:196`, `:324-343` |
| `TLDW_LLM_CMD` / `llm_cmd` command | Operator-chosen model backend; any argv after `shlex.split`. | `claude_client.py:210-212` |
| `yt-dlp` | Metadata, subtitle download, audio download (Whisper), video download (CLI). | `metadata.py:81-84`, `:211-220`, `:243-250`; `whisper_transcribe.py:123-133` |
| `ffmpeg` | mp3 encoding for speech; audio extraction and all video-mode cutting. | `audio.py:222`, `:276-278`; `videomode.py:33`, `:64`, `:86`, `:258`, `:284`, `:334`, `:358` |
| `ffprobe` | Video-mode rendered-duration check (CLI only). | `videomode.py:101` |
| `python -m piper.download_voices` | Voice-model download (`sys.executable`). | `audio.py:248-249` |
| `node` | Test harnesses only; never at runtime. | `tests/test_extension_render.py` |

**Python libraries:** `pillow` (required), `piper-tts` (optional `[tts]`), `faster-whisper` (optional `[whisper]`), `pytest` (dev) (`pyproject.toml:10-15`). `setuptools` is the build backend (`pyproject.toml:1-3`). Transitive packages are listed in `uv.lock` and, for CI, `scripts/requirements-ci.txt`.

**Network services:** Anthropic (through the `claude` CLI); YouTube (through `yt-dlp`, and the direct caption fetch to `youtube.com`, `googlevideo.com`, `ytimg.com` and their subdomains, `metadata.py:129-139`); Hugging Face for Piper voices (`audio.py:234`, the host is whatever `piper.download_voices` uses: `UNKNOWN — needs human confirmation`) and faster-whisper models (`whisper_transcribe.py:195-210`; tldw's own reporting names them `models--Systran--faster-whisper-*`, `hygiene.py:234`). The browser additionally loads YouTube itself, and `content.js` clicks YouTube's own transcript button so the page fetches captions in its own session (`content.js:376-391`, `:517-545`). The panel's sponsor link (`content.js:39`) is a plain link the user may click; no data is sent to it.

**Browser platform:** Chrome MV3 APIs (`action`, `scripting.executeScript`, `runtime.connect`, `tabs.sendMessage`, `storage.local`), and Firefox ≥ 115 through the same code with a `browser ?? chrome` shim (`background.js:8`, `manifest.firefox.json:10`).

**CI:** GitHub Actions (`.github/workflows/checks.yml`); `azure-pipelines.yml` is kept but inert (ADR 0002). CI holds no secrets.

## 3. Data flow

### 3.1 Summarize from the browser (the main path)

```
user clicks toolbar
  └─ background.js: executeScript(content.js) → tabs.sendMessage TLDW_INVOKE {url, videoId}      background.js:58-68
content.js: runtime.connect({name:"tldw"}) → port msg probeCache                                   content.js:577, :656
  └─ background.js: POST /summarize/cached  (Bearer token)                                         background.js:92-127
       hit  → probe {hit:true, payload} → panel renders; done
       miss → content.js reads page meta + opens/reads/closes the transcript panel                 content.js:480-545
              → port msg summarize {url, videoId, page:{title, channel, duration_ms, cues, status}} content.js:585-586
  └─ background.js: POST /summarize/stream (Bearer token, model, segment_ratio, whisper, page)     background.js:149-155
server: auth → body ≤ 8 MiB → validate url/ratio/lang → model allowlist → claude slot             server.py:1020-1035
  ├─ summary cache hit (key = video + backend fingerprint + ratio + prompt fingerprint + source)   server.py:1224-1244, :748-770
  └─ else transcript: memory/disk cache → page cues (type-checked, bounded) → yt-dlp → Whisper     server.py:1272-1300, :785-834
       → claude CLI (prompt + transcript on stdin, no tools) → NDJSON progress/meta/partial/result  server.py:1190-1328
       → caches written (transcript, summary); background key-moment prefetch started             server.py:1313-1327
background.js maps NDJSON to port messages; content.js renders escaped text                      background.js:162-209, content.js:94-127
```

### 3.2 The other paths

- **Key moments.** `getSegments` → `POST /segments/stream`. Served from the segment cache, by waiting on an in-flight prefetch, or by a live model call that streams `segment_added` events (`server.py:1485-1650`, `background.js:499-565`).
- **Questions.** `ask` → `POST /ask/stream` with `url`, `question`, `history`, `model`. The transcript is the server's cached copy, or it is fetched; it never travels from the extension on this route (`background.js:365-366`, `server.py:1796-1816`). The model-side session id is held only by the server, in memory, keyed by video (`server.py:45`, `:1826-1832`).
- **Read aloud.** `speak` → `POST /speak/stream`. The server builds a script, synthesizes with Piper, and streams mp3 through `ffmpeg` as base64 NDJSON chunks (`server.py:1652-1741`, `audio.py:281-321`).
- **Voice preview.** From the panel (`preview` port message → `background.js:451-489`) or from the options page directly (`options.js:83-120`). Both hit `POST /preview`, which returns `audio/mpeg` (`server.py:1857-1901`).
- **Captionless video.** The server emits `needs_transcription` (an offer), or with policy `always` / an accepted offer it starts one transcription job in its own thread under a single machine-wide permit. It narrates progress, releases the request's slot while it waits, and caches the result even if the client hung up (`server.py:52-75`, `:187-295`, `:1371-1461`). `POST /transcribe/stop` sets the job's cancel flag and takes no slot (`server.py:985-1005`).
- **CLI.** `tldw <url>` runs the same core in-process, with no HTTP. Video mode downloads the video and renders with `ffmpeg` into the output folder (`cli.py:278-299`, `videomode.py`).

### 3.3 Where state lives

In-memory (lost on restart): transcript, segment, captionless-answer, Q&A-session and progress maps under one `_cache_lock`, TTL 4 h, cap 64 each (`server.py:35-49`, `:852-853`); transcription jobs (`server.py:64`); preview mp3s (`server.py:551`); the extension worker's own maps (`background.js:25-26`, `:449`).

On disk: `~/.cache/youtube-tldw/{transcripts,summaries,segments}` (TTL 14 days, 500 files each, `txcache.py:21-27`); `~/.cache/youtube-tldw/voices` (`audio.py:61`); `~/.cache/youtube-tldw/whisper` (`whisper_transcribe.py:23`); `~/.cache/youtube-tldw/claude` (0700) as the `claude` working directory (`claude_client.py:119`, `:147-151`). The `claude` CLI files persisted Q&A sessions under its own projects folder for that directory, `~/.claude/projects/<encoded tldw claude dir>/` (`claude_client.py:113-118`). Also `~/.config/youtube-tldw/{token,config.json,usage.jsonl}` (`server.py:689`, `config.py:12`, `usage.py:26`); temp dirs with prefixes `youtube-tldw-audio-`, `youtube-tldw-tx-`, `youtube-tldw-` (`hygiene.py:27`, `core.py:65`, `cli.py:288`); and `chrome.storage.local`. The full per-store table (shape, keys, TTL, cleanup, classification) is `data-model.md`'s.

### 3.4 Concurrency and failure domains

- **One process, one host.** The server is a single local process. When it is down, the extension reports "Can't reach the tldw server" (`background.js:219-220`). There is no redundancy and no availability claim, so the "no single point of failure" redline doesn't apply.
- **Pools** (`server.py:557-563`, `:640-651`): 6 `claude` slots (prefetch never takes the last 2), 2 speech slots, and 1 transcription permit (`server.py:65`). A full pool means a `429` with a message naming the limit (`server.py:1061-1063`). `/transcribe/stop`, `/summarize/cached`, and `/preview` cache hits take no slot (`server.py:985-1012`).
- **Timeouts:** summarize 120 s, segments 300 s, speak 600 s, ask 300 s (`server.py:549`, `:682-687`); the `claude` watchdog kills a silent process (`claude_client.py:370-392`). The extension has its own budgets (`background.js:16-23`).
- **The `claude` CLI is down, logged out, or over its usage limit:** `ClaudeError` → 502 with the CLI's own message (`claude_client.py:79-97`, `:409-416`, `server.py:711-717`). Startup prints which login variables are present, names only (`server.py:659-679`).
- **YouTube rate-limits (HTTP 429):** `yt-dlp` retries with backoff, and the error is translated into a "wait a few minutes" message (`metadata.py:56-76`). The page-supplied transcript and the disk caches exist to avoid asking YouTube at all (`server.py:785-797`, `txcache.py:1-10`).
- **Optional parts missing:** no Piper → 503 (`server.py:1477-1480`); no faster-whisper → `needs_transcription` with an install hint (`server.py:1404-1410`).

### 3.5 Cost shape

There is no hosting cost. Model calls are billed to the user's own Claude plan, or to the API when `ANTHROPIC_API_KEY` is set (`server.py:672-673`). Cost is per call. The levers in code:
- the lean invocation, which a code comment measures at about 5x fewer input tokens (`claude_client.py:100-111`);
- prompt caching off for one-shot calls and on for Q&A (`claude_client.py:122-142`);
- summary and segment caches keyed so that a repeat is free (`server.py:748-770`, `:376-394`);
- the background key-moment prefetch, which spends a call per summarized video whether or not it is played; `usage.jsonl` rows record `trigger` and `segments_served` so this can be measured (`server.py:434-522`, `usage.py:146-151`, `:158-170`).

Piper, faster-whisper and `yt-dlp` cost CPU and bandwidth only.

### 3.6 Data residency

`N/A — tldw makes no data-residency commitment.` Data leaves the machine only to Anthropic (through the `claude` CLI, under the user's own account terms), YouTube, and Hugging Face (§ 6). Where Anthropic processes it is governed by the user's Claude account, not by tldw: `UNKNOWN — needs human confirmation` if any commitment is ever wanted. There are no regions and no cross-region flows.

## 4. Identity and secrets

- **tldw's only secret is its bearer token.** It is resolved as `--token` or `TLDW_TOKEN`, then the file `~/.config/youtube-tldw/token`, then a newly generated `secrets.token_urlsafe(32)` saved to that file with 0600 applied best-effort (`cli.py:603`, `server.py:689-708`, `config.py:44-56`). Every POST compares it with `hmac.compare_digest` (`server.py:955-960`). There are no users, roles or sessions: holding the token is the whole identity.
- **The model login belongs to the `claude` CLI** (its own store, or `CLAUDE_CODE_OAUTH_TOKEN` / `ANTHROPIC_API_KEY` in the environment). tldw only reports which variable names are set, never their values (`server.py:659-679`). tldw never reads a keychain. It passes its whole environment to child processes (`claude_client.py:135`; `proc.run` passes `env=None`, so the parent environment, `proc.py:82-90`). That includes `TLDW_TOKEN` when the user set it that way.
- **The extension stores the token in plain text** in `chrome.storage.local` (`options.js:137-138`) and sends it as `Authorization: Bearer <your-token>`. Only extension-origin code reads it on purpose: `background.js` (`:47`) and `options.js` (`:37`, `:88`). `content.js`'s code never reads `token`, but the storage area isn't restricted to trusted contexts (no `setAccessLevel` call in `extension/`), and `content.js`'s `onChanged` listener (`content.js:50-55`) is handed the new token, in `changes.token.newValue`, whenever Options saves one (§ 7, item 13).
- **YouTube identity (opt-in):** `TLDW_YTDLP_COOKIES` / `ytdlp_cookies` makes `yt-dlp` read one allowlisted browser's cookie store (`metadata.py:44-55`). tldw itself never reads cookies; `yt-dlp` does.
- **CI has no secrets** (`.github/workflows/checks.yml`; ADR 0002).

## 5. Trust boundaries and threat model

The trusted base is the user's OS account. Everything running as that user (the server, the CLI, the `claude` CLI, `yt-dlp`, the browser) can already read the user's files. The boundaries below are where *less* trusted input enters.

| # | Boundary | Untrusted side | What crosses |
|---|---|---|---|
| B1 | Loopback HTTP: any local client → `tldw serve` | Any process on the host, and any web page the browser is showing (it can `fetch` `127.0.0.1`) | HTTP requests to port 8765 |
| B2 | Extension ↔ server | Nothing, when the extension is the caller; but the server can't tell the extension from B1's clients except by the token | JSON bodies, NDJSON streams, `audio/mpeg` |
| B3 | Extension ↔ YouTube page | The YouTube page and its scripts; the DOM the content script reads | Title, channel, duration, transcript-panel rows; panel DOM |
| B4 | tldw → subprocesses | The data handed to them (transcripts, titles), and their output (`yt-dlp` JSON, model text) | argv (constants and validated ids), stdin, stdout/stderr |
| B5 | Outbound network | YouTube, Hugging Face and Anthropic responses | Captions, metadata, audio/video, model files, model output |
| B6 | The model (prompt-injection boundary) | Transcript text, page text, video titles, user questions and history | Prompt + untrusted payload on stdin → model text back |
| B7 | Local filesystem ↔ other OS users | Other accounts on the same machine | Token file, caches, session files |

### 5.1 STRIDE, one row per boundary

| # | Spoofing | Tampering | Repudiation | Information disclosure | Denial of service | Elevation of privilege |
|---|---|---|---|---|---|---|
| B1 | Bearer token on every POST, constant-time (`server.py:955-960`, `:990`, `:1020`, `:1106`). The Origin check is CORS only, not authentication (§ 7). | JSON-only bodies, `Content-Length` required, chunked refused, per-route caps (`server.py:1129-1149`, `:773-782`). | Request log: client address, time, request line, status (`server.py:962-963`; stdlib `log_request`), stderr only; no audit trail. | Open GETs return only `{ok,name,version,backend}` and the voice list (`server.py:972-980`, `claude_client.py:238-248`); DNS rebinding can read them (§ 7). Error strings carry up to 3 lines of subprocess stderr (`proc.py:98-101`). | Pools → 429 (`server.py:1051-1063`); body caps; one transcription (`server.py:65`); duration ceiling (`server.py:1385-1400`). No socket timeout, and `ThreadingHTTPServer` starts one thread per connection with no limit (`server.py:1924-1925`), so a slow client, with or without the token, can hold threads (§ 7, item 15). | Loopback-only bind (`server.py:1935-1939`); no request value reaches a shell or argv except allowlisted ones (§ 6). |
| B2 | Extension proves itself only with the token. Token goes wherever `serverUrl` says (§ 7). | Server re-validates everything (url, ratio, lang, model, voice, whisper policy, page) (`server.py:1151-1166`, `:1090-1100`, `:136-160`, `:1463-1483`, `:785-834`). | None. | Plain HTTP on loopback, accepted in ADR 0003. Token in plain text in `chrome.storage.local` (§ 7). | Client timeouts (`background.js:16-23`); 429 messages surfaced. | `host_permissions` limited to `http://127.0.0.1:8765/*`; no `externally_connectable` key (`manifest.json:1-15`). |
| B3 | Content script accepts only `TLDW_INVOKE` / `TLDW_ERROR` from the extension runtime (`content.js:1908-1936`). The worker accepts ports named `tldw` (`background.js:71`); no `externally_connectable`, so web pages can't connect. | Page data is untrusted: the server type-checks and bounds it (`server.py:794-834`, `transcript.py:99-126`). The page can forge it; the effect is a wrong summary of that same video. | None. | The shadow root is `mode: "open"` (`content.js:136`): the page's scripts can read the rendered summary and answers (§ 7). Model text is escaped before rendering (`content.js:94-127`, `:1281-1293`). | Panel waits are capped (`content.js:395-400`, `PANEL_WAIT_MS`). | No MAIN-world injection; injection only on the user's click via `activeTab` + `scripting` (`background.js:61`, `content.js:388-391`). |
| B4 | n/a | argv lists, never `shell=True` (`proc.py:1-6`, `:82`, `:123`); untrusted data on stdin only (`claude_client.py:9-10`, `:331-355`). File paths only from validated ids or constants (§ 6, I-14). | None. | `yt-dlp` stderr tails can reach error messages (`proc.py:98-101`). Children inherit the server's environment (§ 4). | Every call has a timeout or watchdog (`proc.py:91-92`, `:162-165`, `claude_client.py:370-392`); stderr drained into a bounded tail (`claude_client.py:357-368`). | `yt-dlp` metadata and subtitle calls honour the user's own `yt-dlp` config; only the request-triggerable audio download passes `--ignore-config` (`metadata.py:81-84`, `:211-218` vs `whisper_transcribe.py:113-125`). |
| B5 | Caption fetch only from allowlisted https hosts (`metadata.py:129-139`, `:164-165`). TLS is the remote ends' (Python `urllib`, `yt-dlp`, the CLI). | Caption size cap 8 MiB (`metadata.py:42`, `:173`, `:180-181`); audio `--max-filesize` 300 MiB, no live streams (`whisper_transcribe.py:85`, `:113-128`). Model downloads aren't checksummed by tldw (`audio.py:248`, `whisper_transcribe.py:202-210`). | None. | What leaves the machine: § 6. The direct caption fetch sends a desktop-Chrome `User-Agent` (`metadata.py:166-170`). | YouTube 429 backoff (`metadata.py:56-57`); HF download timeout 60 s (`whisper_transcribe.py:199`). | Model files are executed only as data by Piper / CTranslate2. |
| B6 | n/a | Prompt injection in a transcript or title can steer the model's text. The `claude` backend runs with no tools, no MCP servers and no setting sources (`claude_client.py:108-111`), so injected text can't act. Only the Q&A prompt tells the model the transcript is untrusted (`ask.py:41-42`); the summary prompt doesn't (`summarize.py:21-24`). Model output is parsed and validated as JSON where structure is expected (`claude_client.py:606-646`) and HTML-escaped where shown (`content.js:94-127`). | None. | The model sees only the transcript, metadata and question; the lean flags stop the user's own `CLAUDE.md` and plugins being sent (`claude_client.py:100-107`). | Watchdog timeout (`claude_client.py:390-392`); question and history caps (`ask.py:17-19`, `:52-64`, `server.py:1753-1754`). | A custom `TLDW_LLM_CMD` gets none of the lean flags (`claude_client.py:155-159`, `:210-212`): what tools it has is the operator's choice. |
| B7 | n/a | Token file 0600 best-effort (`config.py:44-56`); `usage.jsonl` re-restricted on each append (`usage.py:189`); `CLAUDE_DIR` 0700 (`claude_client.py:149`). Other stores use the default umask (`txcache.py:74`, `config.py:30-31`). | None. | Token-file chmod race (§ 7). On Windows `chmod` doesn't give 0600; profile ACLs stand in (`config.py:47-51`). | n/a | `config.json` can set `llm_cmd`, which becomes argv: whoever can write that file can run a command as the user (`claude_client.py:210-212`, `config.py:27-31`). It is the user's own file. |

## 6. Security invariants

Each one is verified against the code at the cited lines. **Invariant** is what must stay true; **blast radius** is what breaks, or who is exposed, if it stops being true; **guard** is the test that fails if it regresses, where one was found ("none found" means a regression would pass CI today; `testing.md` owns the full map).

| ID | Invariant | Source | Blast radius | Guard |
|---|---|---|---|---|
| I-1 | **Loopback only.** `serve` refuses any host other than `127.0.0.1`, `localhost`, `::1` before binding, with a message pointing at `docs/reviews/PLAN-extension.md` (that path is cited by the code and must not move). Default host `127.0.0.1`, port 8765. `::1` passes the check but fails to bind on the stdlib's IPv4 `ThreadingHTTPServer` (observed: `gaierror` on Python 3.14, macOS). | `server.py:1933-1939`, `cli.py:304-305` | Removing it exposes every route to the network over plain HTTP and voids ADR 0003. | none found |
| I-2 | **Bearer token on every POST**, compared with `hmac.compare_digest`, checked before the body is read. Unknown POST paths get 404 before auth. | `server.py:955-960`, `:990`, `:1017-1022`, `:1106` | Any local process or web page could spend the user's Claude plan and CPU. | `tests/test_server.py:96`, `:446`, `:486`, `:619`, `:1664`, `:2216` |
| I-3 | **Only `GET /health` and `GET /voices` are open** (plus `OPTIONS` preflight, which does no work). They never return secrets or user data: `/health` = `ok`, `name`, `version`, `backend{kind,label,models,streaming}` (never the configured command); `/voices` = the constant voice list. | `server.py:966-980`, `claude_client.py:238-248`, `audio.py:73-74` | Anything added there is readable by any local process and, through DNS rebinding, by web pages (§ 7). | `tests/test_server.py:90`, `:264` |
| I-4 | **Body discipline:** chunked → 400; non-`application/json` → 415; missing `Content-Length` → 411; length ≤ 0 or over the route cap → 413; invalid JSON → 400. Caps: 16 KiB default, 64 KiB `/speak*` and `/ask/stream`, 8 MiB `/summarize*`. | `server.py:542-548`, `:773-782`, `:1129-1149`, `:1015` | Memory exhaustion; request smuggling via chunking. | `tests/test_server.py:143` (415); chunked/411/413: none found |
| I-5 | **Constant headers:** `Content-Type` is always a server-side constant (`application/json`, `application/x-ndjson`, `audio/mpeg`), with `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`. Exception: the stdlib's own HTML error pages (bad request line, unsupported method) come from `BaseHTTPRequestHandler`, without these headers or CORS. | `server.py:944-953`, `:1192-1197`, `:1494-1499`, `:1664-1669`, `:1766-1771` | Content sniffing; cached responses holding summaries. | none found |
| I-6 | **The log line carries no body and no token.** The default `log_message` writes client address, time, request line and status to stderr. The request line includes any query string; the code comment says "no query", which holds only because the extension sends none. The server's own `print` lines do log video ids, titles and the page `status` (truncated to 200 chars). | `server.py:962-963`, `:1187`, `:1248`, `:1310`; stdlib `BaseHTTPRequestHandler.log_request` | A token in a body or header would otherwise reach the terminal. | none found |
| I-7 | **argv lists, never `shell=True`.** All commands go through `proc.run` / `proc.stream_filter` or `Popen` with a list. On Windows a `.cmd` shim runs through `cmd.exe` only if no argument contains `& | < > ^`. | `proc.py:1-6`, `:42-63`, `:82`, `:123`; `claude_client.py:333`; `grep shell=True src/` finds only the docstring | Command injection from a title, transcript or URL. | `tests/test_proc.py:152` |
| I-8 | **Allowlists for every request value that reaches argv or a file:** model ∈ `MODELS` {`opus`,`sonnet`} (400 otherwise); voice ∈ `audio.VOICES` (plus aliases); `lang` matches `^[A-Za-z][A-Za-z0-9-]{0,15}$`; whisper policy ∈ {`off`,`ask`,`always`}; cookie browser ∈ `COOKIE_BROWSERS`; whisper model ∈ `VALID_MODELS`. | `claude_client.py:164`, `server.py:1090-1100`, `audio.py:64-70`, `server.py:688`, `:1163-1165`, `:1758-1760`, `:66`, `:136-160`, `metadata.py:48-55`, `whisper_transcribe.py:25-28`, `:76-81` | Argument injection into `claude`, `yt-dlp`, Piper. | `tests/test_server.py:730`, `:1377`; `tests/test_metadata.py:129` |
| I-9 | **The backend command is operator-only.** `TLDW_LLM_CMD` (env, or `--llm-cmd`, which sets it) beats `llm_cmd` in `config.json`, which beats `claude`. No request can set or read it; `/health` reports only its kind, and cache keys use a hash of it. | `claude_client.py:205-213`, `:227-248`, `cli.py:599-600` | A request choosing the command would be remote code execution. | none found for "no request can set it" |
| I-10 | **Lean `claude` invocation:** `-p` with `--tools ""`, `--strict-mcp-config`, `--setting-sources ""`, a fixed `--system-prompt`; one-shot calls add `--no-session-persistence` and `DISABLE_PROMPT_CACHING=1`. Calls run with cwd `~/.cache/youtube-tldw/claude`, chmod 0700 on every call. If that directory can't be made, the call runs from the current directory instead. | `claude_client.py:100-152`, `:189-202` | Without it the model gets tools while reading untrusted text, and the user's `CLAUDE.md` and plugins ride along with every transcript. Q&A sessions would land among the user's own Claude Code sessions. | `tests/test_claude_client.py:177-180`, `:186`, `:192`, `:388`, `:401-403`, `:406` |
| I-11 | **Q&A session ids are server-held.** They come only from the CLI's own stream output and are stored in memory per video; a request can't supply one. | `server.py:45`, `:422-431`, `:1826-1832`; `claude_client.py:326-330`, `:417-421`; `ask.py:93-141` | A client-chosen id could resume, and read, any persisted session in that directory. | `tests/test_claude_client.py:522` (resume behaviour); "not from a request": none found |
| I-12 | **URL validation:** https only; host ∈ {`youtube.com`, `www.youtube.com`, `m.youtube.com`, `music.youtube.com`, `youtu.be`}; the id must match `^[A-Za-z0-9_-]{11}$`; or a bare 11-character id. It runs before any subprocess. The `yt-dlp` URL is always rebuilt from the id (`watch_url`). | `urls.py:12-60`, `metadata.py:25-26` | SSRF through `yt-dlp` extractors or local paths. | `tests/test_urls.py` |
| I-13 | **Caption fetch:** https only, host is or ends in `youtube.com` / `googlevideo.com` / `ytimg.com`, URL ≤ 4096 chars, body ≤ 8 MiB (reads cap+1 and refuses). Only the first URL is host-checked: `urlopen` follows redirects without re-checking the target (§ 7, item 14). | `metadata.py:42`, `:129-139`, `:157-182` | SSRF from a crafted caption URL (it comes from `yt-dlp` output). | none found for the host check |
| I-14 | **File paths only from validated 11-character ids or constants:** transcripts `<id>.json`; summaries and segments `<id>-<sha256[:16]>.json`; voices `<allowlisted model>.onnx`; `yt-dlp` output template `%(id)s.%(ext)s`; temp dirs by `mkdtemp`. Cleanup refuses symlinks and anything outside its base. | `txcache.py:30-33`, `server.py:397-398`, `:748-770`, `audio.py:239`, `metadata.py:21`, `:223-231`, `whisper_transcribe.py:113-117`, `hygiene.py:62-65` | Path traversal writing or deleting outside the cache. | `tests/test_hygiene.py:209` (cleanup) |
| I-15 | **Page data is type-checked and bounded:** `duration_ms` must be a non-bool number in (0, 24 h); at most 20,000 panel rows, 1,000 chars per cue; title ≤ 300 and channel ≤ 200 chars; anything unparseable falls back to fetching. The duration that sizes a transcription comes from `yt-dlp`, never the page. | `server.py:785-834`, `transcript.py:99-126`, `server.py:1382-1385` | Memory blow-up; a page talking the server into an hours-long transcription. | `tests/test_server.py:1431` (ceiling) |
| I-16 | **Token file 0600, best-effort.** Written, then chmod'ed; a file that already exists is read without re-checking its mode. On Windows the chmod is effectively a no-op. | `server.py:700-708`, `config.py:44-56` | Another local account reading the token. Known race: § 7. | `tests/test_server.py:771`, `:784` (persistence; mode: none found) |
| I-17 | **Extension isolation:** only `background.js` and `options.js` (extension origins) read the token and send it. `content.js`'s code never reads `token`; but the storage area isn't restricted to trusted contexts (no `setAccessLevel`), and `content.js`'s `onChanged` listener is handed the new token whenever Options saves one (`content.js:50-55`; § 7, item 13). `host_permissions` is exactly `http://127.0.0.1:8765/*`. No `externally_connectable`, `content_scripts` or `web_accessible_resources` keys. The worker ignores ports not named `tldw`. Model and page text is escaped before `innerHTML` in `content.js`. | `background.js:43-52`, `:71`; `options.js:37`, `:88`; `manifest.json:1-15`, `manifest.firefox.json:1-18`; `content.js:50-55`, `:94-127` | A web page or the YouTube page obtaining the token, or driving the server through the extension. | `tests/js/render_checks.js` via `tests/test_extension_render.py` (escaping); manifests: none found |

## 7. Known weaknesses (recorded as facts, not fixed here)

Each item ends with its follow-up task (`tasks/T-NNN.md`). None is changed by this doc.

1. **The token is printed at startup** on every `tldw serve` (`server.py:1959`), so it appears in terminal scrollback, screen shares and pasted logs. `--token` also shows in `ps` (`cli.py:306`). → T-004
2. **Token-file chmod race.** `write_text` creates the file with the process umask, and only then does `chmod 0600` run (`server.py:706-707`). For that window another local account can read it if the umask allows. The parent directory is created with default permissions (`server.py:705`), and an existing file's mode is never re-checked (`server.py:700-703`). → T-004
3. **The Origin check is CORS, not authentication.** `_origin_allowed` only decides whether CORS headers are sent (`server.py:917-939`); a request with any Origin, or none, is still processed. The default rule admits the origin of any installed Chrome extension, not just tldw's (`origin.startswith("chrome-extension://")`, `server.py:924`), unless `--allow-origin` pins one. `extension/README.md:106-107` says the server "only accepts `chrome-extension://` origins (a web page can't trigger it)". It is the token that stops a web page. → T-005; the README text, T-012
4. **DNS rebinding can read the open GETs.** The server never checks `Host` (`grep '"Host"' src/youtube_tldw/server.py` finds nothing). A page that rebinds its own name to 127.0.0.1 can read `/health` and `/voices`: that tldw is running, its version, and the backend kind. It can't get past a POST without the token. → T-005
5. **The token goes to whatever `serverUrl` says.** Every worker and options-page call sends `Authorization: Bearer` to the stored `serverUrl` (`background.js:108-110`, `:149-151`, `:231-234`, `:266-268`, `:385-387`, `:466-468`, `:511-513`; `options.js:97-99`). The URL isn't checked against loopback. Only the user sets it, on the options page (`options.js:125`). → T-004
6. **The token is stored in plain text** in `chrome.storage.local` (`options.js:137-138`). → T-004
7. **`fillVoices` writes `/voices` data into `innerHTML` unescaped** on the options page (`options.js:28-32`, fed from `options.js:70-73`). `/voices` is fetched from `serverUrl` with no token, so whatever answers there controls that markup in an extension page. → T-006
8. **Firefox's extension origin is `UNKNOWN — needs human confirmation`.** The default CORS rule only matches `chrome-extension://` (`server.py:924`). Whether a Firefox build gets any CORS headers, and whether it needs them, is unverified; `--allow-origin` can pin one origin (`cli.py:307-308`). → T-015
9. **The panel's shadow root is open** (`content.js:136`). YouTube's own scripts can read what the panel shows (summaries, answers, questions) and can click its controls. YouTube is trusted to that extent. → T-007
10. **The summary prompt doesn't mark the transcript as untrusted;** only the Q&A prompt does (`ask.py:41-42`, `summarize.py:21-24`). The no-tools invocation (I-10) is what limits the damage, and only for the `claude` backend. → T-007
11. **Docs overstate what leaves the machine.** `extension/README.md:104-105` says "Nothing else leaves your machine", but Hugging Face model downloads and the direct caption fetch also go out (§ 8). → T-012
12. **Child processes inherit the full environment,** including `TLDW_TOKEN` when it is set (`claude_client.py:135`, `proc.py:82-90`). → T-004
13. **The token reaches the content-script world.** `content.js`'s code never reads `token`, but `chrome.storage.local` isn't restricted to trusted contexts (the extension makes no `setAccessLevel` call), and `content.js`'s `onChanged` listener (`content.js:50-55`) is handed every changed key, including the new token in `changes.token.newValue`, whenever Options saves one (`options.js:137-138`). The listener only uses `closeAction`, but the value is in the content script's world on the YouTube tab. → T-004
14. **The caption fetch checks only the first URL.** `fetch_caption_text` checks `is_safe_caption_url` once, then `urllib.request.urlopen` follows redirects without re-checking the host or scheme (`metadata.py:157-182`). I-13's allowlist therefore holds for the URL `yt-dlp` returned, not for where the request ends. → T-005
15. **No socket timeout and no thread limit.** `_Server` is a `ThreadingHTTPServer` with daemon threads (`server.py:1924-1925`) and sets no timeout, so each connection gets its own thread for as long as the client keeps it open. A slow client needs no token to hold threads: the server reads headers before the auth check. → T-005

## 8. What data leaves the machine

| Destination | What | Path | When |
|---|---|---|---|
| Anthropic, via the `claude` CLI | Prompt, video title/channel/duration, the full transcript, and for Q&A the question and trimmed history. Under the user's own login or `ANTHROPIC_API_KEY`. | `claude_client.py:331`, `ask.py:79-90`, `summarize.py:21-24` | Every summary, key-moment selection (including the background prefetch) and question. |
| A custom backend's destination | The same payload, to whatever `TLDW_LLM_CMD` contacts: `UNKNOWN — needs human confirmation` per user. | `claude_client.py:272-291` | When configured. |
| YouTube, via `yt-dlp` | Requests for metadata, subtitles, audio (Whisper) and video (CLI). With cookies opted in, the browser's YouTube cookies. | `metadata.py:81-84`, `:211-218`, `:243-250`, `whisper_transcribe.py:123-133`, `metadata.py:52-55` | When the page didn't supply a transcript, or for the CLI. |
| YouTube / Google video hosts, direct | A GET for a caption URL `yt-dlp` returned, with a desktop-Chrome `User-Agent`. | `metadata.py:157-182` | Before falling back to a second `yt-dlp` run. |
| Hugging Face | Piper voice download on first use of a voice; faster-whisper model download on first transcription. | `audio.py:231-262`, `whisper_transcribe.py:195-210` | First use only. |
| YouTube, from the browser | The page's own transcript request, triggered by `content.js` clicking the page's transcript button. | `content.js:517-545` | Each summarize that misses the cache. |

Nothing else is sent: there is no telemetry, analytics or update check (no other `urlopen` or HTTP client in `src/`; `fetch` in `extension/` only targets `serverUrl`).

**Classification.** No regulated domain applies: no health, payment, children's or government data. What is personal is behavioural: which videos the user summarized (video ids and titles in the caches, `usage.jsonl` rows, Claude session files) and the free-text questions they ask. Those stay on the machine, except the transcripts and questions sent to Anthropic as above. `data-model.md` classifies each store ("Personal data and protection"), and the Architect confirms that classification, with two refinements: `chrome.storage.local` is class **Secret**, since it holds the token in plain text (`options.js:137-138`) and reaches the content-script world (§ 7, item 13); and the extension worker's in-memory summary and audio caches, keyed by video id (`background.js:25-26`), are personal viewing history, like the server's in-memory stores (its preview cache, keyed by voice, `background.js:449`, is not personal).

**YouTube's terms for `yt-dlp` downloads: `UNKNOWN — needs human confirmation`.**

## How this was verified

- Read in full: `src/youtube_tldw/server.py`, `claude_client.py`, `config.py`, `urls.py`, `proc.py`, `metadata.py`, `txcache.py`, `extension/background.js`, `options.js`, both manifests, `build-firefox.sh`, `pyproject.toml`. Read the relevant parts of `content.js` (escaping, shadow root, page reading, port and message handling), `audio.py`, `whisper_transcribe.py`, `ask.py`, `usage.py`, `hygiene.py`, `transcript.py` and `cli.py`.
- Enumerated executables with `grep -n '\["\(ffprobe\|ffmpeg\|yt-dlp\|claude\)"\|sys.executable'` over `src/`, and outbound HTTP with `grep urlopen`; checked `grep shell=True src/` (docstring only) and `grep '"Host"' server.py` (nothing).
- Checked the stdlib's `log_request` / `log_message` source to establish what the request log contains.
- Bound `ThreadingHTTPServer` to `127.0.0.1`, `localhost` and `::1` with port 0 in a scratch process (`::1` fails with `gaierror`); no tldw server was started.
- Found guarding tests by grepping `tests/` for each invariant's behaviour.
- Not reconciled line by line with `contracts.md`, which is drafted in parallel. The Review Coordinator's merge step does that reconciliation.

## Update this doc when

- `src/youtube_tldw/server.py` changes: `serve`, `_Handler` (`do_GET`, `do_POST`, `do_OPTIONS`, `_authorized`, `_read_body`, `_body_limit`, `_send_bytes`, `_cors_headers`, `_origin_allowed`), `load_or_create_token`, the pools, `_page_transcript`, or any `print` that logs request data.
- `claude_client.py` changes `_LEAN`, `_claude_argv`, `_claude_process_opts`, `CLAUDE_DIR`, `_backend`, `backend_info` or session handling; or `ask.py` / `summarize.py` change how untrusted text is framed.
- `proc.py`, `urls.py`, `metadata.py` (`is_safe_caption_url`, `_cookie_args`, any `yt-dlp` argv), `audio.py` (`VOICES`, `ensure_voice`), `whisper_transcribe.py` (`download_audio`, model loading) or `txcache.py` / `hygiene.py` path building change.
- Any new subprocess, network destination, disk store, env var, route, or thread.
- `extension/manifest*.json`, `background.js` (`onConnect`, any `fetch`), `options.js` (token handling, `fillVoices`), or `content.js` (`attachShadow`, `esc`, `renderSummary`, page reading) change.
- tldw is ever served off-host. That voids ADR 0003 and needs a new threat model here first.
