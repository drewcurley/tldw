# Glossary

**Status:** descriptive. Every term below is one that the docs in `docs/`, the code identifiers they cite, or the extension's UI actually use. Each is defined once, and each points to where it lives. It sets no new direction (T-016).

**How to read it.**
- **Where it lives** names the doc that owns the term and the code that defines it. Code paths are abbreviated the way `contracts.md` abbreviates them: `server.py` means `src/youtube_tldw/server.py`, and `content.js` means `extension/content.js`.
- **This file defines; it does not inventory.** Lists, values, TTLs, caps, limits and exact message text belong to the doc that owns them (`contracts.md`, `data-model.md`, `api.md`), and are cited here rather than repeated. If this file and an owning doc disagree, the owning doc wins and this file is the one to fix.
- **Other Tier 2 docs own some terms.** The domain's entities and how they relate are [`domain.md`](domain.md)'s. Personas and journeys are [`ux.md`](ux.md)'s. Severity levels and operating procedures are in [`runbooks/`](runbooks/README.md). Rows for those terms give a one-line meaning and link the owner.
- Examples are invented. A video id in an example is an invented 11-character string such as `AAAAAAAAAAA`.

## 1. Words with more than one name

The code, the UI copy and the docs sometimes use different words for the same thing. Each pair below names one thing.

| Words | The one thing | Where it lives |
|---|---|---|
| **tldw**, **TL;DW**, **youtube-tldw** | `tldw` is the command and the name `/health` reports (`server.py:975`). `TL;DW` is the product's name in UI copy (`content.js:272`, `options.html:27`), and also names its output, a "text TL;DW" or "video TL;DW" (`README.md:105-108`). `youtube-tldw` is the Python distribution (`pyproject.toml:6`) and the name of its directories (`~/.cache/youtube-tldw/`, `~/.config/youtube-tldw/`). `youtube_tldw` is the import package (`src/youtube_tldw/`). | `architecture.md` § 1 |
| **key moments**, **segments**, **selection**, **skip playback** | The parts of a video the model picks for playback. The UI says "key moments" (`content.js:877`); the routes, events and caches say "segments" (`/segments/stream`, `segment_added`, `SEGMENT_DIR`); the code that makes them says "selection" (`core.select_segments`, `core.py:90-151`); playing them in the YouTube player is "skip playback" (`contracts.md`, "Background → content port messages"). | `contracts.md`, "NDJSON events" |
| **Listen**, **read aloud**, **speak**, **speech**, **TTS** | Turning a summary into spoken mp3 audio with Piper. The button says "Listen to summary" (`content.js:878`); the routes are `/speak` and `/speak/stream`; the pool is `speech`. | `architecture.md` § 3.2 |
| **Ask**, **questions**, **Q&A** | Follow-up questions about a video, answered from its transcript. The UI says "Ask about this video" (`content.js:970`); the route is `/ask/stream`; the code module is `ask.py`. | `contracts.md`, "HTTP routes" |
| **captions**, **subtitles**, **auto-captions** | The timed text YouTube serves for a video. The code says `captions` (`txcache.py:36`); the UI copy and some error text say "subtitles" or "auto-captions" (`options.html:57`, `metadata.py:121-124`). | `contracts.md`, "Pass-through `TldrError` text" |
| **Whisper**, **faster-whisper**, **transcription** | Making a transcript from a video's audio, on the user's machine, with the faster-whisper library (optional `[whisper]` extra, `pyproject.toml:14-15`). "Whisper" is the UI's and the config key's name for it. | `architecture.md` § 2.1 |
| **worker**, **background worker**, **service worker**, **event page**, `background.js` | The extension's background script. Chrome runs it as an MV3 service worker; Firefox runs the same file as an event page (`manifest.json:8`, `manifest.firefox.json:8`). | `contracts.md`, "Manifest keys" |
| **panel**, **popup**, **modal** | The summary UI drawn on the YouTube page. The Options copy calls it "the popup" (`options.html:76`) and the READMEs "the modal"; the code, the docs and the tasks call it the panel; its accessibility role is a modal dialog (`content.js:270`). | `contracts.md`, "Dialog semantics and focus" |
| **the owner**, **the user**, **the operator** | One person in this repo: the solo maintainer, who is also the only known user (`ownership.md`). "The operator" is whoever configures the server (`TLDW_LLM_CMD`, `config.json`), as opposed to a request. | `ownership.md`; `ux.md` (personas) |
| **busy**, **`429`** | tldw's own concurrency limit refusing work. It never means YouTube's rate limit, which tldw reports as a `500` (`metadata.py:66-76`). | `api.md` § 6 |

## 2. The product and its domain

The entities here are defined in full, with their relationships and cardinality, in [`domain.md`](domain.md).

