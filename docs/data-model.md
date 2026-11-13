# Data model

**Status:** descriptive. This describes the code as it is on `chore/docs-tier1`, not a plan.

**tldw has no database.** Its data is JSON files on disk, files that other tools write for it (Piper voices, Whisper models, Claude CLI sessions, and yt-dlp's own cache), values in process memory, and the extension's `chrome.storage.local`. There are no tables, indexes, foreign keys, migrations, schema dumps or version markers. This doc gives each store's shape and lifecycle, its personal-data classification, and how to change a format without breaking what reads it.

The flat inventory, with every store's invariant, blast radius and `path:line`, is in [`contracts.md`](contracts.md) under **Data stores**. That section, together with this doc, is the contract. Environment variables and config keys are inventoried there too. `chrome.storage.local` keys are in `contracts.md`'s extension storage section.

All examples below are invented. Video IDs are made-up 11-character strings, and hashes and session IDs are template placeholders.

## Stores at a glance

| # | Store | Path | Written by | Read by | Format |
|---|---|---|---|---|---|
| 1 | Transcript cache | `~/.cache/youtube-tldw/transcripts/` | `tldw serve` | `tldw serve`, `tldw cache` | JSON per file |
| 2 | Summary cache | `~/.cache/youtube-tldw/summaries/` | `tldw serve` | `tldw serve` → extension | JSON per file |
| 3 | Key-moment (segment) cache | `~/.cache/youtube-tldw/segments/` | `tldw serve` | `tldw serve` → extension | JSON per file |
| 4 | Usage log | `~/.config/youtube-tldw/usage.jsonl` | `tldw serve`, `tldw <url>` | `tldw usage`, `tldw serve` (`stats`) | JSON Lines, append-only |
| 5 | Config | `~/.config/youtube-tldw/config.json` | `tldw config` | every process, on every read | one JSON object |
| 6 | Token | `~/.config/youtube-tldw/token` | `tldw serve` | `tldw serve` | plain text |
| 7 | Voices | `~/.cache/youtube-tldw/voices/` | Piper's downloader, tldw (mtimes, marker) | Piper, hygiene | ONNX model + JSON config |
| 8 | Whisper models | `~/.cache/youtube-tldw/whisper/` | faster-whisper | faster-whisper | the library's own layout |
| 9 | Claude working dir | `~/.cache/youtube-tldw/claude/` | tldw (creates it) | the `claude` CLI (as its cwd) | a directory |
| 10 | Claude CLI sessions | `~/.claude/projects/<encoded tldw claude dir>/` | the `claude` CLI | the `claude` CLI (`--resume`) | the CLI's own |
| 11 | Output files | `~/Downloads/youtube-tldw/tldws/` by default | `tldw <url>` only | the user | `.md`, `.mp4`, `.mp3` |
| 12 | Temp dirs and `*.tmp` | `$TMPDIR/youtube-tldw-*`, `<cache dir>/*.tmp` | CLI, server | the same | working files |
| 13 | Alignment cache (T-020) | `~/.cache/youtube-tldw/align/` | `tldw serve` (`/align/stream`) | `tldw serve`, `tldw cache` | JSON per window |
| — | Hugging Face hub cache (read only) | `$HF_HUB_CACHE` / `$HF_HOME/hub` / `~/.cache/huggingface/hub` | not tldw | `tldw cache` (lists, sizes) | not tldw's |
| — | yt-dlp's own cache (third-party) | `UNKNOWN — needs human confirmation` | `yt-dlp`, run by tldw | `yt-dlp` | not tldw's |
| — | Browser cookie database (third-party, read only) | `UNKNOWN — needs human confirmation` | the browser | `yt-dlp`, only when cookies are configured | not tldw's |
| — | In-memory registries | process memory | server, extension | the same | Python / JS objects |
| — | `chrome.storage.local` | the browser profile | extension | extension | see `contracts.md` |

## The stores

### 1. Transcript cache

- **Path:** `~/.cache/youtube-tldw/transcripts/<video_id>.json` (`txcache.CACHE_DIR`, `txcache.py:21`; `_path`, `txcache.py:30-33`).
- **Writer:** `server._cache_put` (`server.py:309-317`). It runs after every summary that had cues (`server.py:1313-1316`), when a transcription job finishes (`server.py:261`), and when `/ask/stream` had to fetch a transcript (`server.py:1816`). A CLI summarize run (`tldw <url>`) never reads or writes it; only `tldw cache` touches it from the CLI.
- **Readers:** `server._cache_get` and `_cache_source` (`server.py:320-357`), which back `/summarize`, `/summarize/stream`, `/summarize/cached`, `/segments/stream` and `/ask/stream`.
- **Key:** the video id alone. It is validated as `^[A-Za-z0-9_-]{11}$` (`urls.py:19`) before it gets here, which is what keeps the name inside the directory (`txcache.py:31-32`). The language isn't part of the key (see Recorded facts).
- **Shape** (`txcache.put`, `txcache.py:75-82`):

  ```json
  {
    "saved_at": 1762905600.0,
    "meta": {"video_id": "AAAAAAAAAAA", "title": "An invented talk",
             "channel": "Example Channel", "duration_ms": 754000},
    "cues": [[0, 4200, "Welcome back to the channel."],
             [4200, 9100, "Today we look at three ideas."]],
    "source": "captions"
  }
  ```

  `cues` holds `[start_ms, end_ms, text]` triples. `source` is `"captions"` or `"audio:<whisper model>"`, such as `"audio:small"` (`txcache.py:36-46`). The caption-track maps are left out on purpose, because they were most of the file (`txcache.py:77-79`). Meta from a page-supplied transcript is truncated to 300 characters (title) and 200 (channel) (`server.py:827-834`), and is stored with `source: "captions"` like any other caption track (`server.py:1311-1316`).
- **Read rules:** an entry older than its TTL is a miss. So is a missing, truncated or older-shape file (`txcache.py:57-58`). `source` is optional and defaults to `captions`, and a non-string value also reads as `captions` (`txcache.py:52-54`). `meta.subtitles` and `meta.auto_captions` are optional (`txcache.py:66-68`).
- **TTL:** 14 days from `saved_at` (`TTL_SECONDS`, `txcache.py:26`).
- **Cap:** 500 files. After every write, the oldest files by mtime are deleted (`txcache.py:27`, `txcache.py:95-99`). Reads don't update mtime, so this is "oldest written", not "least recently used".
- **Cleanup:** expired files stay on disk until `tldw cache clean` or the cap removes them (`cli.py:396-398`, `hygiene.py:195-219`). `tldw cache clean` skips a file it can't read or that has no `saved_at` (`hygiene.py:197`, `hygiene.py:215-216`); only the cap or an overwrite removes one.
- **Consumers and blast radius:** every transcript-needing route. A miss costs a yt-dlp fetch, which is the path YouTube rate-limits (`txcache.py:3-6`). For an `audio:` entry, a miss costs minutes of Whisper CPU (`txcache.py:42-45`). The disk copy is also the fallback for the 4-hour in-memory copy (`server.py:337-342`).

### 2. Summary cache

- **Path:** `~/.cache/youtube-tldw/summaries/<video_id>-<16 hex>.json` (`txcache.SUMMARY_DIR`, `txcache.py:22`).
- **Writer:** `_run_stream`, after a successful streamed summary (`server.py:1325-1327`). The buffered `/summarize` route doesn't write it, and its response carries no `stats` (`server.py:1172-1188`, the plain `_to_payload` at `server.py:1188`).
- **Readers:** `_run_stream`'s cache check, skipped when the request sets `refresh: true` (`server.py:1217`, `server.py:1224-1244`), and `/summarize/cached` (`server.py:1359`).
- **Key** (`_summary_key`, `server.py:748-770`). This doc owns the key composition and the TTL and cap values; `contracts.md` cites them from here. The file name is the video id, then `-`, then the first 16 hex digits of the sha256 of `video_id|backend_fingerprint|ratio-or-"auto"|PROMPT_FINGERPRINT|source`:
  - `backend_fingerprint()` is the first 12 hex digits of the sha256 of the resolved backend argv, model included (`claude_client.py:227-235`);
  - `PROMPT_FINGERPRINT` is the first 12 hex digits of the sha256 of exactly four strings: `_TEXT_BODY`, `_TEXT_PROMPT`, `_TEXT_PROMPT_STREAM` and `_TEXT_REDUCE_PROMPT` (`_prompt_fingerprint`, `summarize.py:140-148`, the list at `summarize.py:144`);
  - `source` is the transcript source this request would use (`_effective_source`, `server.py:878-890`).

  **Not in the key**, though each shapes the summary: `_ratio_clause` (`summarize.py:151`, formatted into every summary prompt at `summarize.py:320`, `:334`, `:349`, `:355`), `_metadata_header` (`summarize.py:241-242`, prepended at `summarize.py:316`), and `SINGLE_PASS_CHARS` (`summarize.py:17`), which decides single-pass against map-reduce (`summarize.py:318`, `:338`). The segment key's fingerprint does include `_ratio_clause` (`core.py:38-41`). See Recorded facts.
- **Shape:** top-level `saved_at` and `payload` (`txcache.py:125`). The payload is the `_to_payload` dict (`server.py:1904-1921`):

  ```json
  {
    "saved_at": 1762905600.0,
    "payload": {
      "transcript_source": "captions",
      "video_id": "AAAAAAAAAAA",
      "title": "An invented talk",
      "channel": "Example Channel",
      "source_url": "https://www.youtube.com/watch?v=AAAAAAAAAAA",
      "original_length": "12m34s",
      "length_label": "2m",
      "key_points": ["First invented point.", "Second invented point."],
      "summary_md": "## Overview\n\nAn invented paragraph.",
      "rationale": "An invented sentence about the length."
    }
  }
  ```

  `original_length` is `timing.format_length` of the duration (`12m34s` style, `timing.py:68-79`), and `length_label` is the reading time from `timing.read_time_label` (`2m` style, `timing.py:82-85`).
- **Read rules:** a miss if the file is expired, unreadable, or `payload` isn't a dict (`txcache.py:108-117`). A hit is sent to the extension as `{"type": "result", **payload, "cached": true, "stats": …}` (`server.py:1233-1234`, `server.py:1368-1369`). The `meta` event built from a hit uses `.get` with defaults (`server.py:1229-1232`).
- **TTL / cap / cleanup:** 14 days (`TTL_SECONDS`, `txcache.py:26`); 500 files (`MAX_ENTRIES`, `txcache.py:27`), pruned separately from segments (`txcache.py:23-25`); `tldw cache clean` removes expired files. An unreadable file, or one with no `saved_at`, is skipped by `tldw cache clean` (`hygiene.py:197`, `hygiene.py:215-216`), so only the cap or an overwrite of the same key removes it. The same holds for stores 1 and 3.
- **Consumers and blast radius:** the extension's "already summarized" path, so no model call. Every payload key reaches the extension unchanged, so the payload shape is part of the response contract (Backend's `_to_payload` entry in `contracts.md`).

