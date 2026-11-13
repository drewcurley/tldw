# Domain

**Status: descriptive.** This describes the code as it is on `chore/docs-tier2`, at tldw 1.3.0 (`src/youtube_tldw/__init__.py:3`, `pyproject.toml:7`). Nothing here is a plan. Every claim cites the code or a Tier 1 doc. Where neither settles a fact, it says `UNKNOWN — needs human confirmation`.

**What this doc owns, and what it doesn't.**
- It owns the **entities** tldw reasons about, their **logical relationships**, and the **vocabulary reconciliation**: where the code, the UI, the READMEs, the tasks and the Tier 1 docs use different words for one thing.
- [`data-model.md`](data-model.md) owns every store's path, record shape, key composition, TTL, cap and classification. They are cited here by store number (`data-model.md`, "Stores at a glance"), never restated.
- [`contracts.md`](contracts.md) owns the inventories: routes, body keys, events, messages, storage keys, config keys, env vars and limits. If this doc and `contracts.md` disagree on an inventory item, `contracts.md` wins, and this doc is the one to fix. The one disagreement found while drafting was corrected in `contracts.md` in this PR (§ 7, item 1).
- [`glossary.md`](glossary.md) owns the one-line definition of each term. This doc names the canonical term and the entity behind it; the glossary defines it.

## 1. The domain in one paragraph

A user is watching a **video** on YouTube. tldw gets the video's **transcript** (from the page, a cache, `yt-dlp`, or a local Whisper **transcription**) and asks a **model backend** for one of three things: a written **summary**, a list of **key moments** to skip through, or an **answer** to a question. From the summary it can also make **read-aloud audio** in a chosen **voice**. Every model call, and every finished summary, leaves a **usage row**. Two front doors reach that core (`architecture.md` § 1): the browser extension, through `tldw serve`, authenticated by a **bearer token**; and the CLI, `tldw <url>`, which writes a **TL;DW output** file. Both are shaped by **settings**: the server's `config.json` and the extension's `chrome.storage.local`. There is one user per install, on their own machine (`architecture.md` § 1; ADR 0003).

## 2. Entities

Each entry gives the definition, the identity (what tells two apart), where it lives, and the code that defines it. Shapes and lifecycles are in `data-model.md`, under the store number given.

### 2.1 Video

- **Definition:** one YouTube video, the subject of every other entity. tldw never stores a video by itself. It is the key that everything else hangs from.
- **Identity:** the 11-character video id, validated by `^[A-Za-z0-9_-]{11}$` (`urls.py:19`). A URL is reduced to it by `canonical_video_id` (`urls.py:22-60`), and only https YouTube hosts or a bare id are accepted (`architecture.md` § 2.1). The extension's worker recognizes `/watch` and `youtu.be` URLs with the same pattern (`background.js:28-41`).
- **Attributes:** `VideoMeta`: `video_id`, `title`, `channel`, `duration_ms`, and the manual and automatic caption-track maps (`metadata.py:29-37`). The canonical link is `https://www.youtube.com/watch?v=<id>` (`watch_url`, `metadata.py:25-26`).
- **Where it lives:** as the key or file-name prefix of stores 1–3, in `usage.jsonl` rows (store 4), and in the in-memory registries (`data-model.md`, "In-memory stores").

### 2.2 Transcript

- **Definition:** the timed text of one video: an ordered list of **cues**, plus the video's meta, plus where the words came from.
- **Cue:** `Cue(start_ms, end_ms, text)` (`transcript.py:27-31`). The page sends rows of `{start_ms, text}` only, and the server builds the end times (`transcript.py:104-140`; `contracts.md`, "Request body keys", `cues`). **A page cue's `start_ms` has whole-second precision**: it is read from the panel's `m:ss` clock (`clockToMs`), so it is rounded down by up to 999 ms. "Cover listed words" pads a caption-line range by that much (2.15).
- **Source (provenance):** exactly one of two values per transcript:
  - `captions` (`txcache.CAPTIONS`, `txcache.py:36`): YouTube's manual subtitles, its auto-captions, or the transcript rows the panel read from the page. A page-supplied transcript is stored as `captions` too (`server.py:1311-1316`).
  - `audio:<model>`, such as `audio:small`: transcribed locally by Whisper with that model (`txcache.py:42`; built by `_audio_source`, `server.py:874-875`).

  An absent or non-string source reads as `captions` (`txcache.py:52-54`). The source rides into the summary payload as `transcript_source` (`server.py:1911`), which is how the panel can say "transcribed from audio by Whisper (`<model>`)" (`content.js:3190-3194`).
- **Two copies, kept separate:**
  - the **on-disk transcript cache**, store 1 (`txcache.py:21`), which survives restarts;
  - the **in-memory transcript cache**, `_transcript_cache` (`server.py:38`), a write-through copy in the `tldw serve` process (`server.py:309-317`). A memory miss falls back to disk and refills memory (`server.py:337-342`). An active Q&A conversation keeps its memory copy alive by touching it (`server.py:1795-1796`).

  Their TTLs and caps differ; both are in `data-model.md` (store 1, and "In-memory stores"). The CLI uses neither: `tldw <url>` fetches every time (`data-model.md`, store 1, "Writer").
- **Identity:** the video id alone. Language is not part of it (`data-model.md`, Recorded facts 4).

### 2.3 Summary