| Term | Meaning | Where it lives |
|---|---|---|
| **video** | One YouTube video, known to tldw only by its video id plus the metadata it reads (title, channel, duration). An entity. | `domain.md`; `metadata.py` |
| **video id** | The 11-character id that names a video: it matches `^[A-Za-z0-9_-]{11}$` (`urls.py:19`). Every cache file name and every YouTube URL tldw builds starts from it. | `contracts.md`, "Cross-cutting data invariants"; `urls.py:12-60` |
| **watch URL**, `source_url` | The canonical `https://www.youtube.com/watch?v=<id>` URL, always rebuilt from the video id (`metadata.py:25-26`). | `contracts.md`, "Summary payload" |
| **URL validation** | The allowlist every URL passes before any subprocess: https YouTube hosts, or a bare video id (`urls.canonical_video_id`, `urls.py:22-60`). Security invariant I-12. | `architecture.md` § 6 |
| **transcript** | A video's text as timed cues, with the metadata it came with and its source. An entity. | `domain.md`; `data-model.md` § 1 |
| **cue** | One timed line of a transcript. On disk it is a `[start_ms, end_ms, text]` triple (`data-model.md` § 1); in the page payload it is `{start_ms, text}` (`transcript.py:104-140`). | `data-model.md` § 1 |
| **transcript source** | Where a transcript came from: `captions` or `audio:<model>` (for example `audio:small`) (`txcache.py:36-46`). A page-read transcript is stored as `captions` (`server.py:1311-1316`). It is part of the summary and segment keys. | `contracts.md`, "Cross-cutting data invariants" |
| **page transcript**, **page payload**, `page` | The transcript, title, channel and duration the extension reads from the YouTube page itself and sends with `summarize`, so the server needn't ask YouTube (`content.js:538-565`, `server.py:785-834`). Untrusted and bounded (I-15). | `contracts.md`, "The `summarize` message's `page` payload" |
| **transcript panel** | YouTube's own "Show transcript" engagement panel, which the extension opens, reads and closes (`content.js:538-575`). | `contracts.md`, "Open, read, close: the transcript panel flow" |
| **captionless video** | A video with no captions or auto-captions, so its transcript can only come from its audio. | `architecture.md` § 3.2 |
| **summary** | The model's condensed version of a video: key points, a markdown body and a rationale. An entity. | `domain.md`; `contracts.md`, "Summary payload" |
| **summary payload**, `_to_payload` | The dict a summary travels as: returned by `/summarize`, carried by the `result` event, stored in the summary cache (`server.py:1904-1921`). Its keys are interface. | `contracts.md`, "Summary payload" |
| **key points**, `key_points` | The summary's list of short takeaway strings. | `contracts.md`, "Summary payload" |
| `summary_md` | The summary's markdown body. Sent to `/speak*` as `summary` (`background.js:345`). | `contracts.md`, "Summary payload" |
| **rationale** | The model's note on how much it compressed and why (`rationale`, also `chosen_ratio`, which is parsed but never shown; `summarize.py:174-184`, `domain.md`). | `contracts.md`, "Model I/O contract" |
| **original length**, `original_length` | The video's length, formatted `12m34s`-style (`timing.format_length`, `timing.py:68-79`). | `data-model.md` § 2 |
| **length label**, `length_label` | The summary's reading time, formatted `2m`-style (`timing.read_time_label`, `timing.py:82-85`). The CLI also puts it in output file names. | `data-model.md` § 2 |
| **ratio**, **summary ratio** | The target fraction of the original length, a number in `(0, 1]` (`server.py:1155-1162`). Absent, or "Auto" in the UI, means the model chooses (`cli.py:57-59`, `options.html:49`). | `contracts.md`, "Request body keys" |
| **segment ratio**, `segment_ratio`, `segmentRatio` | The ratio used for key moments, set by the Options "Key moments length" picker (Auto, Tight, Balanced, Generous; `options.html:47-52`). Stored as a string, sent as `segment_ratio` to summarize and as `ratio` to `/segments/stream` (`background.js:588`). | `contracts.md`, "chrome.storage.local keys" |
| **single pass**, **map-reduce** | How a long transcript is summarized: in one model call up to `SINGLE_PASS_CHARS`, otherwise in chunks then combined (`summarize.py:17`). The browser routes refuse anything that would need map-reduce (`413`). | `contracts.md`, "Limits, pools and slots" |
| **key moments** | See section 1. An entity: a video's segment selection. | `domain.md` |
| **segment** | One key moment as the server sends it: `{start, end, label}`, in seconds, with an `m:ss` label (`core.py:132-136`, `:148-150`). | `contracts.md`, "NDJSON events" |
| **cue range** | The model's pick, as a pair of cue indexes `first_cue`, `last_cue` (`summarize.py:205-231`). `VideoSelection.ranges` holds them (`summarize.py:182-185`). | `contracts.md`, "Model I/O contract" |
| **span**, `Span` | A cue range turned into milliseconds, merged and padded, ready to cut (`spans.py:15-31`, `spans_from_cue_ranges`, `spans.py:33`). `MIN_CLIP_MS` and `BOUNDARY_PAD_MS` shape it (`core.py:22-23`). | `domain.md` (CLI output) |
| **clip** | UI copy for one segment while skip playback plays it ("Clip 2/5", `content.js:3075`). In CLI video mode, one cut span (`MIN_CLIP_MS`). | `domain.md` § 5 |
| **key-moment prefetch**, **prefetch** | A background segment selection started after a summary, so "Play key moments" is instant (`server.py:434-522`). It spends a model call whether or not the key moments are played. | `contracts.md`, "Threads"; `architecture.md` § 3.5 |
| `max_length`, `--max-length` | A hard cap on the selection's length, as a duration string such as `90s` or `5m` (`cli.py:60-62`). A capped request is never cached (`server.py:1554-1558`). | `contracts.md`, "Request body keys" |
| **question**, **history**, **turn** | A Q&A request carries the new question and the earlier turns as `{role, content}` items, trimmed by `ask.py:17-19`. | `contracts.md`, "Request body keys" |
| **Q&A session**, **session id** | The `claude` CLI's own conversation for one video, so a follow-up needn't re-send the transcript. tldw keeps only the id, in memory (`_ask_sessions`, `server.py:45`), and passes it as `--resume`. An entity. | `domain.md`; `data-model.md` § 10 |
| **citation** | A `12:34`-style timestamp in an answer that seeks the player (`.ts`, `content.js:1316-1317`). | `contracts.md`, "Accessible names" |
| **voice** | One of the allowlisted Piper voice models in `audio.VOICES` (`audio.py:44-58`), with `female` and `male` aliases (`audio.py:59-70`). The default is `amy`. | `contracts.md`, "Lists mirrored from the server" |
| **voice preview** | A short sample of a voice, from the panel or the Options page, through `/preview` (`server.py:1857-1901`). It takes no slot when memoized. | `contracts.md`, "HTTP routes" |
| **spoken script** | The speakable text built from a summary's fields, with no markdown or URLs (`audio.build_spoken_script`, `audio.py:353`). | `architecture.md` § 3.2 |
| **Piper**, `piper-tts` | The optional in-process text-to-speech library (`[tts]` extra). Its GPL-3.0-or-later license is accepted for that use by ADR 0004. | `stack.md`, "License posture"; `decisions/0004-accept-gpl-tts-dependency.md` |
| **transcription** | Making a captionless video's transcript from its audio with faster-whisper. | `architecture.md` § 3.2 |
| **whisper policy**, `whisper` | What to do with a captionless video: `off`, `ask` or `always` (`WHISPER_POLICIES`, `server.py:66`). The UI labels are under "Videos with no subtitles" (`options.html:57-61`). A server-side `whisper` config key overrides the request. | `contracts.md`, "Config keys" |
| **whisper model**, `whisper_model` | The faster-whisper model size (`VALID_MODELS`; default `small`). Part of the transcript source `audio:<model>`. An entity. | `domain.md`; `contracts.md`, "Config keys" |
| **transcription offer**, `needs_transcription` | The stream's terminal event that asks the user whether to transcribe, or says Whisper isn't installed (`server.py:1404-1410`). Accepting sends a new request with `transcribe: true`. | `domain.md`; `contracts.md`, "NDJSON events" |
| **transcription job**, `_tx_jobs` | One running transcription, in its own daemon thread named `tldw-transcribe-<video id>`, keyed by video and model; a second caller joins it (`server.py:187-295`). It caches its result even if the client hung up. An entity. | `domain.md`; `contracts.md`, "Threads" |
| **transcription permit**, `_tx_permit` | The single machine-wide semaphore that lets one transcription run at a time (`server.py:65`). Separate from both slot pools. | `contracts.md`, "Limits, pools and slots" |
| **transcription ceiling** | The longest video that may be transcribed, checked against yt-dlp's duration, never the page's (`MAX_TRANSCRIBE_MS`, `server.py:70`; config `whisper_max_minutes`). | `contracts.md`, "Limits, pools and slots" |
| **transcription budget**, **estimate** | How long a job may run, derived from an estimate of its length (`estimate_s`, `whisper_transcribe.estimate_seconds`, `whisper_transcribe.py:91`; `server.py:73-74`). | `contracts.md`, "Limits, pools and slots" |
| **stats**, **hours saved** | Totals from the usage log (`videos`, `saved_hours` and more, `usage.stats()`, `usage.py:208-224`) added to every streamed or probed `result`. The panel shows them, and they gate the sponsor ask. | `contracts.md`, "Summary payload" |
| **usage log**, **usage row**, `kind` | `usage.jsonl`, one JSON row per event, told apart by `kind`: model calls (`summarize`, `segments`, `ask`), `video` and `segments_served`. Store 4. Usage rows are an entity. | `domain.md`; `data-model.md` § 4 |
| **interaction**, `trigger`, `step` | `usage.Interaction` groups the model calls of one piece of work (`usage.py:56-63`). `trigger` (`prefetch` or `request`) is on segment rows; `step` labels each call. | `data-model.md` § 4 |
| **model backend** | Whatever answers the prompts: the stock `claude` CLI by default, or the operator's command. An entity. | `domain.md`; `contracts.md`, "Model I/O contract" |
| **settings** | Two separate sets: the server's `config.json` keys and the extension's `chrome.storage.local` keys, with different writers and defaults. | `domain.md`; `contracts.md`, "Config keys" and "chrome.storage.local keys" |

## 3. CLI output (the TL;DW output)

The TL;DW output is an entity in [`domain.md`](domain.md).