### 3. Key-moment (segment) cache

- **Path:** `~/.cache/youtube-tldw/segments/<video_id>-<16 hex>.json` (`txcache.SEGMENT_DIR`, `txcache.py:25`).
- **Writers:** the background prefetch (`server.py:511`) and a live `/segments/stream` selection (`server.py:1627-1628`), both through `_seg_cache_put` (`server.py:401-405`).
- **Reader:** `_seg_cache_get` (`server.py:408-419`), used by `/segments/stream` and by the prefetch's "already made?" check (`server.py:455`).
- **Key:** the `_seg_key` tuple (`server.py:376-394`), made up of:
  - the video id;
  - the segment ratio rounded to 3 places, or `None`;
  - `core.SEGMENT_FINGERPRINT`, which covers the selection prompts, the listing format, the ratio and length clauses, and `MIN_CLIP_MS` / `BOUNDARY_PAD_MS` (`core.py:26-45`), but not `_metadata_header` (prepended to the listing, `summarize.py:374`) or `SINGLE_PASS_CHARS` (single pass against chunked, `summarize.py:420`, `:428`);
  - `backend_fingerprint()`;
  - the transcript source.

  The file name is the video id, then `-`, then the first 16 hex digits of the sha256 of the tuple's `repr()` (`_seg_file`, `server.py:397-398`). A request with `max_length` gets no key, so it is neither served from the cache nor written to it (`server.py:1552-1558`).
- **Shape** (`txcache.put_segments`, `txcache.py:154-161`):

  ```json
  {
    "saved_at": 1762905600.0,
    "meta": {"video_id": "AAAAAAAAAAA", "title": "An invented talk",
             "channel": "Example Channel", "duration_ms": 754000},
    "segments": [{"start": 12.4, "end": 41.0, "label": "0:12"},
                 {"start": 300.2, "end": 344.9, "label": "5:00"}]
  }
  ```

  `start` and `end` are seconds rounded to 2 places, and `label` is `format_clock` of the display start (`core.py:148-150`).
- **Read rules:** a miss if expired or unreadable, or if `segments` isn't a list (`txcache.py:140-151`). The list items are not validated on read.
- **TTL / cap / cleanup:** 14 days (`TTL_SECONDS`, `txcache.py:26`); 500 files (`MAX_ENTRIES`, `txcache.py:27`); `tldw cache clean` removes expired files, with the same skip of unreadable files as store 2.
- **Consumers and blast radius:** "Play key moments". A miss costs a model call, and selections are most of the spend (`usage.py:59-62`).

### 4. Usage log