- **Definition:** the written TL;DW of one video, made by the model from one transcript.
- **Parts** (`TextResult`, `summarize.py:173-178`; the wire form is `_to_payload`, `server.py:1904-1921`, listed in `contracts.md`, "Summary payload"):
  - **key points:** a list of short strings (`key_points`);
  - **summary body:** markdown (`summary` in the model's JSON, `summary_md` on the wire);
  - **rationale:** the model's note on how hard it compressed (`rationale`);
  - **length label:** the reading time of the key points and body together, `<n>m` (`textmode.length_label`, `textmode.py:30-35`, using `timing.read_time_label`, `timing.py:82-85`). It is not the video's length, which is `original_length` (`timing.format_length`, `timing.py:68-79`);
  - **ratio:** two different things carry this name. The **requested summary ratio** (body `ratio`, CLI `--ratio`) is an input, a fraction in `(0, 1]` or absent for "let the model decide" (`_ratio_clause`, `summarize.py:151`), and part of the summary's identity. The model's own **`chosen_ratio`** is parsed (`summarize.py:200`) but never reaches the payload, the panel or the `.md` file (`server.py:1904-1921`, `textmode.py:12-27`). The extension never sends a summary ratio (`background.js:185`, `:226`; the code says so at `server.py:1239-1240`), so every browser summary is an "auto" one.
- **Identity:** the summary key (`_summary_key`, `server.py:748-770`). Its composition is owned by `data-model.md` § 2; in short, the video, the backend fingerprint, the requested ratio, the prompt fingerprint and the transcript source.
- **Where it lives:** the summary cache, store 2, written only by the streamed route (`data-model.md` § 2); the extension worker's in-memory `cache`, keyed by video id only (`background.js:25`, `:204-205`; `data-model.md`, Recorded facts 8); and, as text, a CLI `.md` file (store 11).
- **Derived:** the panel's "you've TL;DW'd <n> videos, <h> hours you didn't have to watch" figure comes from `video` usage rows (2.11), not from the summary cache (`usage.py:208-224`, `content.js:1221-1233`).

### 2.4 Key moments

- **Definition:** the parts of one video worth watching, chosen by the model from the transcript, and played by skipping the real YouTube player between them ("Play key moments", `content.js:877`).
- **Three levels:**
  - a **segment selection:** the whole ordered list for one video, under one set of inputs;
  - a **segment:** one item, `{start, end, label}`, in seconds rounded to 2 places, with `label` the `m:ss` of its display start (`core.py:146-148`; `contracts.md`, NDJSON `segment_added` and `segments`);
  - underneath, a **span** (`Span(start_ms, end_ms, label_ms)`, `spans.py:15-31`): the millisecond form the server and CLI shape, pad and cap before converting (`core.py:139-148`).

  The model itself answers in **cue ranges**, `(first_cue, last_cue)` index pairs (`VideoSelection.ranges`, `summarize.py:181-185`; `contracts.md`, "Model I/O contract").
- **Identity:** the segment key (`_seg_key`, `server.py:376-394`), owned by `data-model.md` § 3: the video, the **segment ratio**, the selection fingerprint, the backend fingerprint and the transcript source. A selection capped with `max_length` has no key and is never stored (`server.py:1554-1558`).
- **Segment ratio:** the extension's "Key moments length" setting (`options.html:47-56`), sent as `segment_ratio` on summarize requests and as `ratio` on `/segments/stream` (`background.js:185`, `:226`, `:588`). It shapes the selection only, "not the written summary" (`options.html:54-55`).
- **Prefetch:** after a summary, the server makes the selection in the background so it is ready when the user clicks (`server.py:434-522`; `contracts.md`, "Scheduled jobs and background work"). Prefetch is a way of producing a selection, not a separate entity.
- **Where it lives:** the segment cache, store 3; the in-memory `_seg_cache` (`server.py:39`); the panel's `lastSegments` for the page session (`content.js:86`).

### 2.5 Question-and-answer session

- **Definition:** a conversation about one video. The user asks; the model answers from that video's transcript, citing moments as `[m:ss]` timestamps the panel turns into seeks (`ask.py:6-8`, `content.js:1278`, `:1322`).
- **Three parts, in three places:**
  - **the conversation history:** `[{role, content}]`, kept by the panel for the page session, one per video (`chat`, `content.js:33-35`, `:1258`). The browser sends it with each question (`background.js:462`); the server trims it (`contracts.md`, "Limits, pools and slots", the `ask.MAX_*` row);
  - **the model session id:** held only by the server, in memory, keyed by the video id alone (`_ask_sessions`, `server.py:45`, `:422-431`). It lets a follow-up resume the model's session instead of re-sending the transcript (`ask.py:100-106`);
  - **the model's session file:** written by the `claude` CLI under `~/.claude/projects/<encoded tldw claude dir>/`, store 10. It holds the Q&A prompt, the title, the channel, the **full transcript**, the questions and the answers (`data-model.md` store 10; `ask.py:79-90`). It stays on disk after the server forgets the id, and tldw never cleans it.
- **Identity:** the video, while the server holds its session id. After that window, or a restart, the next question starts a new model session, and a stale id falls back to a full call (`ask.py:133-139`).
- **Not stored by tldw:** the questions and answers themselves are never in the server's memory beyond the request (`data-model.md`, "Personal data and protection", the in-memory row).

### 2.6 Voice and read-aloud audio

- **Voice:** one of the 13 allowlisted Piper voices, by short id (`amy`, `ryan` and the rest), each mapping to a Piper model name and a label (`audio.VOICES`, `audio.py:44-58`). The default is `amy` (`audio.py:59`); `female` and `male` are CLI aliases (`audio.py:60`). Its model files are store 7, downloaded on first use.
- **Read-aloud audio:** the spoken form of one summary in one voice: an mp3 synthesized from a script built from the title, channel, key points and body (`audio.build_spoken_script`, `audio.py:353`; `stream_speech`, `audio.py:281`). The panel calls it "Listen to summary" (`content.js:878`).
- **Voice preview:** a fixed sample sentence in one voice (`PREVIEW_TEXT`, `server.py:550`), memoized per voice (`_preview_cache`, `server.py:551`). A preview does not count as "spoken" for voice cleanup (`data-model.md` store 7).
- **Where it lives:** the extension worker's `audioCache`, keyed by video id and voice (`background.js:26`); the panel's `lastAudio` (`content.js:28`); a file the user saves from the panel, named `<title> - <channel> - tldw version.mp3` (`content.js:3123-3125`); or a CLI mp3 (2.7). The server keeps no read-aloud audio.

### 2.7 TL;DW output (CLI)

- **Definition:** the file `tldw <url>` writes: a TL;DW of one video, in one of two **modes** (`--mode`, `cli.py:48`):
  - **text:** the summary as Markdown (`textmode.render_markdown`, `textmode.py:12-27`), saved under `text/`;
  - **video:** a recut `.mp4` of the key moments (`_run_video`, `cli.py:193-273`), saved under `video/`. The model's `VideoSelection` (`summarize.py:181-185`) becomes `Span`s (`spans.py:15-31`) that are padded, capped and merged with a kept intro (`cli.py:198-213`), then cut with crossfades by `videomode.recut` (`videomode.py:158`);
  - **audio** is not a third mode. `--render-audio` (`cli.py:50`) adds an mp3 under `audio/` to either mode: the spoken summary for text mode, the recut's soundtrack for video mode (`cli.py:171-179`, `:268-272`).
- **Polish overlays** (video mode only, `Polish`, `videomode.py:135-155`): the **badge** ("TL;DW" in a corner), the **intro banner** ("TL;DW version" over the kept intro), the **end card** ("Made with youtube-tldw", after a fade to black), and the **timestamps** (the source time faded in at each cut). Each has a `--no-*` flag (`cli.py:68-81`).
- **Identity:** none. Each run writes a new file; an existing name gets ` (2)`, ` (3)` (`naming.py:74-84`). Naming and location are store 11 (`data-model.md` § 11).
- **Not shared with the server:** the CLI reads no cache and writes no `video` or `segments_served` usage rows; its model-call rows have `kind` `""` (`data-model.md` store 4).

### 2.8 Bearer token

- **Definition:** the shared secret that lets a caller use the server's POST routes (`contracts.md`, "Request headers the server reads", `Authorization`).
- **Identity and source:** one per running server: `--token`, else `TLDW_TOKEN`, else the token file, store 6, created if missing (`cli.py:603`, `server.py:692-708`).
- **Copies:** the extension keeps its own, in plain text, in `chrome.storage.local` under `token` (`contracts.md`, "chrome.storage.local keys"; `architecture.md` § 7, item 6; T-004). With the normal and the hackathon extension both installed, there are two such copies (T-004, T-019).
- **Class:** Secret (`data-model.md`, "Personal data and protection").

### 2.9 Whisper model

- **Definition:** the faster-whisper model size used to transcribe a captionless video: one of `VALID_MODELS` (`whisper_transcribe.py:25-28`), `small` unless config `whisper_model` says otherwise (`resolve_model`, `whisper_transcribe.py:76-81`).
- **Why it is an entity:** it is part of a transcript's identity (`audio:<model>`), and so of every summary and selection made from that transcript. Changing it makes cached audio transcripts, and their summaries, miss on purpose (`_effective_source`, `server.py:878-890`).
- **Where it lives:** the model files are store 8.

### 2.10 Transcription job and offer

- **Transcription job:** one run of Whisper over one video's audio, in its own thread, owned by the server and not by any request (`_start_transcription`, `server.py:187-295`). It is registered in `_tx_jobs` while it runs, keyed by `(video_id, model)` (`server.py:64`, `:196`). It carries its progress, a cancel flag, its budget, its result or error, and its status (`server.py:210-224`). It writes the transcript cache the moment it succeeds, whether or not anyone is still listening (`server.py:259-261`).
- **Transcription offer:** the `needs_transcription` event, which asks the user before any audio is fetched (`contracts.md`, NDJSON events). The panel shows it as "Transcribe the audio" (`content.js:845`). The video's meta is remembered briefly so accepting costs no extra `yt-dlp` call (`_captionless_meta`, `server.py:848-871`).
- **Transcription policy:** `off`, `ask` or `always` (`WHISPER_POLICIES`, `server.py:66`), chosen by the request's `whisper` field unless config `whisper` pins it for the machine (`_whisper_policy`, `server.py:136-160`). The CLI doesn't use the policy; it has `--whisper-fallback` (`cli.py:89`).

### 2.11 Usage rows

The usage log is store 4. Its row shapes are owned by `data-model.md` § 4; this is the domain view of what each row means.

| Row `kind` | Means | Written by | Source |
|---|---|---|---|
| `summarize`, `segments`, `ask` | One **model call**, made inside that kind of interaction | `usage.record`, on the stock `claude` backend only | `usage.py:132-155`, `claude_client.py:66-76`, `:405-408` |
| `""` (empty) | One model call made outside any interaction: every CLI call | the same | `usage.py:146-148` |
| `video` | One **finished summary**: video length, reading time, words. Not a model call | `usage.note_video` | `usage.py:192-205`, `server.py:1180`, `:1268` |
| `segments_served` | One **selection handed to the panel**, and how (`cache`, `prefetch` or `live`). Not a model call | `usage.note_served` | `usage.py:157-169`, `server.py:1568`, `:1586`, `:1646` |

- **Interaction:** the in-memory, per-thread grouping of the model calls made for one request: its kind, its video, its `trigger` (`prefetch` or `request`, segment rows only), and its calls (`Interaction`, `usage.py:55-77`; the scope, `usage.py:104-125`). It is not persisted as its own row; each call row carries its `kind`, `video_id` and `trigger` (`usage.py:146-151`). Interactions are opened at `server.py:501`, `:1175`, `:1263`, `:1619` and `:1792`.
- **Call:** `Usage`, one model call as the backend reported it (`usage.py:31-52`). Custom backends report no usage, so they leave no call rows (`data-model.md` store 4).

### 2.12 Model backend

- **Definition:** whatever answers the prompts: the `claude` CLI by default, or an operator-configured command (`TLDW_LLM_CMD`, else config `llm_cmd`) (`_backend`, `claude_client.py:205-213`; `contracts.md`, "Model I/O contract").
- **Model:** within the `claude` backend only, `opus` or `sonnet` (`MODELS`, `claude_client.py:164`), pinned per request thread (`use_model`, `claude_client.py:168-183`). For a custom backend, `/health` reports `kind` `custom` and the label "your model", and the Options page disables its model picker (`claude_client.py:238-248`, `options.js:54-63`).
- **Identity:** the backend fingerprint, a hash of the resolved argv, model included (`backend_fingerprint`, `claude_client.py:227-235`). It is part of every summary and segment key, so output from one backend or model is never served for another.
- **The prompt-injection boundary.** The transcript is untrusted third-party text, and the model is where it is interpreted. The no-tools invocation limits the damage, but only for the `claude` backend, and the summary prompt doesn't mark the transcript as untrusted (`architecture.md` § 7, item 10; T-007).

### 2.13 Settings

Two separate stores, with different writers, readers and defaults. Neither one sees the other.

| | Server config | Extension settings |
|---|---|---|
| Store | `~/.config/youtube-tldw/config.json`, store 5 | `chrome.storage.local`, one per installed extension in one browser profile |
| Written by | `tldw config set` / `unset` only (`cli.py:349-364`) | the Options page (`options.js:176-177`); the panel writes only the two sponsor keys (`content.js:1239`, `:1243`, `:1245`) |
| Read | on every call, so a running server sees changes at once (`config.py:15-24`) | at the moment each request starts (`contracts.md`, "chrome.storage.local keys") |
| Keys and defaults | `contracts.md`, "Config keys" | `contracts.md`, "chrome.storage.local keys", where the defaults are defined in three places and differ |
| Class | operator config, possibly personal (`data-model.md`) | Secret, because it holds the token (`data-model.md`; `architecture.md` § 8) |

Where they meet:
- **Transcription policy:** a config `whisper` value overrides the extension's `whisper` setting for every request (`server.py:148-154`). Their defaults differ on purpose: the extension defaults to `ask` (`background.js:11`, `options.js:5`), while the server treats an absent field as `off`, which keeps older extensions working (`server.py:136-146`, the docstring).
- **Model:** the extension's `model` setting only means something on the `claude` backend, which config `llm_cmd` or `TLDW_LLM_CMD` can replace (2.12).
- **Server URL and token:** extension settings that must match the running server (2.8), and nothing checks that the URL is loopback (`architecture.md` § 7, item 5).
- The CLI reads only server config (`output_dir`, `whisper_model`, `llm_cmd`, `ytdlp_cookies`); `whisper` and `whisper_max_minutes` are server-only (`server.py:126`, `:152`).

### 2.14 Not entities

These appear across the docs but are mechanisms, not things the domain is about. Each is defined in [`glossary.md`](glossary.md) and inventoried in `contracts.md`:
- the **slot pools** (`claude` and `speech`) and the **transcription permit** (`contracts.md`, "Limits, pools and slots");
- the **caches** themselves: they are stores of the entities above (`data-model.md`);
- the **NDJSON events** and **port messages**: transport, not domain events (`contracts.md`, "NDJSON events" and "Extension message protocol");
- the **panel**: the extension's on-page UI, where the entities are shown (`content.js:150-281`; personas and journeys are in `ux.md`).

### 2.15 "Cover listed words" (T-020)

The extension's feature that covers listed words in a playing video with a bleep, a moo or silence. Its entities live in the browser, except the alignment window's words, which are cached on the server.

- **Listed words:** the user's list (`muteWords`, or the shipped default in `extension/mute_words.js` when the key is absent). Matched whole-word and case-insensitively, and YouTube's `[ __ ]` censor marker counts as a match. An empty list is off. **The list never leaves the browser.**
- **Hit:** one caption line (cue) that holds at least one listed word or marker: `{id, cueStart, cueEnd, kind ("word" | "marker"), count, line, words, applied}` (`muteRanges`). `count` is how many matches the line has; a hit is refined only when Whisper hears at least that many.
- **Mute range:** a span of media time during which the video is silenced. A hit starts with a **line range** (`cueStart − 200 ms` to the next strictly later cue's start `+ 1000 ms`, capped at 8 s) and may be narrowed to **word ranges** (`wordStart − 150 ms` to `wordEnd + 100 ms`, at least 300 ms). A word Whisper times at 2 s or longer is ignored (`MUTE_WORD_MAX_MS`), and so is one outside its window; a hit whose words are all ignored stays covered by caption line. Overlapping or touching ranges merge into the **active ranges**; a merged range is "line" if any part of it is. A refinement that would shrink the range the playhead is in waits until the playhead leaves it.
- **Alignment window:** a time span sent to the server for word timing, one per hit (`cueStart − 1000` to `cueEnd + 500`), joined while the result is at most 12 s; at most 200 per request, the ones still ahead of the playhead first. Its words are cached as store 13 (`data-model.md`).
- **Word-timed / by caption line:** whether a hit has word ranges. Everything not word-timed stays covered by caption line for as long as the feature is on.
- **Cover sound:** `bleep` (default: a 1 kHz sine), `mute` (silence only) or `moo` (a synthesised one); every mode silences the video itself. Sound plays only in the first 1.5 s of a range.
- **Session:** one per tab, owned by the page, not the panel (`muteCover`). It ends on Stop, a new video, a detached player, or leaving the page.

## 3. Relationships

**Cardinality here is logical.** Its *enforcement* is `N/A — file-backed`: there is no database, so no foreign keys, unique constraints or cascades (`data-model.md`, "Tables, indexes, foreign keys, partitioning, replication"). Links are made by building a file name or a dictionary key from the parent's identity. Every dangling reference is tolerated, and the result is a miss or a fallback, never an error (`data-model.md`, "References between stores").

| From | To | Logical cardinality | How the link is made | Source |
|---|---|---|---|---|
| Video | Transcript | 1 : 0..1 at a time, per store | Store 1 file name and `_transcript_cache` key are the video id alone; a new transcript overwrites the old | `txcache.py:30-33`, `server.py:309-317` |
| Transcript | Cue | 1 : N, ordered | `cues` list | `txcache.py:75-82` |
| Transcript | Whisper model | N : 0..1 | `source` `audio:<model>`; `captions` has none | `txcache.py:36-46` |
| Video | Summary (cached) | 1 : N | One per backend fingerprint × requested ratio × prompt fingerprint × transcript source, each its own file | `server.py:748-770`; `data-model.md` § 2 |
| Video | Summary (extension memory) | 1 : 0..1 | `cache` keyed by video id only, until a regenerate | `background.js:25`, `:204-205` |
| Summary | Transcript | N : 1, by value | Only through `source` in the key and `transcript_source` in the payload; the summary file doesn't point at the transcript file, and outlives it | `server.py:1911`; `data-model.md`, "References between stores" |
| Summary | Key point | 1 : N, ordered | `key_points` list | `summarize.py:173-178` |
| Video | Segment selection | 1 : N | One per segment ratio × selection fingerprint × backend fingerprint × transcript source; a `max_length` selection is 0..N and never stored | `server.py:376-398`, `:1554-1558` |
| Segment selection | Segment | 1 : N, ordered | `segments` list | `txcache.py:154-161`, `core.py:146-148` |
| Video | Running selection | 1 : 0..1 per segment key | `_seg_prefetch_events`: one selection per key at a time, prefetch or live | `server.py:42`; `contracts.md`, "In-memory stores" |
| Video | Q&A session (server) | 1 : 0..1 active, for the in-memory window | `_ask_sessions` keyed by video id only (not by model) | `server.py:45`, `:422-431` |
| Video | Model session file | 1 : N over time | A new model session after each expiry or restart; old files stay in store 10 | `ask.py:133-139`; `data-model.md` store 10 |
| Page session | Conversation history | 1 : 0..1, for one video | `chat = {videoId, history}`, replaced when the video changes | `content.js:35`, `:1258` |
| Q&A session | Turn | 1 : N, ordered | `history` items; the server keeps only the last few (`contracts.md`, the `ask.MAX_*` row) | `ask.py:52-64` |
| Video × voice | Read-aloud audio | N : M, at most 1 per pair in the worker | `audioCache` key is the video id and the voice | `background.js:26` |
| Voice | Voice preview | 1 : 0..1 | `_preview_cache` (server), `previewCache` (worker, Options) keyed by voice | `server.py:551`, `background.js:523`, `options.js:81` |
| Video × Whisper model | Transcription job | N : M, at most 1 per pair at a time; at most 1 running machine-wide | `_tx_jobs` key `(video_id, model)`; a second caller joins the first job; one transcription permit | `server.py:64-65`, `:196-209` |
| Video | Transcription offer | 1 : 0..1, for its window | `_captionless_meta` keyed by video id | `server.py:848-871` |
| Model backend | Summary, segment selection | 1 : N | `backend_fingerprint()` in both keys | `claude_client.py:227-235` |
| Video | Usage row | 1 : N | `video_id` field; `stats()` keeps only the latest `video` row per video | `usage.py:146-148`, `:208-224` |
| Interaction | Call row | 1 : N, in memory only | Rows copy the interaction's `kind`, `video_id` and `trigger`; the grouping itself is not stored | `usage.py:55-77`, `:146-151` |
| Call row | Model session file | N : 0..1 | `session_id` field; nothing resolves it | `usage.py:42`; `data-model.md` store 10 |
| Server process | Bearer token | 1 : 1 | Chosen once at start | `server.py:692-708` |
| Bearer token | Extension copy | 1 : N | One `token` per installed extension; nothing keeps them in step, and a new token means every copy is stale (401) | `data-model.md` store 6; `contracts.md`, the token-file row |
| User's home | Server config | 1 : 1 | One `config.json`, shared by every `tldw` process | `config.py:12` |
| Installed extension | Extension settings | 1 : 1 | One `chrome.storage.local` per extension, per browser profile | `contracts.md`, "chrome.storage.local keys" |
| Page session | "Cover listed words" session | 1 : 0..1 | `muteCover`; per tab | `content.js` `createMuteCover` |
| Session | Hit | 1 : N, ordered | `muteRanges` over the page's cues | `content.js` pure block |
| Hit | Mute range | 1 : 1 line range, or 1 : N word ranges once refined; ranges merge across hits | `buildMuteRanges` | same |
| Hit | Alignment window | N : 1 (a window may hold several hits) | `alignWindows` joins while ≤ 12 s | same |
| Video × window × Whisper model | Cached window words | 1 : 0..1 | store 13 key (no word list in it) | `align.py` `cache_key` |
| CLI run | TL;DW output | 1 : 1, plus 0..1 mp3, plus 0..1 kept source video | `naming.avoid_overwrite`; a re-run is a new file | `cli.py:160-272`, `naming.py:74-84` |

## 4. Constraints

The values are owned elsewhere and are cited, not restated:
- **TTLs and caps** of every store and registry: `data-model.md`, per store and "In-memory stores".
- **Request limits** (body caps, question and history bounds, the single-pass transcript limit, timeouts, the transcription ceiling): `contracts.md`, "Limits, pools and slots".
- **Allowlists:** model (`contracts.md`, `model` body key), voice (`audio.VOICES`), Whisper model (`VALID_MODELS`), transcription policy (`WHISPER_POLICIES`), cookie browsers (`contracts.md`, `TLDW_YTDLP_COOKIES`).

The domain rules those values serve:
1. **A video is its validated id.** Nothing downstream accepts anything else, and that is what keeps cache file names inside their directories (`contracts.md`, "Cross-cutting data invariants", the file-name row).
2. **A transcript has exactly one source,** `captions` or `audio:<model>` (`contracts.md`, "Cross-cutting data invariants", the provenance row).
3. **Everything that shapes a cached summary or selection is in its key,** with known gaps that are recorded, not fixed (`data-model.md`, Recorded facts 9 and 12; T-014).
4. **A summary ratio and a segment ratio are separate inputs.** The first shapes the written summary; the second shapes key moments only (`server.py:360-373`, the docstring; `options.html:54-55`).
5. **One transcription per video and model, and one at a time on the machine** (2.10).
6. **Usage is append-only, and a row's kind decides what it counts as** (`data-model.md` § 4; `contracts.md`, "Cross-cutting data invariants").
7. **What leaves the machine.** Transcripts, titles and questions go to Anthropic through the `claude` CLI, or to whatever a custom backend contacts; YouTube and Hugging Face are contacted too (`architecture.md` § 8). tldw is local-only, not "fully private".
8. **Q&A keeps the transcript after the conversation ends,** in the model's session file (2.5; `data-model.md` store 10).

## 5. Vocabulary

**Definitions are in [`glossary.md`](glossary.md).** This section reconciles the words the sources use and names the one these docs use. It changes no code and no copy (plan rule 9); where a source is wrong, the finding is recorded in the task that tracks it.

Sources compared: the code identifiers in `src/youtube_tldw/` and `extension/`; the UI copy in `content.js` and `options.html`; `README.md` and `extension/README.md`; `contracts.md` and `data-model.md`; and the criteria and items in `tasks/`.

| Docs' term | Also called | Where | Note |
|---|---|---|---|
| **tldw** (the tool), **TL;DW** (its output) | `youtube-tldw` (package and README title), `tldw` (command), "TL;DW" (panel heading and toolbar), `TL;DW — Hackathon` (this fork's extension name) | `pyproject.toml:6`, `pyproject.toml:17-18`, `README.md:1`, `content.js:272`, `contracts.md`, "Manifest keys" | "TL;DW" also names the output: "text TL;DW", "video TL;DW" (`README.md:105-108`) |
| **video** | "video id", `vid`, `videoId` | throughout | — |
| **transcript** | "captions", "subtitles", "auto-captions", "transcript cues" | `metadata.py:29-37`, `options.html:57`, `README.md:20-21` | "Captions" is one source of a transcript, not a synonym. Options says "Videos with no subtitles" for a video with no caption source at all |
| **cue** | "transcript row" (from the page), "panel row" | `transcript.py:27-31`, `transcript.py:99` | — |
| **transcript source** | `source` (cache), `transcript_source` (payload), "provenance" | `txcache.py:36`, `server.py:1911`, `contracts.md` | — |
| **summary** | "TL;DW", `summary_md`, `summary`, "the written summary" | `server.py:1919`, `summarize.py:176`, `options.html:54` | The summary *body* is one part of a summary (2.3) |
| **length label** | "read", "read-time" | `content.js:960`, `README.md:308` | Not the video's length (`original_length`) |
| **summary ratio** | `ratio` (body), `--ratio` (CLI) | `server.py:1155-1162`, `cli.py:57` | `README.md:132` says the extension exposes "the same dial" as Options → Key moments length; that holds for video mode only (Recorded findings) |
| **segment ratio** | `segment_ratio`, `segmentRatio`, "Key moments length" ("Auto", "Tight", "Balanced", "Generous") | `server.py:360-373`, `contracts.md`, `options.html:47-52` | Sent as `ratio` on `/segments/stream` (`background.js:588`) |
| **key moments** (the feature) | "Play key moments", "key segments" | `content.js:877`, `README.md:410-411` | — |
| **segment selection** | "selection", "segments", "key-moment selection" | `server.py:376-394`, `README.md:276-280`, `usage.py:59-62` | — |
| **segment** | "clip" (the panel's skip pill, "Clip 2/5") | `core.py:146-148`, `content.js:3075` | The docs say "segment"; "clip" is UI copy for one segment being played. A CLI video's "clip" (`cli.py:34`, `MIN_CLIP_MS`) is a span |
| **span** | "cut", "cue range" (the model's form) | `spans.py:15-31`, `summarize.py:181-185` | Internal; never on the wire |
| **prefetch** | "background key-moment prefetch", `trigger: "prefetch"` | `server.py:434-522`, `usage.py:59-62` | A way to make a selection, not an entity |
| **question-and-answer session** (Q&A) | "Ask about this video", "follow-up questions", "chat", `ask` | `content.js:970`, `README.md:189`, `content.js:33-35`, `server.py:1792` | — |
| **read-aloud audio** | "Listen to summary", "speech", "speak", "TTS", "audio" | `content.js:878`, `server.py:1652`, `README.md:160` | The `speech` slot pool is named after it. "Audio" in the CLI means `--render-audio`, which is not a mode (2.7) |
| **voice** | "voice model" (Piper's), `female` / `male` (CLI aliases) | `audio.py:44-60` | — |
| **voice preview** | "▶ Preview", "sample" | `content.js:880-882`, `options.html:36` | — |
| **transcription** | "Whisper fallback", "transcribe the audio", `--whisper-fallback`, `whisper` (policy) | `README.md:324`, `content.js:845`, `cli.py:89`, `server.py:66` | "Whisper" is also the model family (2.9) |
| **transcription offer** | `needs_transcription` | `contracts.md`, NDJSON events | — |
| **model backend** | "backend", "your model", `llm_cmd`, `TLDW_LLM_CMD` | `claude_client.py:205-248`, `README.md:65` | "Backend" is the command; "model" is `opus` / `sonnet` inside the `claude` backend |
| **model** | "Thorough — Opus", "Fast — Sonnet" | `options.html:39-42`, `claude_client.py:164` | — |
| **bearer token** | "server token", "token" | `background.js:176`, `options.html:31` | — |
| **server config** | `tldw config`, "config" | `config.py:12`, `contracts.md`, "Config keys" | — |
| **extension settings** | "Options", "options page", "storage" | `options.html`, `contracts.md`, "chrome.storage.local keys" | — |
| **panel** | "modal", "on-page modal" | `content.js:150-281`, `README.md:402`, `extension/README.md:42` | The code and `contracts.md` say "panel"; the READMEs say "modal". The docs and the tasks (T-009) say "panel" |
| **regenerate** | "↻", "Summarize again", `refresh` | `content.js:274`, `contracts.md`, `refresh` body key | — |
| **usage row** | "usage", "usage.jsonl row", "call" | `usage.py`, `data-model.md` § 4 | A *call row* is one kind of usage row (2.11) |
| **interaction** | `kind` on a call row | `usage.py:55-77` | — |
| **TL;DW output** | "saved file", "output", "render folder" | `README.md:304-312`, `data-model.md` store 11 | — |
| **polish** | "video polish", "badge", "banner", "end card", "timestamps" | `videomode.py:135-155`, `README.md:144-155` | — |

**Tasks' own criteria.** Checked against `tasks/T-004.md` to `tasks/T-019.md`. They use "panel", "key moments", "model", "whisper", "voice", "token", "transcript" and "backend" in the senses above, and none conflicts. T-018 says "model, whisper and voice lists", meaning the allowlists in 2.6, 2.9 and 2.12.

## 6. N/A categories

- **Users, accounts and tenants:** N/A — one user per install, on their own machine; there is no user entity and no multi-tenancy (`architecture.md` § 1; ADR 0003).
- **Referential enforcement** (foreign keys, cascades, unique constraints): N/A — file-backed (§ 3).
- **Domain events or an event bus:** N/A — tldw publishes no events. NDJSON events and port messages are transport (`contracts.md`).
- **Regulated data domains:** N/A — no health, payment, children's or government data (`architecture.md` § 8, "Classification").
- **Lifecycle state machines with stored states:** N/A — no entity has a persisted status field. The only states are in memory: a transcription job's flags while it runs (`server.py:210-224`), and the panel's UI state (`content.js:20-89`).

## 7. Recorded findings

Found while drafting. Recorded as facts; nothing is changed by this doc.

1. **Corrected in this PR:** `contracts.md`'s probe row and `segmentRatio` storage row used to say the summary cache key depends on `segment_ratio`. They now say `segment_ratio` drives only the key-moments prefetch. The summary key uses the body's `ratio`, which the extension never sends (`server.py:748-770`; `_run_cached`, `server.py:1350-1367`; the code's own comment, `server.py:1239-1240`). `model` affects the key through the backend fingerprint (`claude_client.py:227-235`).
2. **A stale comment:** `usage.py:57` lists `video` as an `Interaction` kind, but no code opens a `video` interaction; `video` rows are written directly by `note_video` (`usage.py:201-203`). Appended to T-012.
3. **`README.md:14` calls a local backend "fully offline".** YouTube is still contacted for metadata and captions when the page doesn't supply them, and Hugging Face on first use, whatever the backend (`architecture.md` § 8). Appended to T-012.
4. **`README.md:132` says the extension exposes "the same dial" as `--ratio`.** That holds for video mode. In text mode, `--ratio` shapes the written summary, which the extension's setting never affects (`options.html:54-55`; the extension sends no summary ratio, `background.js:226`). Appended to T-012.
5. **Two mp3 naming schemes.** The CLI names files `<channel> - <title> - tl;dw - <length>.mp3` (`naming.py:52-55`); the panel's download is `<title> - <channel> - tldw version.mp3` (`content.js:3123-3125`). A fact, not a weakness: they are different products of different entities (2.6, 2.7).
6. **The server's Q&A session is keyed by the video alone,** not by model (`server.py:45`, `:428-431`). A follow-up asked after switching the model setting resumes the session the other model started. What the `claude` CLI does with `--resume` under a different `--model` is `UNKNOWN — needs human confirmation`, and is not tracked as a new item: T-015 already asks about the CLI's session behaviour.

## How this was verified

- Read in full: `txcache.py`, `usage.py`, `textmode.py`, `spans.py` (the dataclass), `core.py`, `ask.py` (the module docstring and `answer`), and the parts of `server.py` that define every registry (`:30-75`), the transcription job (`:136-232`), the segment and session keys (`:358-431`), the summary key and effective source (`:745-910`), the streamed and probed summary paths (`:1150-1370`) and `_to_payload` (`:1904-1921`).
- Read the entity-defining parts of `metadata.py`, `transcript.py`, `summarize.py`, `claude_client.py`, `audio.py`, `whisper_transcribe.py`, `videomode.py`, `naming.py`, `timing.py`, `cli.py` (`build_parser`, `_run_text`, `_run_video`, `run`, `_serve_parser`, `main`), `background.js` (settings, request bodies, caches), `content.js` (state, result view, offer view, ask, sponsor box, saved file name) and `options.html`.
- Enumerated: every `@dataclass` and class in `src/` (grep `^class \|@dataclass`); every `config.get(` caller; every `usage.interaction` / `note_*` / `record` call site; every `JSON.stringify` request body in `extension/`; every `chosen_ratio` use; every `_summary_key`, `_seg_key` and `_segment_ratio` call site.
- Vocabulary: counted each candidate term across `tasks/T-0*.md`, `docs/*.md`, both READMEs and the extension's UI files, and read the hits that disagreed.
- Checked every relationship's key against the code that builds it, and every TTL, cap and limit reference against the `data-model.md` or `contracts.md` section cited, without copying their values.
- No server or extension was run. There are no examples with data in this doc.

## Update this doc when

- A store is added, removed or re-keyed: `txcache.py` paths or `_path`; `server.py`'s `_summary_key`, `_seg_key`, `_seg_file`, `_effective_source`, `_audio_source`, `_to_payload`, or any module-level registry (`_transcript_cache`, `_seg_cache`, `_seg_prefetch_events`, `_seg_progress`, `_ask_sessions`, `_tx_jobs`, `_captionless_meta`, `_preview_cache`).
- A domain dataclass changes: `VideoMeta`, `Cue`, `TextResult`, `VideoSelection`, `Span`, `Summary`, `Polish`, `Usage`, `Interaction`.
- `usage.py` gains a row `kind`, or an interaction kind is added at a new `usage.interaction(` call site.
- `claude_client.MODELS`, `_backend`, `backend_fingerprint` or `backend_info` changes; `audio.VOICES`, `DEFAULT_VOICE` or `VOICE_ALIASES` changes; `whisper_transcribe.VALID_MODELS` or `DEFAULT_MODEL` changes; `server.WHISPER_POLICIES` or `_whisper_policy` changes.
- The CLI gains or loses a mode or an output kind (`cli.py` `--mode`, `--render-audio`, the `--no-*` polish flags), or `naming.py` changes.
- The extension adds a setting, changes what it sends as `ratio` or `segment_ratio`, adds an in-memory cache, or changes UI copy that names an entity (`content.js`, `options.html`).
- `contracts.md` or `data-model.md` renames a store, a section cited here, or an inventory item named in § 5.
- `glossary.md` changes a canonical term used here.
- "Cover listed words" changes what a hit, range or window is (`content.js`'s T-020 pure block), or store 13's key (T-020).