| Term | Meaning | Where it lives |
|---|---|---|
| **`tldw <url>`**, **the CLI** | The command-line front door. It runs the same core in-process, with no HTTP (`cli.py:589-619`). | `contracts.md`, "CLI" |
| **text mode**, **video mode** | `--mode text` writes a markdown summary; `--mode video`, the default, renders a shorter recut video (`cli.py:48-49`). | `contracts.md`, "Flags, every parser" |
| `--render-audio` | Also saves an mp3: the recut audio in video mode, a spoken summary in text mode (`cli.py:50-52`). | `contracts.md`, "Flags, every parser" |
| **polish**, `Polish` | The video-mode overlays, all on by default: the corner **badge**, the **intro banner**, the fade-to-black **end card**, and the source **timestamp** at each cut (`videomode.py:135-157`; `--no-badge`, `--no-banner`, `--no-end-card`, `--no-timestamps`, `cli.py:74-81`). | `contracts.md`, "Flags, every parser" |
| **intro**, `--keep-intro`, `--no-intro` | How many opening seconds video mode keeps as an intro (`cli.py:68-73`). | `contracts.md`, "Flags, every parser" |
| **burn captions** | `--burn-captions`: draw recut-aligned captions into the video (`cli.py:64-65`). | `contracts.md`, "Flags, every parser" |
| **output dir**, **output files** | Where the CLI saves `.md`, `.mp4` and `.mp3` files, in `text/`, `video/` or `audio/`, named `{channel} - {title} - tl;dw - {length}.{ext}` (`naming.py:52-84`). Store 11. The server never writes here. | `data-model.md` § 11 |
| **video download** | yt-dlp fetching the source video for CLI video mode (`metadata.py:237-260`). | `contracts.md`, "Subprocess executables" |

## 4. The server, its routes and its streams

| Term | Meaning | Where it lives |
|---|---|---|
| **`tldw serve`**, **the server** | The loopback HTTP server the extension calls, one process on the user's machine (`server.py:1933-1971`). | `api.md` § 1 |
| **loopback**, **loopback-only bind** | `serve` refuses any host but `127.0.0.1`, `localhost` and `::1` before binding (`server.py:1935-1939`). Security invariant I-1; ADR 0003 rests on it. | `architecture.md` § 6 |
| **route** | One HTTP path the server answers. All are listed in `contracts.md`; adding one means changing every definition site. | `contracts.md`, "HTTP routes" |
| **definition sites** | The places in `server.py` that must agree for a route to work: `do_GET`, the `do_POST` chain, the "not one of ours" tuple, the in-slot dispatch, `_body_limit`, `do_OPTIONS` (`server.py:966-1049`). | `api.md` § 10.1; `contracts.md`, "Where routes are defined" |
| **buffered route**, **stream route** | `/<action>` answers in one JSON or mp3 body; `/<action>/stream` sends NDJSON as the work proceeds. | `api.md` § 2 |
| **open GETs** | `GET /health` and `GET /voices`, the only routes with no token (`server.py:972-980`). Security invariant I-3. | `architecture.md` § 6 |
| **probe**, **cache probe** | `POST /summarize/cached` and the `probeCache`/`probe` port messages: ask for a cached summary before reading the page. Any failure is a miss, so an older server's `404` reads as a miss (`server.py:1330-1369`, `background.js:166-201`). | `contracts.md`, "HTTP routes" |
| **hit**, **miss** | A cache read that found a usable entry, or didn't. A bad, expired or older-shape entry is a miss, never an error. | `data-model.md`, "No version markers" |
| **NDJSON** | Newline-delimited JSON, `application/x-ndjson`: one JSON object per line, flushed one at a time, ending when the socket closes. | `api.md` § 7 |
| **event**, **stream event**, `type` | One NDJSON line. Every one carries a `type`. Not the same as a port message, even where the spelling matches. | `contracts.md`, "NDJSON events" |
| **terminal event** | The one event that ends a stream (for example `result`, `segments_done`, `answer_done`, `error`). A stream that closes without one is reported as "ended unexpectedly". | `api.md` § 7 |
| `progress`, `creep`, **heartbeat** | Non-terminal status lines. `creep: true` tells the client to ease its bar forward while the model works silently; a heartbeat is the repeated progress line while waiting on a prefetch or transcription. | `contracts.md`, "Streaming behaviour" |
| `partial`, `partial` kind | A piece of the summary as the model writes it, `key_point` or `paragraph`. | `contracts.md`, "`partial` `kind` values" |
| **slot**, **slot pool** | A counted pool of concurrent work. The `claude` pool covers summaries, key moments, questions and the prefetch; the `speech` pool covers reading aloud and previews (`_claude_slots`, `_speech_slots`; `server.py:556-651`). The limits are in the owning table. | `contracts.md`, "Limits, pools and slots"; `api.md` § 5 |
| **busy** | A full pool: a `429` whose text names the limit, never a queue. | `contracts.md`, "Limits, pools and slots"; [`runbooks/server-busy.md`](runbooks/server-busy.md) |
| **prefetch reserve**, `PREFETCH_RESERVE` | The claude slots background work may never take, so a click is never refused because of a prefetch (`server.py:562`). | `contracts.md`, "Limits, pools and slots" |
| **retake**, `RETAKE_BUDGET` | A request waiting on a transcription or prefetch gives its slot back, then has this long to get one again (`server.py:563`). | `contracts.md`, "Limits, pools and slots" |
| **body cap** | The per-route request size limit (`_body_limit`, `server.py:773-782`; the `MAX_*_BYTES` constants). | `contracts.md`, "Limits, pools and slots" |
| `_guarded` | The `do_POST` path for routes outside the slot block: token, body, model, then an optional slot (`server.py:1102-1127`). | `contracts.md`, "Route inventory" |
| **request thread**, **handler thread** | `ThreadingHTTPServer` gives every connection its own daemon thread (`server.py:1924-1925`). | `contracts.md`, "Threads" |
| **registry**, **in-memory store** | A module-level dict in the server holding transcripts, selections, sessions, jobs or previews, guarded by `_cache_lock` and lost on restart (`server.py:35-49`). | `contracts.md`, "In-memory stores" |
| **startup hygiene**, **sweep** | Cleanup that runs once at every `serve` start, after the bind: stale transcription temp dirs and unlisted voices (`startup_hygiene`, `server.py:89-112`). | `contracts.md`, "Scheduled jobs and background work" |
| **stale audio age**, `_stale_audio_age` | How old a transcription temp dir must be before a sweep removes it (`server.py:77-86`). Age only, so a second server's live job is safe. | `data-model.md` § 12 |
| **request log** | The stdlib's one line per request on stderr: client, time, request line, status (`server.py:962-963`). No body and no token. | `architecture.md` § 6 (I-6) |
| **startup lines**, **the token line** | What `serve` prints when it starts, including the token on every start (`server.py:1942-1963`, `:1959`). Never pasted anywhere. | `deployment.md` § 4–5 |

## 5. The model backend and the cache keys

| Term | Meaning | Where it lives |
|---|---|---|
| **stock CLI**, **`claude` backend** | The default model backend: the `claude` command-line tool, called with tldw's own argv (`claude_client.py:189-202`). | `contracts.md`, "Model I/O contract" |
| **custom backend**, `TLDW_LLM_CMD`, `llm_cmd` | Any operator command that reads the prompt on stdin and prints the answer. The env var beats the config key, which beats `claude` (`claude_client.py:205-213`). It gets none of the lean flags. | `contracts.md`, "Environment variables" |
| **model**, `MODELS` | The allowlisted model names, `opus` and `sonnet` (`claude_client.py:164`). The Options picker labels them "Thorough — Opus" and "Fast — Sonnet" (`options.html:41-42`). | `contracts.md`, "Lists mirrored from the server" |
| `use_model`, **pinned model** | The context manager that pins the model on the current thread; a new thread must enter it again (`claude_client.py:164-186`). | `contracts.md`, "Public module entry points" |
| **lean invocation**, **lean flags**, `_LEAN` | The `claude` flags that give the model no tools, no MCP servers and no setting sources, with a fixed system prompt (`claude_client.py:100-111`). Security invariant I-10. | `architecture.md` § 6 |
| **stream-json**, **envelope** | The `claude` CLI's output formats: one JSON envelope for a buffered call, one JSON object per line for a streamed one. | `contracts.md`, "Model I/O contract" |
| **repair retry** | The single buffered retry when the model's JSON doesn't validate; its usage row's step gains `-retry` (`claude_client.py:621-646`). | `contracts.md`, "Model I/O contract" |
| **prompt caching** | The model provider's caching of a repeated prompt prefix: off for one-shot calls, on for Q&A, through `DISABLE_PROMPT_CACHING` (`claude_client.py:122-142`). | `contracts.md`, "Environment variables" |
| **backend fingerprint**, `backend_fingerprint()` | A short hash of the resolved backend argv, model included, so a cache never serves one backend's answer for another (`claude_client.py:227-235`). | `data-model.md` § 2 |
| **prompt fingerprint**, `PROMPT_FINGERPRINT` | A short hash of the summary prompt strings, so a prompt edit turns old summaries into misses (`summarize.py:140-148`). What it covers, and misses, is `data-model.md`'s. | `data-model.md` § 2 |
| **segment fingerprint**, `SEGMENT_FINGERPRINT` | The same for everything that shapes a selection (`core.py:26-45`). | `data-model.md` § 3 |
| **summary key**, `_summary_key` | What names a summary cache file (`server.py:748-770`). Its composition is owned by `data-model.md` § 2. | `data-model.md` § 2 |
| **segment key**, `_seg_key` | What names a segment cache file and keys the in-memory selection registries (`server.py:376-398`). Composition: `data-model.md` § 3. | `data-model.md` § 3 |
| **"change the key, never the file"** | The rule for changing what a cache holds: change its key so old files become misses, rather than rewriting files. | `data-model.md`, "No version markers" |