- **Path:** `~/.config/youtube-tldw/usage.jsonl` (`usage.USAGE_FILE`, `usage.py:26`).
- **Writers:** `usage._append` (`usage.py:184-189`). It is reached three ways: `record` for every model call on the stock `claude` backend (`claude_client.py:66-76`, `claude_client.py:405-408`); `note_served` when a selection reaches the panel (`server.py:1568`, `server.py:1586`, `server.py:1646`); and `note_video` after a summary (`server.py:1180`, `server.py:1268`). Custom backends report no usage, so they write no call rows (`claude_client.py:66-70`).
- **Readers:** `read_rows` and `stats` (`usage.py:208-245`), `tldw usage` (`cli.py:457-573`), and the `stats` field of every streamed or probed summary result (`server.py:1234`, `server.py:1328`, `server.py:1369`; the buffered `/summarize` has none, `server.py:1188`).
- **Key:** none. One row is appended per event. `stats` de-duplicates `video` rows by `video_id`, so the latest row for a video wins (`usage.py:208-224`).
- **Shapes,** told apart by `kind`:

  Model call (`usage.py:146-152`, fields from `Usage`, `usage.py:31-43`):

  ```json
  {"ts": 1762905600.0, "kind": "summarize", "video_id": "AAAAAAAAAAA",
   "step": "summarize", "model": "claude-example-model", "input_tokens": 1200,
   "cache_creation_tokens": 0, "cache_read_tokens": 0, "output_tokens": 640,
   "cost_usd": 0.0123, "duration_ms": 18000, "session_id": "<session-id>",
   "resumed": false}
  ```

  - `kind` is the interaction: `summarize`, `segments` or `ask` (`server.py:501`, `server.py:1175`, `server.py:1263`, `server.py:1619`, `server.py:1792`). It is `""` for a call made outside any interaction, which is every CLI call, since the CLI opens none.
  - `trigger` (`prefetch` or `request`) appears only on segment rows (`usage.py:149-150`).
  - `step` is the caller's label: `summarize`, `summarize-map`, `segments`, `segments-chunk` or `ask`. A buffered JSON repair attempt adds `-retry` (`claude_client.py:638`).

  Finished summary (`usage.py:201-203`):

  ```json
  {"ts": 1762905600.0, "kind": "video", "step": "video", "video_id": "AAAAAAAAAAA",
   "video_ms": 754000, "read_ms": 96000, "words": 320}
  ```

  Selection served (`usage.py:166-167`; `how` is `cache`, `prefetch` or `live`):

  ```json
  {"ts": 1762905600.0, "kind": "segments_served", "video_id": "AAAAAAAAAAA", "how": "cache"}
  ```
- **Read rules:** blank and non-JSON lines, and non-object rows, are skipped (`usage.py:227-245`). Every numeric field is read with `.get(...) or 0`, which tolerates a missing or `null` field but **not a wrong type**: `stats()` passes `video_ms` and `read_ms` through `int()` (`usage.py:218-219`), so a non-numeric string (or a list or object) in one `video` row raises. Nothing catches it at the three `stats()` call sites (`server.py:1234`, `server.py:1328`, `server.py:1369`), so that one row breaks every streamed and probed summary result until it is removed. See Recorded facts.
- **TTL / cap / cleanup:** none. It grows without bound. `tldw cache` neither reports nor cleans it (`cli.py:390-392`).
- **Concurrency:** appends from one process are serialized by a lock (`usage.py:186`). Nothing serializes appends from two processes (a CLI run during `tldw serve`, or two servers); a garbled line would be skipped on read.
- **Consumers and blast radius:** `tldw usage`'s cost tables, and the "hours you didn't have to watch" figure the panel shows. `tldw usage` counts any row whose `kind` isn't `video` or `segments_served` as a model call (`cli.py:488`).

### 5. Config

- **Path:** `~/.config/youtube-tldw/config.json` (`config.CONFIG_FILE`, `config.py:12`).
- **Writer:** `tldw config set` / `unset` (`cli.py:349-364`), through `set_value` / `unset` (`config.py:27-41`): load, mutate, then `write_text(json.dumps(cfg, indent=2) + "\n")`.
- **Readers:** `config.get`, which re-reads the file on every call (`config.py:15-24`). A running server therefore sees a change immediately.
- **Shape:** one flat object. Every value written by the CLI is a string (`cli.py:351`). The keys tldw reads are `output_dir`, `whisper_model`, `whisper`, `whisper_max_minutes`, `llm_cmd` and `ytdlp_cookies`; each one's meaning and default is in `contracts.md` → Config keys. Any other key can be stored, and is never read.

  ```json
  {
    "output_dir": "~/Media/tldw-example",
    "whisper_model": "small",
    "whisper": "ask"
  }
  ```

  (A stored `output_dir` is an absolute path, because the CLI resolves it first (`cli.py:356-357`). It is shown with `~` here only to keep real paths out of this doc.)
- **Read rules:** a missing file or invalid JSON reads as `{}`, and so does a JSON value that isn't an object (`config.py:15-20`). Other `OSError`s, such as a permission error, are not caught. **The file is tolerated; its values' types are not.** A hand-edited file can hold a number, list or object where the CLI writes a string, and the readers differ:
  - `llm_cmd` and `ytdlp_cookies` are `.strip()`ped (`claude_client.py:210`, `metadata.py:53-54`), so any truthy non-string raises `AttributeError`, on every model call (when `TLDW_LLM_CMD` is unset) or every yt-dlp metadata and subtitle call (when `TLDW_YTDLP_COOKIES` is unset);
  - `whisper_model` is tested with `saved in VALID_MODELS` (`whisper_transcribe.py:78-79`), a `frozenset` (`whisper_transcribe.py:25`), so a non-empty list or object raises `TypeError`; a number falls back to `small`;
  - `output_dir` goes to `Path(saved)` (`cli.py:29-31`), so a truthy non-string raises `TypeError`;
  - `whisper` is checked with `isinstance(pinned, str)` (`server.py:152-153`) and is ignored when it isn't one;
  - `whisper_max_minutes` falls back to the default for a bool or a non-integer (`server.py:125-132`), but a value of 0 or below is clamped to **1 minute**, not refused (`max(1, minutes)`, `server.py:133`).

  See Recorded facts.
- **Not atomic.** See Recorded facts.
- **Mode:** the process umask's mode; tldw doesn't restrict it.

### 6. Token

- **Path:** `~/.config/youtube-tldw/token` (`server.TOKEN_FILE`, `server.py:689`).
- **Writer:** `load_or_create_token`, when `tldw serve` starts without `--token` or `TLDW_TOKEN` and the file is missing or empty (`server.py:692-708`). The token is `secrets.token_urlsafe(32)` (43 characters), written with no trailing newline. `chmod 0600` runs after the write (`server.py:706-707`).
- **Reader:** the same function on later starts; the value is `strip()`ped (`server.py:700-703`).
- **Shape:** a single line, written here as `<your-token>`.
- **Lifecycle:** tldw never rotates it. A new one is generated only when the file is missing or empty, and every installed extension then needs the new value (it holds its copy in `chrome.storage.local`).