## 6. The extension and its UI

Journeys through these controls are in [`ux.md`](ux.md).

| Term | Meaning | Where it lives |
|---|---|---|
| **the extension** | The Manifest V3 browser extension in plain JavaScript under `extension/`, with no build step for Chrome. | `architecture.md` § 2.2 |
| **manifest** | `extension/manifest.json` (Chrome) and `extension/manifest.firefox.json` (Firefox): permissions, `host_permissions`, `action`, `name`. | `contracts.md`, "Manifests" |
| **toolbar action**, **"TL;DW this video"** | The toolbar button, the extension's only entry point; its tooltip is `default_title` (`manifest.json:9-12`, `background.js:58`). There is no keyboard shortcut. | `contracts.md`, "Manifest keys" |
| **content script**, `content.js` | The script injected into the YouTube tab on a click. It draws the panel and reads the page, and never calls the server. | `contracts.md`, "Extension message protocol" |
| **Options page** | `options.html` / `options.js`: the extension's settings, saved to `chrome.storage.local`. It calls the server directly. | `contracts.md`, "Extension → server HTTP calls" |
| **shadow root** | The panel lives in an open shadow root under `#tldw-host` (`content.js:150`). Open means YouTube's scripts can read it (architecture § 7, item 9). | `architecture.md` § 5 |
| **toast** | tldw's own one-line notice on the page (`#tldw-toast`, `role="status"`), used when the panel was closed mid-run (`content.js:775-796`). | `contracts.md`, "Fixed messages from the panel and Options" |
| **skip pill** | The small floating control (`#tldw-pill`) with previous, next and stop buttons during skip playback (`content.js:2946-3085`). | `contracts.md`, "Page selectors" |
| **port**, **port name** `tldw` | A long-lived `runtime.connect` channel from the content script to the worker; each piece of work opens its own. The worker drops any port not named `tldw` (`background.js:71`). | `contracts.md`, "Port name" |
| **port message** | One message on a port, such as `summarize`, `probe`, `askDelta`. The extension's internal protocol, not the server's NDJSON. | `contracts.md`, "Content → background port messages" |
| `TLDW_INVOKE`, `TLDW_ERROR` | The two one-shot `tabs.sendMessage` messages the worker sends after a toolbar click (`background.js:54-68`). | `contracts.md`, "Background → content via `tabs.sendMessage`" |
| **ping**, **keepalive** | A port message sent every 20 s and ignored on purpose; it keeps the MV3 worker alive through a long fetch (`background.js:81`). | `contracts.md`, "Content → background port messages" |
| **safety timer**, **panel timer** | The panel's own timeout, so it can say something if the worker goes quiet. | `contracts.md`, "Matching timeouts between panel and worker" |
| **worker budget** | The worker's own timeout per request (`CLIENT_TIMEOUT_MS`, `TRANSCRIBE_TIMEOUT_MS` and the others, `background.js:16-23`). It should run out before the panel timer. | `contracts.md`, "Matching timeouts between panel and worker" |
| **close action**, `closeAction`, **close policy** | What closing the panel mid-run does: `continue` ("Keep working in the background") or `abort` ("Stop and discard") (`options.html:68-71`; `closePolicy`, `content.js:73-78`). | `contracts.md`, "chrome.storage.local keys" |
| **backgrounded run** | A run whose panel was closed under `continue`; it finishes and announces itself with a toast. | `contracts.md`, "Fixed messages from the panel and Options" |
| **Regenerate**, `refresh` | The `↻` control: summarize again, ignoring the cached summary but keeping the transcript (`content.js:274`; `refresh`, `server.py:1217`). | `contracts.md`, "Accessible names" |
| **" (cached)"** | The heading suffix meaning "not freshly generated for this click" (`content.js:956`). | `contracts.md`, "Text the code depends on" |
| **"transcribed from audio" note** | The panel's provenance note, shown when `transcript_source` starts with `audio:` (`content.js:3191-3193`). | `contracts.md`, "Result payload keys the panel reads" |
| **"trimmed video" note** | The header note giving the key moments' total length (`trimmedNote`, `content.js:2962`, `:2976`). | `contracts.md`, "Text the code depends on" |
| **Play key moments** | The `⏭` control that starts skip playback (`.playkey`, `content.js:877`). | `contracts.md`, "Accessible names" |
| **Listen**, **Stop**, **download** | The speech controls: generate audio, stop generating, save the mp3 (`.listen`, `.dl`; `content.js:878`, `:3165-3166`). | `contracts.md`, "Accessible names" |
| **Transcribe the audio**, **Stop transcribing** | Accept a transcription offer, or stop a running transcription (`.transcribe`, `.stoptx`; `content.js:845`, `:713-715`). | `contracts.md`, "Accessible names" |
| **sponsor ask** | The panel's occasional request for sponsorship, shown after enough summaries and a limited number of times (`sponsorShown`, `sponsorDismissed`; `SPONSOR_AFTER_VIDEOS`, `SPONSOR_MAX_SHOWS`, `content.js:40-41`). | `contracts.md`, "chrome.storage.local keys" |
| `serverUrl` | The Options "Server URL" setting: where every request, and the token, goes (`background.js:10`, `options.js:3`). | `contracts.md`, "chrome.storage.local keys" |
| **host permission** | The manifest's `host_permissions`, exactly `http://127.0.0.1:8765/*`, coupled to the default `serverUrl` and port (`manifest.json:7`). | `contracts.md`, "Manifest permissions" |
| `DEFAULTS` | The settings defaults, defined in three places that differ: `background.js`, `options.js` and `content.js`'s inline defaults. | `contracts.md`, "chrome.storage.local keys" |
| **mirrored lists** | Extension copies of a server list: `MODELS`, `WHISPER_POLICIES`, `VOICE_OPTIONS`, `VOICE_FALLBACK`, `RATIOS`, `sanitizeField`. Some are test-guarded and some are not. | `contracts.md`, "Lists mirrored from the server" |
| **hotkey shield** | Window-level capture listeners that stop keystrokes in the panel reaching YouTube's shortcuts (`onKey`, `shieldKey`, `content.js:289-315`). | `contracts.md`, "Keys" |
| **Tab trap** | Tab and Shift+Tab wrapping focus inside the panel (`content.js:328-340`); its known gaps are T-009's. | `contracts.md`, "Keys" |
| **keyboard and accessibility baseline** | The invariant that every panel control works from the keyboard, keys never reach YouTube, and the panel is announced as a modal dialog. The target standard is `ux.md`'s. | `contracts.md`, "Keyboard and accessibility baseline"; `ux.md` |
| **Firefox build**, `dist-firefox/` | The copy `extension/build-firefox.sh` makes for Firefox, with the Firefox manifest renamed (`build-firefox.sh:7-12`). It copies a fixed file list. | `contracts.md`, "build-firefox.sh copy list" |
| **reload** | After an extension change: reload the extension at `chrome://extensions`, then the YouTube tab, or Chrome keeps the old content script (`extension/README.md:8-11`). | `onboarding.md`, "Dev setup" |
| **persona**, **journey** | Who uses tldw and the paths they take through it. | `ux.md` |
| **Cover listed words** | The panel toggle (`.covertoggle`) and the feature behind it (T-020): it silences the video for each listed word and, by default, plays a bleep over it. It runs on after the panel is closed. | `ux.md`, Journey 8; `contracts.md`, "Word timing (`/align/stream`, T-020)" |
| **listed words**, `muteWords` | The user's list for "Cover listed words", edited in Options as "Words to cover"; absent means the shipped default in `extension/mute_words.js`, `""` means off. Never sent anywhere. | `domain.md` 2.15 |
| **hit** | One caption line that holds a listed word, or YouTube's `[ __ ]` marker. | `domain.md` 2.15 |
| **mute range**, **line range**, **word range** | A span of media time the video is silenced for: a whole caption line (up to 8 s), or narrowed to one word (at least 0.3 s). | `domain.md` 2.15 |
| **word-timed**, **by caption line** | Whether a hit has been narrowed to its word by Whisper, or is still covered for its whole caption line (the fallback, which always works). | `domain.md` 2.15 |
| **alignment window**, **word timing**, **alignment** | A few seconds of audio around a hit, sent (as times only) to `POST /align/stream`, where Whisper times every word in it; and that process. | `api.md` § 7.1; `data-model.md` store 13 |
| **cover sound**, `muteSound` | Bleep (default), mute or moo. Every mode silences the video; bleep and moo also play a generated sound for at most 1.5 s. | `contracts.md`, `chrome.storage.local` keys |
| **mute pill** | tldw's on-player control while covering is on (`#tldw-mute-pill`), in its own shadow root, with a Stop button. Not the skip pill. | `contracts.md`, "Page selectors" |

## 7. Errors and messages

| Term | Meaning | Where it lives |
|---|---|---|
| `TldrError` | The base class of tldw's expected failures; its subclasses are `BadUrlError`, `NoTranscriptError`, `TranscriptTooLongError`, `ClaudeError` and `TldrTimeoutError` (`__init__.py:3-29`). The class decides the HTTP status (`_STATUS`, `server.py:711-717`). | `contracts.md`, "Errors" |
| **the three error layers** | A JSON error before a stream, an `error` event inside one, and the stdlib's HTML page. | `api.md` § 6 |
| **in-stream error** | An NDJSON `error` event sent after a `200`, carrying the status the same failure would have had before the stream. | `contracts.md`, "The three layers" |
| **interface copy**, **interface text** | Any string a user can read. `TldrError` text is sent as-is, so rewording one changes what users read. | `api.md` § 6 |
| **pass-through** | A status for which the extension shows the server's own text, rather than fixed copy. | `contracts.md`, "What the extension does with a status" |
| **mapper**, `httpError`, `askError`, `speakError` | The worker's three functions that turn a status into user text, plus the Options page's own mapping (`background.js:514-521`, `:565-571`, `:645-655`; `options.js:105-107`). Their `(status, detail)` signature is a test contract. | `contracts.md`, "User-facing message contracts" |
| **"names a next step"** | The invariant that every message tells the user what to do next. Its current exceptions are listed, and tracked in T-008. | `contracts.md`, "Every message names a next step" |
| **"ended unexpectedly"** | The worker's message when a stream closes with no terminal event (`background.js:284-286`). | `contracts.md`, "Fixed messages from the worker" |
| **YouTube rate-limiting** | YouTube refusing a burst of requests from the machine. tldw surfaces it as a `500` with its own text, not a `429` (`metadata.py:66-76`). | `contracts.md`, "Pass-through `TldrError` text"; [`runbooks/youtube-rate-limit.md`](runbooks/youtube-rate-limit.md) |
| `reason` | On `/align/stream` errors (T-020): a machine-readable cause (`whisper_off`, `busy`, `rate_limited`, …) the panel picks its message by. | `contracts.md`, "Word timing (`/align/stream`, T-020)" |
| `internal error` | What any non-`TldrError` exception becomes; its detail goes only to the server log (`server.py:1306-1309`). | `contracts.md`, "Errors" |

## 8. Storage and data

Every store is described in [`data-model.md`](data-model.md), which numbers them 1 to 13 and owns their TTLs and caps.

| Term | Meaning | Where it lives |
|---|---|---|
| **store** | Anything tldw keeps: a file or directory, a value in process memory, or the extension's storage. There is no database. | `data-model.md`, "Stores at a glance" |
| **transcript cache**, **summary cache**, **segment cache** | Stores 1, 2 and 3: JSON files under `~/.cache/youtube-tldw/` (`txcache.py:21-27`). | `data-model.md` §§ 1–3 |
| **TTL**, **cap**, **prune** | How long an entry stays valid, how many a store may hold, and the removal that enforces the cap on every write. The values are per store. | `data-model.md`; `contracts.md`, "In-memory stores" |
| `saved_at` | The top-level epoch-seconds field in every cache file; a file without it is a miss. | `contracts.md`, "Cross-cutting data invariants" |
| **temp-then-replace**, `*.tmp` | Cache writes go to `<name>.tmp`, then replace `<name>.json`, so a reader never sees half a file (`txcache.py:84-86`). | `data-model.md` § 12 |
| **write-through** | The in-memory transcript and segment registries also write their disk store (`server.py:317`, `:405`). | `contracts.md`, "In-memory stores" |
| **best effort** | A cache or usage write that fails never fails the request (`txcache.py:88-92`). | `contracts.md`, "Cross-cutting data invariants" |
| **tolerant read** | A cache read treats any bad, expired or older-shape file as a miss. Usage and config reads tolerate bad lines and files, but not wrong value types. | `data-model.md`, "No version markers" |
| **alignment cache**, **word timings** | Store 13 (T-020), `~/.cache/youtube-tldw/align/`: the words Whisper heard in each alignment window, keyed without the word list; `tldw cache` calls it "word timings". | `data-model.md` § 13 |
| **version marker** | A schema version in a record. Only store 13 has one, inside its key (`ALIGN_SHAPE`). | `data-model.md`, "No version markers" |
| **config**, `config.json`, **config key** | Store 5, `~/.config/youtube-tldw/config.json`, re-read on every call. `tldw config set` accepts any key; only the keys `contracts.md` lists are read. | `contracts.md`, "Config keys" |
| **hyphen spelling**, **keymap** | `tldw config set` turns `-` into `_`, and validates only the hyphen spellings `whisper`, `whisper-max-minutes` and `whisper-model` (`cli.py:318-337`). | `contracts.md`, "`config` behaviour" |
| **token file** | Store 6, `~/.config/youtube-tldw/token`: the bearer token in plain text, mode 0600 applied after the write. | `data-model.md` § 6 |
| **voices dir**, **voice-tracking marker**, **idle voice**, **unlisted voice** | Store 7. The marker `.spoken-tracking-since` records when tracking began; an idle voice hasn't been spoken with for a while; an unlisted voice is one the allowlist can't select. `tldw cache clean` and startup hygiene remove them by those rules. | `data-model.md` § 7 |
| **Whisper models dir** | Store 8, `~/.cache/youtube-tldw/whisper/`; never deleted by tldw. | `data-model.md` § 8 |
| **Claude working dir**, `CLAUDE_DIR` | Store 9, `~/.cache/youtube-tldw/claude/`: the `claude` CLI's cwd for tldw's calls, so its sessions are filed apart from the user's (`claude_client.py:113-152`). | `data-model.md` § 9 |
| **Claude CLI sessions** | Store 10, under `~/.claude/projects/`: owned by the `claude` CLI. Q&A sessions there hold the transcript, questions and answers. | `data-model.md` § 10 |
| **temp dirs** | `youtube-tldw-`, `youtube-tldw-tx-` and `youtube-tldw-audio-` working directories (store 12); only the last is swept. | `data-model.md` § 12 |
| **Hugging Face hub cache** | Hugging Face's own model cache, which `tldw cache` lists but never deletes. | `data-model.md`, "Hugging Face hub cache" |
| `tldw cache`, `tldw cache clean` | Reports the stores' sizes; `clean` removes expired and stray entries, stale temp dirs and unlisted or idle voices (`cli.py:376-440`). `--dry-run` removes nothing. | `data-model.md`, "What `tldw cache` covers" |
| `tldw usage` | Reads the usage log into cost and call tables (`cli.py:457-573`). | `data-model.md` § 4 |
| `chrome.storage.local` | The extension's persistent settings, token included, in plain text. Never `.sync`. | `contracts.md`, "chrome.storage.local keys" |
| **classification**, **Secret**, **Personal** | The personal-data classes per store. Secret: the token file and `chrome.storage.local`. Personal: activity and viewing history, and the user's own questions. | `data-model.md`, "Personal data and protection" |
| `HOME_PATHS` | The test list of every home-relative store path; a new store must be added there (`tests/conftest.py:26-37`). | `contracts.md`, "Data stores" |