### 7. Voices

- **Path:** `~/.cache/youtube-tldw/voices/` (`audio.VOICE_DIR`, `audio.py:61`).
- **Files:**
  - `<model>.onnx` and `<model>.onnx.json`, one pair per voice, downloaded on first use by `python -m piper.download_voices <model> --data-dir <VOICE_DIR>` (`audio.py:230-264`). `<model>` is always a value from the 13-entry `VOICES` allowlist, such as `en_US-amy-medium` (`audio.py:44-58`). Partial `<model>.onnx*` files are deleted between retries (`audio.py:255-256`).
  - `.spoken-tracking-since`, an empty marker. Its mtime is when voice-use tracking began. `ensure_voice_tracking` runs at every `tldw serve` start and on first use (`hygiene.py:33`, `hygiene.py:138-145`, `server.py:111`), but creates the marker only if it is missing and never touches an existing one (`hygiene.py:142-143`), so its mtime stays the first-ever creation time.
- **mtimes are data.** Speaking with a voice sets its `.onnx` mtime to now (`hygiene.mark_voice_spoken`, `hygiene.py:148-154`, called from `audio.py:300-303`). Previews pass `record_use=False` and don't (`server.py:1885-1887`). Hygiene reads these mtimes as "last spoken".
- **Cleanup:**
  - At every `tldw serve` start, any `<stem>.onnx` or `<stem>.onnx.json` whose stem isn't in the allowlist is deleted (`server.py:104-107`, `hygiene.py:124-135`).
  - `tldw cache clean` also deletes **idle** voices (`hygiene.py:157-190`): spoken with more than 30 days ago, only once the marker itself is at least 30 days old, never the default voice (`en_US-amy-medium`, `cli.py:404`), and never the most recently spoken one.
- **Consumers:** `/speak`, `/speak/stream`, `/preview`, CLI `--render-audio`.

### 8. Whisper models

- **Path:** `~/.cache/youtube-tldw/whisper/` (`whisper_transcribe._CACHE_DIR`, `whisper_transcribe.py:23`).
- **Writer:** faster-whisper's `WhisperModel(..., download_root=…)` (`whisper_transcribe.py:199-210`). The internal layout is the library's; `UNKNOWN — needs human confirmation` beyond that, since tldw never parses it.
- **Size:** 75MB to 3.1GB per model (`whisper_transcribe.py:32-38`). Changing `whisper_model` downloads another model, and the old one stays.
- **Cleanup:** never by tldw (`hygiene.py:11-14`, `cli.py:437-439`). `tldw cache` reports its size and the configured model (`cli.py:421-432`).

### 9. Claude working dir

- **Path:** `~/.cache/youtube-tldw/claude/` (`claude_client.CLAUDE_DIR`, `claude_client.py:119`).
- **Writer:** `_claude_process_opts` creates it with mode 0700 before every stock `claude` call and passes it as `cwd` (`claude_client.py:143-152`). If it can't be created, the call runs from the server's own working directory (`claude_client.py:150-151`). Custom backends never use it (`claude_client.py:155-159`).
- **Contents:** whether the CLI writes anything into its working directory is `UNKNOWN — needs human confirmation`. The directory exists so that the CLI's session store (next item) files tldw's sessions under a project of their own (`claude_client.py:113-118`).
- **Cleanup:** none. `tldw cache` doesn't list it.

### 10. Claude CLI sessions

- **Path:** `~/.claude/projects/<encoded tldw claude dir>/`. This is the Claude CLI's per-project session store for its working directory, item 9.
- **Writer:** the `claude` CLI, and only for calls that persist. Only Q&A persists (`stream_text` passes `persist=True`, `claude_client.py:576-577`). Every other call passes `--no-session-persistence` (`claude_client.py:189-202`), and a test guards that (`tests/test_claude_client.py:186`).
- **Contents:** owned by the CLI. The shape is not tldw's contract, and is `UNKNOWN — needs human confirmation`. From what tldw sends, a session holds the Q&A prompt, the video's title, channel and full transcript, the user's questions, and the answers (`ask.py:79-90`).
- **References into it:**
  - the session id, kept in memory per video for 4 hours (`_ask_sessions`, `server.py:422-431`);
  - the `session_id` field of `usage.jsonl` call rows (`usage.py:42`, `usage.py:100`).

  Nothing enforces these references. A stale id falls back to a full call that re-sends the transcript (`ask.py:133-139`).
- **Cleanup:** never by tldw. The CLI's own retention policy is `UNKNOWN — needs human confirmation`.

### 11. Output files

- **Path:** the base is `--output-dir` > `TLDW_OUTPUT_DIR` > config `output_dir` > `~/Downloads/youtube-tldw/tldws` (`cli.py:20-32`, `cli.py:82`). Files go in `text/`, `video/` or `audio/` (`naming.py:58-71`).
- **Writer:** the CLI only (`cli.py:153-273`); the server writes no files here. `.md` files use `write_text` (`textmode.py:38-39`); `.mp4` and `.mp3` files are `shutil.move`d out of the run's temp dir (`cli.py:189`, `cli.py:257`, `cli.py:265`).
- **Name:** `{channel} - {title} - tl;dw - {length}.{ext}`. Both fields are sanitized and byte-capped (60 bytes for the channel, 120 for the title), a containment check runs, and an existing name gets ` (2)`, ` (3)` (`naming.py:27-84`). The `.md` file's content comes from `textmode.render_markdown` (`textmode.py:12-27`).
- **Cleanup:** never by tldw; these are the user's files.

### 12. Temp dirs and `*.tmp`

| What | Prefix / name | Created | Removed | Swept if left behind |
|---|---|---|---|---|
| CLI run | `youtube-tldw-` | `cli.py:288` | `finally`, `cli.py:294-295` | No |
| Transcript fetch | `youtube-tldw-tx-` | `core.py:65` | `finally`, `core.py:86-87` | No |
| Transcription audio | `youtube-tldw-audio-` (`hygiene.AUDIO_TMP_PREFIX`) | `server.py:236` | `finally`, `server.py:282` | Yes, by age: at least 6 hours (`server.py:77-86`), at serve start and by `tldw cache clean` (`hygiene.py:83-111`) |
| Word-timing audio (T-020) | `youtube-tldw-audio-` (the same prefix, on purpose) | `server.py` `_run_align` | `finally`, with the window wavs inside it | Yes, by the same age rule |
| Cache write in flight | `<name>.tmp` in each cache dir | `txcache.py:84`, `txcache.py:124`, `txcache.py:158` | `replace` onto `.json` | Yes, by `tldw cache clean` if older than 1 hour (`hygiene.py:28`, `hygiene.py:208-211`) |

The temp root is `tempfile.gettempdir()` (`hygiene.py:107`).

### 13. Alignment cache (T-020)