## 9. Security

| Term | Meaning | Where it lives |
|---|---|---|
| **bearer token**, **the token**, `<your-token>` | tldw's only secret: one token that authorizes every `POST` route. Docs write `<your-token>` in its place and never a real value. An entity. | `domain.md`; `architecture.md` § 4; `api.md` § 4 |
| **trust boundary**, **B1–B7** | The seven places where less-trusted input enters tldw, from loopback HTTP (B1) to other OS users (B7). | `architecture.md` § 5 |
| **STRIDE** | The threat categories (spoofing, tampering, repudiation, information disclosure, denial of service, elevation of privilege) applied to each boundary. | `architecture.md` § 5.1 |
| **security invariant**, **I-1…I-17** | What must stay true for the security model to hold, each with its blast radius and guard. | `architecture.md` § 6 |
| **known weakness** | A security fact recorded but not fixed, each with its follow-up task. | `architecture.md` § 7 |
| **prompt-injection boundary**, **B6** | The model reads untrusted transcript and page text, so injected text can steer its output; the lean flags stop it acting. | `architecture.md` § 5 |
| **allowlist** | A fixed set a request value must belong to before it reaches argv or a file: URL hosts, models, voices, `lang`, whisper policy, cookie browsers, whisper models (I-8). | `architecture.md` § 6 |
| **Origin check**, **CORS**, `_origin_allowed` | Decides only whether CORS headers are sent; a request is processed whatever its Origin. Never authentication (`server.py:917-939`). | `architecture.md` § 7, item 3 |
| **DNS rebinding** | A web page rebinding its own name to `127.0.0.1` to read the open GETs, since the server never checks `Host`. | `architecture.md` § 7, item 4 |
| **subprocess chokepoint**, `proc.run` | Every external command runs through `proc.py`: argv lists, no shell, timeouts (`proc.py:1-186`). I-7. | `architecture.md` § 2.1 |
| **caption host allowlist** | The direct caption fetch accepts only https YouTube and Google video hosts (`is_safe_caption_url`, `metadata.py:129-139`). I-13. | `architecture.md` § 6 |
| **cookies**, `ytdlp_cookies`, `COOKIE_BROWSERS` | Opt-in: yt-dlp reads one allowlisted browser's cookie store (`metadata.py:44-55`). The cookie database is classed Secret. | `contracts.md`, "Config keys"; `data-model.md` |
| **what leaves the machine** | The data sent to Anthropic, YouTube and Hugging Face, and when. tldw is not "fully private". | `architecture.md` § 8 |

## 10. Tests and CI

| Term | Meaning | Where it lives |
|---|---|---|
| **the suite** | tldw's pytest tests, run from the repo root. | `testing.md`, "At a glance" |
| `MIN_TESTS`, **floor** | The least number of tests CI accepts, raised by hand when tests are added (`scripts/ci-tldw-tests.sh:27`). | `testing.md`, "Coverage gates" |
| **full pass**, **zero skips** | CI fails on any failure, error or skip (`scripts/ci-tldw-tests.sh:58-62`). | `testing.md`, "Coverage gates" |
| **contract level** | `tests/test_server.py`, which runs the real HTTP server; the level that guards the API. | `testing.md`, "Integration and contract" |
| **extension harness**, **node harness** | `tests/js/*.js`, which run the extension's real functions in node, driven from `tests/test_extension_render.py`. | `testing.md`, "Extension" |
| **source marker**, **extraction marker**, `grab()` | The literal source text a harness cuts a function out by. `grab()` and `between()` fail on a missing marker; a bare `indexOf` does not. | `contracts.md`, "Source text tests depend on" |
| **docs-contract check**, `test_docs_contracts.py` | The test that fails if the code gains an item `contracts.md` doesn't list, or a listed route, env var or storage key disappears. | `testing.md`, "Docs-contract check" |
| **reverse check**, `FLOORS`, `EXCLUDED` | The docs-contract check's own guards: listed-but-gone items fail; each extractor has a count floor; each omission needs a reason. | `testing.md`, "Docs-contract check" |
| **leak check** | The deterministic scan of `docs/` and new task files for home-directory paths, secret patterns and the template-placeholder marker (`tests/test_docs_contracts.py:853-870`). | `testing.md`, "Docs-contract check" |
| **isolation**, `_isolated_home`, `_fresh_server_state` | The autouse fixtures that give every test a fake home, a clean environment, stub `claude` and `yt-dlp`, and fresh server state (`tests/conftest.py:46`, `:88`). | `testing.md`, "Isolation" |
| **stub**, **seam** | A stand-in for a real program, and a replaceable hook such as `claude_client._Timer` that lets a test drive timing (`claude_client.py:294-296`). | `testing.md`, "Flake policy" |
| **flake** | A test that sometimes fails without a code change. A bug: fix it, never re-run until green. | `testing.md`, "Flake policy" |
| **guarding test**, **unguarded** | The test that fails if a contract regresses; a contract with none is unguarded, and gaining one is in T-010. | `testing.md`, "Regression-safety check" |
| **fixture suites** | `scripts/test/*.sh`: shell tests of the hooks and CI scripts, not of tldw. | `testing.md`, "Procedure" |
| **required checks** | The four CI check names the `hackathon/demo` ruleset requires, matched byte for byte. | `deployment.md` § 7 |
| **procedure checks**, **trusted gate** | The CI job that runs the target branch's own copy of the gates against a PR, so a PR can't weaken them for itself (`scripts/ci-pr-checks.sh`). | `deployment.md` § 7 |
| **hash-locked install** | CI installs only pinned, hashed binary wheels from `scripts/requirements-ci.txt`. | `testing.md`, "Security and dependency scans" |
| **FU-*n*** | A numbered follow-up in a Tier 1 doc's follow-ups list, tracked in the task it names (for example `testing.md`'s FU-12, in T-010). | `testing.md`, "Follow-ups" |

## 11. The playbook and its process