**Path:** `~/.cache/youtube-tldw/align/` (`align.ALIGN_DIR`, `align.py:28`). **Written by** `align.put_window`, **read by** `align.get_window`, both from `_run_align`. One file per time window of one video, holding the words Whisper heard in it. Used by "Cover listed words" to narrow a caption-line hit to the word.

- **File name:** `<video_id>-<first 16 hex of sha256(key)>.json`, where the key is built by `align.cache_key`:

  `video_id|start_ms|end_ms|whisper_model|lang=auto|ALIGN_FINGERPRINT`

  - `whisper_model` is `whisper_transcribe.resolve_model()`.
  - `lang=auto` is fixed: alignment always auto-detects (`language=None`). It is a term so that a future language hint can never share entries with it.
  - `ALIGN_FINGERPRINT` is the first 12 hex of the sha256 of a sorted JSON object of: the transcribe keyword arguments (`TRANSCRIBE_KWARGS`: `beam_size`, `word_timestamps`, `vad_filter`, `condition_on_previous_text`, `language`); `MODEL_COMPUTE_TYPE` and `MODEL_DEVICE`; the ffmpeg cut's shape (`_CUT_SHAPE`, which a test pins to `cut_argv` with its values removed); `MAX_WORDS_PER_WINDOW`, `MAX_WORD_CHARS` and `BAD_TIMESTAMP_SLACK_MS`; the installed `faster-whisper` and `ctranslate2` versions; and **`ALIGN_SHAPE`**, the payload shape's own version (`align.py:143-169`). `cpu_threads` is left out: it changes speed, not output. A test changes each input and checks the key changes.
  - The fingerprint is computed **lazily** and memoised, never at import. If either library version can't be read, it is `None`: nothing is looked up, and the route answers `503` `whisper_missing`. **A running server keeps its memoised fingerprint**, so after a library upgrade restart `tldw serve` before the new version changes the key.
- **The word list is not in the key, on purpose.** The cache holds "what Whisper heard", which the list doesn't shape: matching happens in the browser. So editing the list never re-transcribes a window it already has. It can still cause new windows to be timed, because windows follow which caption lines are hits.
- **Shape:**

  ```json
  {"saved_at": 1763000000.0, "video_id": "dQw4w9WgXcQ", "start_ms": 9000,
   "end_ms": 15500, "words": [[10300, 10600, "blorp"], [10650, 10900, "zarf"]]}
  ```

  `words` are `[s, e, w]` in absolute media milliseconds. On the wire, `/align/stream` sends each as `{"s": …, "e": …, "w": …}`; `send_window` converts (`server.py:1959-1963`). The example's words are made up.
- **Validated on read** (`get_window`, `_valid_words`): older than `TTL_SECONDS` (14 days); a `video_id`, `start_ms` or `end_ms` that differs from the request (a copied file or a hash collision), or one that isn't an `int`; more than 200 words; a word that isn't three items, with `int` (not `bool`) times and `start_ms ≤ s ≤ e ≤ end_ms`; text empty or over 64 characters. Any of these, and any read error, is a **miss**, never an error.
- **On the way in**, `words_from_raw` drops a word with `e < s`, or more than 1 s outside the window, and clamps the rest; then caps at 200 words and 64 characters.
- **Writes:** best effort, `.tmp` then `replace` (`put_window`). A window that failed is never written.
- **Cap and TTL:** its own cap, `ALIGN_MAX_ENTRIES` (5,000 files), enforced by `txcache._prune(ALIGN_DIR, cap=…)` **once per request**, in `_run_align`'s `finally`. Requests that never take `_align_lock` (all cached, or refused early) don't prune; `tldw cache clean` still removes expired entries. `_prune`'s default cap for the other stores is unchanged (tested).
- **Class:** personal viewing history, like store 1: text derived from public video audio, which also shows that the video was watched. **It also narrows what the list holds:** the list itself is never stored, but each window is stored because a caption line around it held a listed word, so whoever can read this directory can narrow each window to a few likely words. It keeps the process umask's mode, like stores 1–3. About 1–3 KB per window.
- **The only schema version in any store.** `ALIGN_SHAPE` (in the fingerprint) versions this payload. A shape change bumps it, and every old entry misses.

### `chrome.storage.local` keys added by T-020