| Term | Meaning | Where it lives |
|---|---|---|
| **the playbook**, `AGENTS.md` | The repo's rules for how agents and humans work, read at the start of every session. `CLAUDE.md` mirrors it, with this repo's notes. | `AGENTS.md`; `onboarding.md`, "Where to look next" |
| **agentic starter**, **upstream** | The template repo this fork adopted its playbook from, at `bb9406f` (T-003). Departures are ADR 0002. | `AGENTS.md:9`; `decisions/0002-hackathon-fork-departures.md` |
| **this fork**, **integration branch**, `hackathon/demo` | This checkout is a hackathon fork. Its PRs target `hackathon/demo`, which is thrown away after the demo and never merges into `main` (`AGENTS.md:9`). | `deployment.md` § 1, § 6 |
| **feature branch** | A branch named `feat/`, `fix/`, `chore/` or `refactor/` plus a short name (`AGENTS.md:344-348`). | `onboarding.md`, "Your first PR under this playbook" |
| **rebase-merge** | The only merge method on `hackathon/demo`; it rewrites commit SHAs, which is why reviewed content is identified by a tree hash. | `deployment.md` § 8 |
| **agent**, **role** | One of the 9 specialists (`AGENTS.md:80-96`), each a Claude Code subagent with a brief in `.claude/agents/`. In this repo one human stands behind all 9. | `ownership.md`, "Agent roles" |
| **Review Coordinator** | The agent that runs review cycles, gathers sign-offs and writes the artifact; it wins on process. | `AGENTS.md:80-96`; `docs/reviews/README.md` |
| **Test Engineer**, **SDET** | The testing agent; its brief is `.claude/agents/sdet.md`. The legacy playbook called it SDET. | `ownership.md`, "Agent roles" |
| **wins on** | The domain in which an agent's view prevails in a disagreement (for example the Analyst on scope, the Architect on security). | `AGENTS.md:114-134`; `ownership.md`, "Tiebreak rules" |
| **redline** | A non-negotiable line in an agent's brief. Crossing one BLOCKs unless an ADR records a named, dated acceptance (`AGENTS.md:21`). | `.claude/agents/*.md`; `decisions/README.md`, "Status lifecycle" |
| **lens**, **the 7 strategic lenses** | Seven stakeholder views (CEO, Purchasing, PM, Adopter, Builder, Investor, Marketing) that every major decision is checked through (`AGENTS.md:138-150`). | `docs/reviews/README.md`, "Writing an artifact" |
| **major decision** | A choice that shapes the product's direction, such as architecture, scope or tooling; it needs the lenses. | `AGENTS.md:138-150` |
| **review cycle**, **round** | The 3 rounds, in order: Round 1 Analysis (Analyst, Architect, Data Engineer), Round 2 Implementation (Backend, Frontend, UX), Round 3 Verification (Test Engineer, DevOps) (`AGENTS.md:100-110`). | `docs/reviews/README.md` |
| **plan review**, **build review**, **draft review** | The cycle run on a plan before any code, then on the built change (for docs, the draft). | `onboarding.md`, "Your first PR under this playbook" |
| **review scope** | Which agents a change needs, by its row in `AGENTS.md` § 5 (`AGENTS.md:154-168`). A `/docs/*` change needs all 9. | `docs/reviews/README.md` |
| **full cycle**, **lightweight review** | All rounds and agents, as against the one-agent path for trivial changes such as typos or lockfile bumps (`AGENTS.md:166-168`). | `AGENTS.md` § 5 |
| **review artifact** | The written record of a review: status, blockers, warnings, suggestions, sign-offs and, in `final.md`, the content hash. One folder per review under `docs/reviews/`. | `docs/reviews/README.md` |
| **sign-off**, **spot-check** | An agent's verdict, with the `path:line` places it read in the source to check the change. | `docs/reviews/README.md`, "Writing an artifact" |
| **PASS**, **PASS WITH ITEMS**, **BLOCK** | The three verdicts and artifact statuses (`AGENTS.md` § 9). | `docs/reviews/README.md` |
| **blocker**, **warning**, **suggestion** | Must fix before merge; fix before the next review; consider for polish. | `docs/reviews/_TEMPLATE.md` |
| **content hash**, **tree hash** | The `git write-tree` hash of the signed content, `final.md` left out, so a rebase-merge can't change what was signed. | `docs/reviews/README.md`, "Sign-off is on the final content" |
| **contract** | Anything an autonomous commit could break: a route, body key, event, message, storage key, store format, signature, env var, config key, integration or selector. All are listed in `contracts.md`, which is binding. | `contracts.md` |
| **contract change** | A change that alters or removes a `contracts.md` entry. It needs the full cycle, the regression-safety check and `contracts.md` updated in the same PR. | `AGENTS.md:358-365`; `api.md` § 10 |
| **invariant**, **blast radius** | What must stay true about an entry, and what breaks, or who is exposed, if it stops being true. Every contract entry states both. | `contracts.md`; `AGENTS.md:178-189` |
| **regression-safety check**, **regression-safety line** | The step before a commit that proves no documented contract silently broke, and the one-line record of it in the artifact. | `testing.md`, "Regression-safety check"; `AGENTS.md:358-365` |
| **consensus gate**, **unanimous consensus**, **the trailer** | No file in `docs/` changes without all 9 agents' sign-off. A commit touching `docs/` must carry `unanimous-consensus: T-NNN`; the hook and CI refuse one without it. | `docs/README.md`, "Hard rule — unanimous consensus" |
| **consensus stall**, **tiebreak** | When the cycle deadlocks, the area's DRI decides and the reasoning becomes an ADR (`AGENTS.md:74`). In this repo that is the owner. | `ownership.md`, "Tiebreak rules" |
| **DRI** | Directly Responsible Individual: the person who decides for an area. Here, the owner for every area. | `ownership.md` |
| `CODEOWNERS`, **ruleset** | GitHub's path-to-reviewer file, mirrored by `ownership.md`; and the server-side branch protection on `hackathon/demo`. | `ownership.md`; `deployment.md` § 8 |
| **hard gates** | The rules with no exceptions, such as no code without a reviewed plan and no `docs/` edit without consensus (`AGENTS.md:430-445`). | `AGENTS.md` § 8 |
| **Grounding & Completeness Protocol** | The six rules every doc is held to: grounded, no invented facts, exhaustive where it's a contract, invariants and blast radius, verification recorded, a freshness footer (`AGENTS.md:178-189`). | `docs/README.md`, "The Grounding & Completeness Protocol (summary)" |
| **grounded**, `path:line` | Every factual claim cites the file and line it comes from. | `docs/README.md` |
| `UNKNOWN — needs human confirmation` | The marker for a fact the code doesn't settle. It always has a follow-up, usually T-015. Never replaced by a guess. | `AGENTS.md:183`; `tasks/T-015.md` |
| `N/A — <reason>` | How a category that doesn't apply is written: always with its reason, never left out. | `docs/README.md`, "Not in this repo" |
| **"met as N/A" / "met differently"** | The note appended to a task's seeded acceptance criterion that doesn't fit this repo, so nothing is quietly narrowed. | `tasks/T-001.md` (Tier 1 and Tier 2 plans) |
| **descriptive**, **forward-looking**, **pending** | A descriptive doc describes code that exists; a forward-looking one describes intended state. Every doc here is descriptive, and something not yet built is called "pending". | `docs/README.md`, "The Grounding & Completeness Protocol (summary)" |
| **"How this was verified"**, **"Update this doc when"** | The two closing sections of every doc: how it was checked against the code, and which code changes require it to change (the freshness contract, `AGENTS.md:187`). | `AGENTS.md:178-189` |
| **Docs Bootstrap Workflow** | Building `docs/` in two tiers under the consensus rule (`AGENTS.md` § 6). Here it is T-001. | `docs/README.md`, "What goes here" |
| **Tier 1**, **Tier 2** | Tier 1 is the docs needed before any feature work, including the full contract inventory; Tier 2 (`domain.md`, `ux.md`, `glossary.md`, `runbooks/`) is due by the end of the first feature sprint (`AGENTS.md:191-216`). | `docs/README.md`, "What goes here" |
| **Knob 1**, **Knob 2**, **Knob 3** | The three ways to scale the playbook down: one human wearing several agent hats; a wider lightweight path; deferring, never skipping, Tier 2 (`AGENTS.md:528-539`). | `ownership.md`, "Agent roles"; `docs/README.md`, "§ 0 Step 3 in this repo" |
| **session-start read** | The docs an agent reads at the start of every session: all of `docs/` except the legacy review files (`AGENTS.md:52-56`). | `docs/README.md`, "How to use this folder during a session" |
| **legacy review files** | The 13 flat files in `docs/reviews/` from before the playbook. Non-binding history; `PLAN-extension.md` must not move, because the code cites it. | `docs/README.md`, "Legacy review files" |
| **task ledger**, **task file**, **task ID** | `tasks/`, one file per task named `T-NNN.md`; subtasks use dotted IDs such as `T-001.3`. Every commit names its task ID. | `tasks/README.md` |
| **task states** | `proposed`, `in-review`, `in-progress`, `completed`, `blocked`, `cancelled`. | `tasks/README.md`, "States" |
| **frontmatter**, **activity log**, `INDEX.md` | A task file's YAML header is its source of truth; its activity log is append-only; `tasks/INDEX.md` is regenerated from the frontmatter, never hand-edited. | `tasks/README.md` |
| **acceptance criteria** | A task's testable definition of done, owned by the Analyst. | `.claude/agents/analyst.md` |
| **follow-up task**, **deferred** | A task (T-004 to T-018) opened to track a fact the Tier 1 docs recorded but didn't fix. The owner deferred them all; new findings are appended to the closest one, not opened as new tasks (`tasks/T-001.md:314`). | `docs/README.md`, "Follow-up tasks from the Tier 1 bootstrap" |
| **ADR**, **Architectural Decision Record** | One file per decision under `docs/decisions/`, numbered `NNNN`. Never edited after merge, only superseded. | `decisions/README.md` |
| **ADR status** | `proposed`, `accepted`, `deprecated` or `superseded by NNNN`. | `decisions/README.md`, "Status lifecycle" |
| **template placeholder**, `.placeholder-allow` | The bracketed marker the hooks and CI refuse in a new line (the pattern at `scripts/ci-checks.sh:112`), and the allowlist of files exempt from that check. Templates here use `<angle>` slots instead. | `docs/reviews/README.md`, "Writing an artifact" |
| **hooks** | `.githooks/pre-commit`, `commit-msg` and `pre-push`, which enforce the gates locally. In this repo they are installed per worktree. | `onboarding.md`, "Hooks: installed per worktree" |
| **PR body headings** | The six headings CI requires in a pull request body. | `onboarding.md`, "Your first PR under this playbook" |
| **scope**, **scope creep** | What a PR may contain: only what its acceptance criteria name. The Analyst wins on it. | `.claude/agents/analyst.md` |