- `muteWords` (string): the "Words to cover" list, one normalised entry per line. **Absent means the shipped default** (`TLDW_MUTE_DEFAULTS` in `extension/mute_words.js`), and Options removes the key whenever the box equals the default, so a change to the default reaches the user. `""` means off. Class: personal (the user's own list). It is **never sent** to the worker or the server, and appears in no log or cache.
- `muteSound` (string): `bleep` (default), `mute` or `moo`. Not personal.

Their full contracts are in `contracts.md` → `chrome.storage.local` keys.

### In-memory stores

These are not persisted and vanish on restart. The full list, with keys, TTLs and caps, is in `contracts.md` → Data stores → In-memory stores. The server keeps:

- `_transcript_cache` and `_seg_cache`, write-through copies of stores 1 and 3: 4 hours, 64 entries each (`server.py:35-39`);
- `_ask_sessions`: video id → Claude session id, 4 hours;
- `_captionless_meta`: 30 minutes;
- `_tx_jobs`, `_seg_prefetch_events` and `_seg_progress`, which live only while the work runs;
- `_preview_cache`: voice model → mp3, bounded by the allowlist.

In `audio.py` there are `_voice_cache` (at most 2 loaded voices) and `_piper_state`. The extension's background worker keeps three uncapped `Map`s (`background.js:25-26`, `background.js:523`).

### Hugging Face hub cache (read only)

`tldw cache` lists `models--Systran--faster-whisper-*` directories under `$HF_HUB_CACHE`, else `$HF_HOME/hub`, else `~/.cache/huggingface/hub`, with their sizes and an `rm -rf` hint, and never deletes them (`hygiene.py:224-237`, `cli.py:443-454`). They aren't tldw's: it keeps its own models in store 8.

### Third-party stores tldw's subprocesses use

tldw neither reads nor cleans these, and `tldw cache` doesn't report them (`cli.py:390-392`). They are listed because running tldw causes them to be written or read.

- **yt-dlp's own cache.** tldw runs `yt-dlp` at `metadata.py:82-83` (metadata), `metadata.py:212-215` (subtitles), `metadata.py:245` (CLI video download) and `whisper_transcribe.py:125` (audio for Whisper), and never passes `--no-cache-dir` (grep over `src/youtube_tldw/`: no hits). Whatever yt-dlp caches by default is therefore written on every fetch. Its location, layout, contents and retention are `UNKNOWN — needs human confirmation`.
- **The browser's cookie database.** When `TLDW_YTDLP_COOKIES` or config `ytdlp_cookies` names a browser in `COOKIE_BROWSERS`, the metadata and subtitle calls add `--cookies-from-browser <name>` (`metadata.py:48-55`, used at `metadata.py:83` and `metadata.py:215`), so `yt-dlp` reads that browser's cookies. The video and Whisper-audio downloads don't pass it. The database's location and layout, and whether yt-dlp keeps a copy, are `UNKNOWN — needs human confirmation`. Class: **secret** (it holds the user's signed-in browser sessions), though tldw itself never sees the values.

## Tables, indexes, foreign keys, partitioning, replication

N/A — file-backed, with no database engine. In their place:

- **Lookup** is by file name, built from the key. There is no scan on any request path: every cache read opens exactly one file (`txcache.py:48`, `txcache.py:111`, `txcache.py:143`). Only pruning and `tldw cache` list a directory.
- **References between stores** are logical and not enforced:
  - summary and segment file names begin with the video id of a transcript entry, but the files don't depend on that entry existing;
  - a segment key includes the transcript's `source`;
  - `usage.jsonl` rows name `video_id` and `session_id`;
  - `_ask_sessions` points at a CLI session file.

  Every dangling reference is tolerated, and the result is a miss or a fallback, never an error.
- **No partitioning, sharding or replication.** Each user runs their own server against their own home directory. The only "partition" is one directory per cache area, so that summaries and selections are capped separately (`txcache.py:23-25`).

## What `tldw cache` covers

`tldw cache` reports six areas (`cli.py:390-392`): transcripts, summaries, key moments, word timings (store 13, T-020), voices and Whisper models. The areas `tldw cache clean` expires are chosen **by name** (`cli.py:395`, used at `cli.py:399`), not by position, so adding an area can't shift which ones are cleaned (tested). It also reports Hugging Face's faster-whisper models, report-only. `tldw cache clean` (`cli.py:394-417`):

| Store | Reported | Cleaned by `tldw cache clean` | Cleaned at `tldw serve` start |
|---|---|---|---|
| 1 Transcripts | Yes (size, count) | Expired entries and stray `.tmp` | No |
| 2 Summaries | Yes | Expired entries and stray `.tmp` | No |
| 3 Key moments | Yes | Expired entries and stray `.tmp` | No |
| 7 Voices | Yes (size, count) | Unlisted and idle voice files | Unlisted voice files only |
| 8 Whisper models | Yes (size, configured model) | Never | Never |
| HF hub cache | Yes, if any found | Never (prints `rm -rf` hints) | Never |
| 12 `youtube-tldw-audio-*` temp dirs | No | Older than `_stale_audio_age()` | Older than `_stale_audio_age()` |
| 13 Word timings | Yes ("word timings", size, count) | Expired entries and stray `.tmp` | No |
| 4 `usage.jsonl` | No | No | No |
| 5 `config.json` | No (that's `tldw config get`) | No | No |
| 6 Token | No | No | No |
| 9 Claude working dir | No | No | No |
| 10 Claude CLI sessions | No | No | No |
| 11 Output files | No | No | No |
| 12 `youtube-tldw-` and `youtube-tldw-tx-` temp dirs | No | No | No |

`--dry-run` lists what would be removed and removes nothing (`cli.py:386-387`, `cli.py:408`).

## Personal data and protection

The Architect owns classification and encryption decisions (`.claude/agents/data-engineer.md`, "Conflict defaults"), and has confirmed the classes below in `architecture.md` § 8 ("Classification"), with two refinements that are folded into the table: `chrome.storage.local` is **Secret**, and the extension worker's in-memory summary and audio caches are personal viewing history. tldw serves no regulated domain (T-001 plan, "What tldw is").

- **Encryption at rest:** none, for every store. `src/` has no encryption code (grep for `encrypt`, `cipher`, `keyring`, `keychain`: no hits). Protection is the operating system's file permissions on the user's home directory.
- **Owner-only modes:** set on the token (0600), `usage.jsonl` (0600) and `CLAUDE_DIR` (0700). Each is applied **after** the file or directory exists, and is a no-op on Windows (`config.py:44-56`). Every other store keeps the process umask's mode.
- **Access logging:** N/A. tldw writes no access log for any store. Its server log goes to stdout and stderr only, never to a file. The stdlib request line (the default `log_message` is kept, `server.py:962-963`) records the method, the path **with its query string**, and the status; it carries no body and no token. The comment there says "no query", which is stale (Recorded facts). No `fetch(` in `extension/` sends a query string (`background.js:182`, `:223`, `:305`, `:340`, `:459`, `:540`, `:585`; `options.js:53`, `:70`, `:97`), so today the line holds only route paths. tldw's own progress lines do print video titles (`server.py:1310`).
- **Masking:** none. The usage log keeps only video ids, not titles.

| Store | Class | What it reveals | Owner-only? |
|---|---|---|---|
| 6 Token | **Secret**: a credential | Lets any local process call the server | Yes, 0600 (after the write) |
| 4 `usage.jsonl` | Personal: activity history | Which videos were summarized, played or asked about, and when; cost; Claude session ids | Yes, 0600 (after each append) |
| 10 Claude CLI sessions | Personal: user-authored content | The user's own questions and the answers, plus the transcript | The CLI's own modes, `UNKNOWN — needs human confirmation`; the working dir above it is 0700 |
| 1 Transcripts | Personal: viewing history; the body is third-party content | Which videos were opened; their text | No |
| 2 Summaries | Personal: viewing history | The same, as summaries | No |
| 3 Key moments | Personal: viewing history | Which videos, and their timestamps | No |
| 13 Alignment cache | Personal: viewing history; the words are third-party content | Which videos were watched with "Cover listed words" on, and the words around each hit (not the user's list) | No |
| 11 Output files | Personal: viewing history, user-owned files | Titles and channels in file names; summaries and media | No |
| 12 Temp dirs | Personal, transient | Downloaded audio, video and subtitles of one video | No |
| 5 `config.json` | Operator config; possibly personal | `output_dir` is an absolute path that can include the OS account name; `llm_cmd` is free text the operator typed, and whether anyone puts a credential in it is `UNKNOWN — needs human confirmation`; `ytdlp_cookies` names a browser, not cookies | No |
| 7 Voices, 8 Whisper models, HF cache | Not personal: public model files | The marker's and `.onnx` files' mtimes reveal when speech was used | No |
| Server in-memory stores | Same classes as the disk stores they mirror. Q&A text is never in server memory beyond the request: the server keeps only the session id (`_ask_sessions`, `server.py:422-431`; the browser sends the conversation, `ask.py:3-4`). The questions and answers do persist, on disk, in the CLI session store (store 10). | — | Process memory |
| Extension worker's in-memory `cache` and `audioCache` | Personal: viewing history (Architect's refinement) | Summaries and spoken audio, keyed by video id (`background.js:25-26`); `previewCache` is keyed by voice and is not personal (`background.js:523`) | Process memory |
| `chrome.storage.local` | **Secret** (Architect's refinement): holds the token in plain text; see `contracts.md` | The token, which lets the holder call the server | The browser profile |

## No version markers, and how to change a format safely

**Only store 13 carries a schema version**, and only inside its key: `ALIGN_SHAPE` is part of `ALIGN_FINGERPRINT` (T-020). No other store has a `version` or `schema` field in any record (`txcache.py`, `usage.py`, `config.py`), and no migration tooling, migrations directory or backfill job. Compatibility instead comes from four mechanisms that exist today. A format change must use one of them. A change that needs something else, such as rewriting files in place, needs its own reviewed plan, because nothing in the code supports it.

**General rules, all grounded in how the readers work:**
- **Cache readers must stay tolerant.** Every cache read is wrapped so that any exception class it can raise on bad data is a miss (`txcache.py:57`, `txcache.py:116`, `txcache.py:150`). A new required field must not raise anything outside those `except` tuples (`OSError`, `ValueError`, `KeyError`, `TypeError`, plus `IndexError` for transcripts). The usage log and config readers are tolerant of bad lines and bad files, **not** of a wrong value type (store 4 and store 5 read rules), so the next rule matters there most.
- **Never change a field's type.** Not in a cache record, a `usage.jsonl` row or a config key. `stats()` raises on a non-numeric `video_ms` or `read_ms` (`usage.py:218-219`), and the config readers assume strings (store 5). Readers would treat an old row's or file's value with the new type's operations.
- **Writers stay best effort**, inside the broad `except Exception` (`txcache.py:88-92`).
- **`saved_at` stays top-level** in every cache file: both the getters and `tldw cache clean` read it there (`hygiene.py:214`).
- **Rollback.** An older version reads files written by a newer one through the same tolerant paths. Where its fingerprints differ, it computes different file names and simply misses. Whether a specific older release starts cleanly on today's stores is what DevOps's rollback test in `deployment.md` settles; this doc doesn't claim it.
- **Deploy sequencing:** one process writes each store, and nothing is shared across machines, so there is no multi-node ordering. Restart `tldw serve` after an upgrade. The extension reads only payloads, so a payload change has to be tolerated by both the old and the new extension (see Summaries below).
- **Backfill:** none, ever. Caches refill on demand; history in `usage.jsonl` is never rewritten.

**Per store:**

1. **Summaries and key moments: change the key, never the file.** Anything that changes what would be produced must change the key:
   - an edit to `_TEXT_BODY`, `_TEXT_PROMPT`, `_TEXT_PROMPT_STREAM` or `_TEXT_REDUCE_PROMPT` already does, through `PROMPT_FINGERPRINT` (`summarize.py:144`). Those four are all it covers. An edit to `_ratio_clause`, `_metadata_header` or `SINGLE_PASS_CHARS` changes summaries **without** changing the summary key, so it serves stale summaries for up to 14 days unless the same PR adds that input to `_prompt_fingerprint` (the summary key's composition, store 2);
   - a selection-shaping constant does, through `SEGMENT_FINGERPRINT` (`core.py:26-45`), which includes `_ratio_clause` (`core.py:38-41`) but not `_metadata_header` or `SINGLE_PASS_CHARS` (store 3);
   - the backend or model does, through `backend_fingerprint()`;
   - a new input must be added to `_summary_key` or `_seg_key`.

   Old files then become misses and age out in 14 days, or go at once with `tldw cache clean` once expired. The cost is one model call per video, recorded in the README for selections ("re-made once per video").

   **Gap:** no fingerprint covers the `_to_payload` shape, so adding a payload key leaves old entries without it for up to 14 days. Either the extension must treat the key as optional, or the change adds a payload-shape term to `_summary_key`. The segment item shape (`start`, `end`, `label`) has the same gap, and is not validated on read.
2. **Transcripts: add-only, tolerant reads.** The key is the video id alone, so it can't be bumped without discarding every transcript. A forced miss costs a yt-dlp fetch for captioned videos and **re-runs Whisper** (minutes of CPU) for `audio:` entries (`txcache.py:42-45`). New fields must therefore be optional, read with a default the way `source` is (`txcache.py:52-54`, guarded by `tests/test_server.py:1244` "a cache file from before provenance is still a hit"). Never rename or remove `saved_at`, `meta`, `cues`, or the cue triple order. The one deliberate invalidation path is the source: `_effective_source` makes an `audio:<old model>` entry miss when `whisper_model` changes (`server.py:878-906`).
3. **`usage.jsonl`: append-only, with a new `kind` for a new kind of row.** Never rewrite or reorder existing lines. A new field is added to new rows only, and readers use `.get(...) or 0` (`cli.py:481-483`). A new non-call `kind` must be added to the exclusion at `cli.py:488`, or `tldw usage` counts it as a model call. Renaming a field silently zeroes it in reports for every older row.
4. **Voices: the mtimes are data.** The `.onnx` mtime means "last spoken" and the marker's mtime means "tracking since". Any change that rewrites, copies or re-downloads voice files resets that information. Renaming or removing a model in `audio.VOICES` deletes its files at the next `tldw serve` start (`server.py:104-107`). That is intended, and it is a delete.
5. **`config.json`: add keys, don't rename them, and keep every value a string.** The CLI writes only strings (`cli.py:351`). A new reader must check the type before using the value, the way `_whisper_policy` does (`isinstance`, `server.py:152-153`) and `_max_transcribe_ms` does (`server.py:125-133`); today `llm_cmd`, `ytdlp_cookies`, `whisper_model` and `output_dir` don't (store 5). A renamed key silently reverts to its default for anyone who set the old one.
6. **Token:** changing its format or location invalidates every configured extension.
7. **Alignment cache (store 13): change the key, never the file.** Anything that shapes a window's words goes into `ALIGN_FINGERPRINT`, and a payload-shape change bumps `ALIGN_SHAPE`. Old files then miss, and the cost is re-timing each window once (seconds of CPU and one audio download per video). Never add the word list to the key.
8. **Claude sessions:** not tldw's format. Moving `CLAUDE_DIR` strands existing sessions under the old project, while `--resume` keeps working by id (`claude_client.py:117-118`).

## Recorded facts and weaknesses

Recorded as they are; the code is not changed by this doc. Each names the task that tracks it.

1. **`config.json` writes are not atomic.** `set_value` / `unset` call `write_text` directly (`config.py:31`, `config.py:40`), with no temp file and no lock. A crash or a concurrent `tldw config set` mid-write leaves invalid JSON, which `load` reads as `{}` (`config.py:19-20`). Every setting then silently reverts to its default (an `off` Whisper pin becomes the request's choice), and the next `set` writes a file holding only that one key. Tracked in T-014.
2. **Owner-only modes are applied after creation:** the token (`server.py:706-707`), `usage.jsonl` (`usage.py:187-189`) and `CLAUDE_DIR` (`claude_client.py:148-149`). `usage.jsonl` has no test of its mode. Tracked in T-004.
3. **The cache `.tmp` name is per key, not per writer** (`txcache.py:84`, `txcache.py:124`, `txcache.py:158`). Two concurrent writes of the same key can move each other's partly written file. The cost is a miss, never a wrong hit. Tracked in T-014.
4. **No language in any cache key.** `lang` shapes the transcript fetch (`core.py:56`) but isn't part of the transcript, summary or segment key (`txcache.py:30-33`, `server.py:748-770`, `server.py:376-394`). Whether any client sends a language other than `en` is `UNKNOWN — needs human confirmation`. Tracked in T-014.
5. **`youtube-tldw-` and `youtube-tldw-tx-` temp dirs are never swept.** Only `youtube-tldw-audio-*` is (`hygiene.py:92`). A CLI run killed before its `finally` leaves its downloads behind. Tracked in T-014.
6. **`usage.jsonl` grows without bound**, and nothing reports it. Tracked in T-014.
7. **Stale comments:**
   - the in-memory `_transcript_cache` value is described as three items, when it holds four (`server.py:38` against `server.py:314-315`);
   - `_tx_jobs` is described as keyed by video id, when the key is `(video_id, model)` (`server.py:64` against `server.py:196`);
   - the request-log comment says the line has "no query" (`server.py:962-963`), when the stdlib line includes the query string.

   Tracked in T-012.
8. **The extension's in-memory summary map is keyed by video id only** (`background.js:25`, `background.js:205`), so after a model or ratio change it serves its copy until a refresh. Frontend to confirm. Tracked in T-014.
9. **The summary key misses three inputs that shape a summary:** `_ratio_clause`, `_metadata_header` and `SINGLE_PASS_CHARS` (`summarize.py:17`, `:151`, `:241`; `PROMPT_FINGERPRINT` covers only the four strings at `summarize.py:144`). Editing one serves stale summaries for up to 14 days. `SEGMENT_FINGERPRINT` likewise misses `_metadata_header` and `SINGLE_PASS_CHARS` (`core.py:35-42`). Tracked in T-014.
10. **Wrong-typed values break readers instead of falling back:**
    - a non-numeric `video_ms` or `read_ms` in one `usage.jsonl` row makes `stats()` raise (`usage.py:218-219`), uncaught at `server.py:1234`, `server.py:1328` and `server.py:1369`;
    - a non-string `llm_cmd` or `ytdlp_cookies` raises at `.strip()` (`claude_client.py:210`, `metadata.py:53-54`);
    - a list or object `whisper_model` raises at `saved in VALID_MODELS` (`whisper_transcribe.py:79`);
    - a non-string `output_dir` raises at `Path(saved)` (`cli.py:31`);
    - a `whisper_max_minutes` of 0 or below is clamped to 1 minute, not refused or defaulted (`server.py:133`).

    Tracked in T-014.
11. **Cache files `tldw cache clean` can't read are never cleaned by it.** A file that is unreadable or has no `saved_at` is skipped (`hygiene.py:197`, `hygiene.py:215-216`); only the 500-file cap or an overwrite of the same key removes it. Tracked in T-014.
12. **No fingerprint covers the cached payload or segment-item shape** (Per store, rule 1, "Gap"). Tracked in T-014.
13. `UNKNOWN — needs human confirmation`: the Claude CLI's session format, file modes and retention; whether it writes into `CLAUDE_DIR`; faster-whisper's on-disk layout; yt-dlp's own cache location, layout and retention; the browser cookie database's location and whether yt-dlp keeps a copy. Tracked in T-015, except the yt-dlp cache and cookie-database items, which are listed in T-014 and not yet in T-015.

## How this was verified

- Read in full: `txcache.py`, `hygiene.py`, `usage.py`, `config.py`, `audio.py`, `whisper_transcribe.py`, `claude_client.py`, `metadata.py`, `core.py`, `naming.py`, `textmode.py`, `ask.py`, `proc.py`, `tests/test_isolation.py` and `tests/conftest.py`. Read the store-touching parts of `server.py`, `cli.py`, `summarize.py` and `videomode.py`.
- Every `Path.home()` constant in `src/` was matched one to one against `HOME_PATHS` (10 entries), and every `mkdtemp` prefix (3) against the temp table.
- Every call site of `txcache.*`, `hygiene.*` and `usage.record` / `note_*` was found by grep and traced to its route.
- Every record shape was taken from its writer (the `json.dumps` call) and checked against its reader's key accesses and `except` tuples.
- The `tldw cache` coverage table was built from `_run_cache`'s `areas` list and `clean` branch, and from `startup_hygiene`.
- Absences were verified by grep with no hits: encryption, keychain and keyring code; `logging`; version or schema fields in the store modules; module-level registries beyond those listed.
- Round 1 fixes were re-checked in the code: the four strings in `_prompt_fingerprint` (`summarize.py:144`) against every use of `_ratio_clause`, `_metadata_header` and `SINGLE_PASS_CHARS` in `summarize.py`; every `config.get(` call site's handling of a non-string; `usage.stats()`'s `int()` calls and their three callers; `ensure_voice_tracking` and `expired_entries` in `hygiene.py`; every `fetch(` in `extension/` for a query string; and every `yt-dlp` argv for `--no-cache-dir` and `_cookie_args()` (grep: no `--no-cache-dir` in `src/`).
- The examples are invented.

## Update this doc when

- `src/youtube_tldw/txcache.py`, `hygiene.py`, `usage.py` or `config.py` changes.
- Any `Path.home()` constant, `mkdtemp` prefix, or `.tmp` handling is added or changed in `src/youtube_tldw/` (and `tests/conftest.py` `HOME_PATHS` with it).
- `server.py`'s `_summary_key`, `_seg_key`, `_seg_file`, `_to_payload`, `_cache_put`, `_cache_get`, `_effective_source`, `load_or_create_token`, `startup_hygiene`, `_stale_audio_age`, or any module-level registry changes.
- `summarize._ratio_clause`, `_metadata_header` or `SINGLE_PASS_CHARS` changes (they shape summaries but aren't in the summary key), or any `config.get(` reader or `usage.stats` changes how it handles a value's type.
- `summarize.PROMPT_FINGERPRINT` or `core.SEGMENT_FINGERPRINT` changes what it covers (not every prompt edit: those change the fingerprint's value, which is the mechanism working).
- `claude_client.CLAUDE_DIR`, `_claude_argv` (session persistence) or `backend_fingerprint` changes.
- `audio.VOICES`, `VOICE_DIR` or `ensure_voice` changes; or `whisper_transcribe._CACHE_DIR` or the `WhisperModel` call.
- `cli.py`'s `_run_cache`, `_run_usage`, `_run_config`, `_default_output_dir` or `DEFAULT_OUTPUT` changes; or `naming.py`.
- The extension adds a persistent store or changes what it keeps in memory.
- `align.py`'s key, fingerprint inputs, `ALIGN_SHAPE`, read validation, caps or `ALIGN_DIR` change; or `_run_align`'s prune or write rules; or `muteWords`/`muteSound` handling in `options.js` changes (T-020).