## 12. Operations

| Term | Meaning | Where it lives |
|---|---|---|
| **runbook**, **playbook (runbook sense)** | One operating procedure for a user's own install, opening with the message the user sees. Not the same as "the playbook", which is `AGENTS.md`. | `runbooks/README.md` |
| **severity**, **Sev1–Sev3** | How much a failure costs the user, scaled to a single-user local tool. | `runbooks/incident-severity.md` |
| **on-call** | N/A: no rotation, paging or escalation, because tldw is not hosted and has one owner. | `runbooks/on-call.md`; `ownership.md`, "On-call" |
| **environment** | Where tldw runs: the user's machine is the only runtime; staging and production are N/A. | `deployment.md` § 1 |
| **promotion path**, **release** | How a change reaches `hackathon/demo`, and how a version is cut on `main` (a version-bump commit plus an annotated tag). | `deployment.md` § 6, § 9 |
| **rollback** | Going back to an earlier version on a user's install, with both halves (server and extension) moved together. | `deployment.md` § 10 |
| **second, isolated instance** | A second `tldw serve` with its own `HOME` and `TMPDIR`, so it has its own token, config and caches. | `deployment.md` § 4 |

## 13. Terms the playbook uses that don't apply here

| Term | Status here | Where it lives |
|---|---|---|
| **workspace model**, `workspace.manifest.md`, `integration-map.md`, `docs/repos/` | N/A: tldw is a single repo (`AGENTS.md` § 6.5). | `docs/README.md`, "Not in this repo" |
| **doc aggregation**, **golden source** | N/A: a multi-repo mechanism (`AGENTS.md` § 6.6). | `docs/README.md`, "Not in this repo" |
| **feature flag** | N/A: no flag system; env vars, config keys and opt-in body fields do that job. | `contracts.md`, "Feature flags" |
| **webhook**, **cron**, **queue** | N/A: tldw sends and receives no webhooks, and nothing runs on a schedule. | `contracts.md`, "Category index" and "Scheduled jobs and background work" |
| **database**, **table**, **migration**, **foreign key** | N/A: file-backed stores with no engine. | `data-model.md`, "Tables, indexes, foreign keys, partitioning, replication" |
| **API versioning** | None: no version in any path or header. `/health` reports a version nothing reads. | `api.md` § 8 |
| **staging**, **production**, **hosting** | N/A: no hosted service; the server refuses a non-loopback bind. | `deployment.md` § 1; ADR 0001 |

## How this was verified

- **The term list was derived by reading every doc, not sampled.** Read in full: `docs/README.md`, `architecture.md`, `contracts.md`, `data-model.md`, `onboarding.md`, `ownership.md`, `decisions/README.md`, `reviews/README.md` and `reviews/_TEMPLATE.md`. Read by section: `api.md` (§§ 1–9), `testing.md`, `deployment.md` (§§ 1–9), `stack.md` (categories and license posture). Headings and bold terms were listed across every doc with `grep -n '^#'` and `grep -ohE '\*\*[^*]+\*\*'` to catch terms outside the sections read in full. Playbook terms were checked in `AGENTS.md` (Quick Summary, §§ 0–9, § 12) and `tasks/README.md`.
- **Code identifiers** were checked in the source, not taken from the docs: `Span` and `spans_from_cue_ranges` (`spans.py:15-40`), `Polish` (`videomode.py:135-157`), `TextResult` and `VideoSelection` (`summarize.py:174-185`), `Summary` (`core.py:47-53`), `Interaction` (`usage.py:56-63`), the pool constants (`server.py:556-563`), `_tx_permit` and `WHISPER_POLICIES` (`server.py:64-66`), the registries (`server.py:35-49`), `_prompt_fingerprint` (`summarize.py:140-148`), `backend_fingerprint` (`claude_client.py:227-235`), `CAPTIONS` (`txcache.py:36`), `MIN_CLIP_MS` and `BOUNDARY_PAD_MS` (`core.py:22-23`), `build_spoken_script` (`audio.py:353`), and the CLI flags' help text (`cli.py:47-95`).
- **UI terms** were read in `extension/options.html` (every `<label>` and `<option>`, `:27-79`) and in `contracts.md`'s accessible-names table, which cites `content.js`.
- **The synonym table** (section 1) was built by finding each thing under each of its names in the code, the UI copy and the docs.
- **Ownership of terms:** entities point to `domain.md`, personas and journeys to `ux.md`, severity and procedures to `runbooks/`. Those three were drafted in parallel with this doc. Their drafts were read for headings, and this file was reconciled against `domain.md` § 2.14 ("Not entities") and § 5 ("Vocabulary"): the clip/segment/span distinction, "summary ratio", "captions" as one source of a transcript, and "TL;DW" as the output's name follow `domain.md`. The links to `runbooks/` name files that exist (`ls docs/runbooks`). The Tier 2 review re-checks that nothing here contradicts the three.
- **No values were restated** where an owning doc holds them (TTLs, caps, pool limits, message text); each row cites the owning section instead.
- **The leak rules** (`tests/test_docs_contracts.py:853-870`): no home-directory paths, no token values, no real video ids, and the placeholder marker is described, not written.

## Update this doc when

- A doc in `docs/` is added, removed or renamed, or a doc adds a term this glossary doesn't define.
- `contracts.md`, `data-model.md` or `api.md` renames a section this doc cites by name.
- A code identifier named here is renamed or removed: in particular the route names, NDJSON and port message types, `_to_payload`'s keys, the pools and `_tx_permit`, the fingerprints and keys (`_summary_key`, `_seg_key`), `Span`, `Polish`, `VideoSelection`, `Interaction`, `TldrError` and its subclasses, or the mappers.
- The extension's UI copy changes a control's label, the Options labels or options (`extension/options.html`), or the toolbar title (`extension/manifest.json`).
- `domain.md`, `ux.md` or `runbooks/` defines a term differently from this file, or moves a term this file links to.
- `AGENTS.md` changes its rounds, roles, lenses, gates, tiers or knobs, or `tasks/README.md` changes the task states.
