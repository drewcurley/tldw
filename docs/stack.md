# Stack

> **Status: descriptive.** These are the choices in the code today (tldw 1.3.0) and why they were made, as far as the code, its comments, its commit history and the pre-playbook plans in `docs/reviews/` record it. Those older plans are non-binding history, cited only as evidence of what was considered. Where no reason or alternative is recorded anywhere, this says `UNKNOWN — needs human confirmation` rather than supplying one.
>
> **Load-bearing** (marked ⚓) means replacing the item would touch many files or a contract other parts depend on. The foundational choices are recorded in ADR 0001 (`decisions/0001-foundational-stack.md`).

## Categories

| Category | In use | Load-bearing |
|---|---|---|
| Language(s) | Python ≥ 3.11 (server, CLI); JavaScript (extension); POSIX shell (hooks, CI scripts, `build-firefox.sh`) | ⚓ |
| Frontend framework | None: a Manifest V3 extension in plain JS, no bundler, no build step for Chrome | ⚓ (MV3) |
| Backend framework | None: Python stdlib `http.server.ThreadingHTTPServer` | ⚓ |
| Database | N/A: no database. JSON files on disk | ⚓ (file formats) |
| Cache | In-process dicts plus JSON files under `~/.cache/youtube-tldw/` | |
| Queue | N/A: no queue. In-process threads and counted slot pools | |
| Authentication provider | N/A: no provider. A self-issued bearer token | ⚓ |
| Continuous Integration provider | GitHub Actions; Azure Pipelines file kept but inert | |
| Hosting platform | N/A: not hosted. Each user runs `tldw serve` on their own machine | ⚓ (by decision) |
| Monitoring stack | N/A: no monitoring service. stdout/stderr logs plus `usage.jsonl` and `tldw usage` | |
| Secret manager | N/A: no secret manager. A 0600 token file or an env var | |
| Package manager | pip / pipx (install); `uv` only to generate the CI lock file | |
| Model backend | The `claude` CLI, or any operator command (`TLDW_LLM_CMD`) | ⚓ |
| Media tooling | `yt-dlp`, `ffmpeg`, `ffprobe` | ⚓ (`yt-dlp`) |
| Speech (TTS) | Piper (`piper-tts`, optional) | |
| Transcription (STT) | faster-whisper (optional) | |
| Imaging | Pillow (video-mode overlays) | |
| Test runners | pytest; node `vm` harnesses run from pytest | |
| Build backend | setuptools | |

## Each entry: why, and what else was considered

### Language: Python ≥ 3.11, plain JavaScript, shell ⚓
`requires-python = ">=3.11"` (`pyproject.toml:9`). Python is the whole server and CLI. The repo started as a Python CLI (first commit `5115675`) and the server was added to it (`1fcaa57`), reusing its core rather than rewriting it: the server imports `core`, never `cli` (`docs/reviews/PLAN-extension.md`, "Shared `core.py`"). The extension is plain JS because MV3 runs it directly; there is no TypeScript or bundler (`extension/` has no `package.json`). **Alternatives considered:** `UNKNOWN — needs human confirmation`; none is recorded for either language.

### Frontend framework: none (MV3, plain JS) ⚓
Three scripts and one HTML page (`extension/background.js`, `content.js`, `options.js`, `options.html`), with a `browser ?? chrome` shim so one copy runs in Chrome and Firefox (`background.js:6-8`). The panel is built as HTML strings inside a shadow root, so YouTube's styles can't leak in (`content.js:1-3`, `:150`). The choices inside it that are recorded:
- **A long-lived port instead of an awaited fetch** in the click handler, because MV3 suspends a service worker about 30 s into a plain fetch (`background.js:1-4`).
- **Reading the transcript panel instead of `ytInitialPlayerResponse`, the timedtext endpoint, or a MAIN-world script.** YouTube answers timedtext with 429 or an empty body, and MAIN-world injection isn't covered by `activeTab` (`content.js:397-412`).
- **`chrome.storage.local`, never `.sync`** (`docs/reviews/PLAN-extension.md`, "Extension scope cuts").

**Alternatives considered for the framework itself** (React, a bundler, TypeScript): `UNKNOWN — needs human confirmation`. **Load-bearing:** the MV3 manifest and its permissions, the port protocol, and the page selectors are all contracts (see `contracts.md`). There is no framework to swap, but moving off MV3 would be a rewrite.

### Backend framework: Python stdlib `http.server` ⚓
"Stdlib only" (`server.py:1-3`). `ThreadingHTTPServer` gives one daemon thread per request (`server.py:1924-1930`). The protocol is pinned to HTTP/1.0, "close per response; avoids keep-alive pitfalls" (`server.py:929`). The one runtime dependency of the package is Pillow (`pyproject.toml:10`), so the server adds no web framework to install. Concurrency uses a custom `_Slots` pool rather than a `Semaphore`, because prefetch must ask "is there room *and* would that leave some for a person?", and a semaphore can't answer that (`server.py:566-573`). **Alternatives considered** (Flask, FastAPI, aiohttp): `UNKNOWN — needs human confirmation`; the stdlib choice is recorded, the comparison isn't. **Load-bearing:** routes, auth, body limits and streaming are all written against `BaseHTTPRequestHandler`; `contracts.md` lists the five places a route is defined.

### Database: N/A (JSON files) ⚓
No database. Transcripts, summaries and segment selections are JSON files with a `saved_at` field, a 14-day TTL and a 500-file cap per directory, written atomically through a `.tmp` file and `replace` (`txcache.py:1-10`, `:21-27`, `:84-86`). The reason they're on disk at all: every restart used to re-fetch every video, and YouTube rate-limits bursts with 429 (`txcache.py:3-6`). Config is `config.json` (`config.py:12`), usage is append-only JSONL (`usage.py:26`). **Alternatives considered** (SQLite or similar): `UNKNOWN — needs human confirmation`. **Load-bearing:** the file formats, read with tolerant defaults, are the schema (`data-model.md`).

### Cache: in-process plus JSON files
Two layers. In memory: dicts under one lock, TTL 4 h, cap 64 entries, lost on restart (`server.py:26-49`). On disk: the `txcache` files above. The extension worker keeps its own maps for the life of the worker (`background.js:25-26`). The rationale for memory-then-disk is recorded at `server.py:26-34` and `txcache.py:1-10`. **Alternatives considered** (Redis, or any external cache): N/A. A single local process has no use for a shared cache, and none is recorded.

### Queue: N/A
No message queue. Background work is threads started in-process: the transcription job (`server.py:288-289`) and the key-moment prefetch (`server.py:519`), gated by the slot pools and a single transcription permit (`server.py:65`, `:557-563`). Long work that must outlive a request (transcription) runs as a job that the request only watches (`server.py:52-63`).

### Authentication provider: N/A (self-issued bearer token) ⚓
`secrets.token_urlsafe(32)`, persisted to `~/.config/youtube-tldw/token` and compared with `hmac.compare_digest` (`server.py:692-708`, `:955-960`). A token is "mandatory to stop drive-by triggering" because any web page can `fetch` a localhost server (`docs/reviews/PLAN-extension.md`, "Security"). The model's login is the `claude` CLI's own and is not tldw's concern (`claude_client.py:3-4`). **Alternatives considered:** that plan names hosted mode as needing "TLS + real per-user auth + Anthropic API billing", deferred to a separate review; it was later dropped in favour of local-only (commit `1dedf94`; ADR 0001). Native messaging as an alternative transport: `UNKNOWN — needs human confirmation`. **Load-bearing:** every POST route and every extension caller depends on it.

### Continuous Integration provider: GitHub Actions
`.github/workflows/checks.yml` runs four required jobs: `procedure checks`, `shell scripts (lint + test)`, `overview deck (build + compare)` and `tldw tests (pytest + extension)`. Actions are pinned by SHA (`checks.yml:42`, `:122`, `:138`, `:194`). `azure-pipelines.yml` mirrors it but no Azure pipeline is connected. Both are kept because five starter suites read the Azure file (ADR 0002). The tldw job's install is hash-locked through `scripts/requirements-ci.txt` (ADR 0002). **Alternatives considered:** the starter supports GitHub Actions or Azure DevOps (`AGENTS.md` § 7); this fork uses Actions.

### Hosting platform: N/A (local-only, by decision) ⚓
`serve` refuses to bind anything but loopback (`server.py:1935-1939`). The README states "local-only by design" (`README.md:433-435`), decided in commit `1dedf94` (2026-06-18): "each user installs locally and runs their own `tldw serve` against their own Claude plan." **Alternatives considered:** a hosted server for trusted users, the original goal in `docs/reviews/PLAN-extension.md` ("hostable later"), removed by that commit. See ADR 0001; ADR 0003 for the security consequence.

### Monitoring stack: N/A
Request lines go to stderr (stdlib `log_message`); progress, failures and per-call token usage go to stdout (`server.py:962-963`, `usage.py:138-144`). `usage.jsonl` holds one row per model call, and `tldw usage` summarizes it (`usage.py:26`, `cli.py:463-467`). There is no metrics service, tracing or alerting. Nothing is hosted, so there is nothing to page anyone about.

### Secret manager: N/A
The token is a 0600 file or `TLDW_TOKEN` / `--token` (`server.py:689-708`, `cli.py:306`, `:603`). The extension keeps a copy in plain text in `chrome.storage.local` (`options.js:176-177`). tldw never reads a keychain; the `claude` CLI's login lives wherever the CLI keeps it. CI has no secrets.

### Package manager: pip / pipx; `uv` for the CI lock
The README's recommended install is `pipx install .` (not editable); a dev checkout uses `pip install -e '.[dev]'` in a venv (`README.md:55-61`, `:437-442`). Optional extras are `[tts]` and `[whisper]`, installed with `pipx inject` (`pyproject.toml:14-15`, `README.md:57`, `:336`). CI installs from a hash-locked `scripts/requirements-ci.txt`, generated by `uv pip compile --generate-hashes` (its header). **`uv.lock` is committed and was updated by each release commit (`2aef5a9`, `73bc1e4`, `6ae0839`), but nothing in CI, the scripts or the READMEs reads it.** Whether it is meant to be used: `UNKNOWN — needs human confirmation`. **Alternatives considered** (poetry and others): `UNKNOWN — needs human confirmation`.

### Model backend: the `claude` CLI, or any command ⚓
The default is `claude -p` on the user's own Claude plan (`claude_client.py:1-10`, `:189-213`). The reasons it is a CLI rather than an API client are in ADR 0001. An operator can set `TLDW_LLM_CMD` or `tldw config set llm-cmd …` to any command that reads a prompt on stdin and prints text, "e.g. the `llm` CLI or `ollama run <model>`" (`claude_client.py:5-7`). **Load-bearing, and the main vendor lock-in.** Features that work only with the `claude` CLI: the model picker (`backend_info`, `claude_client.py:238-248`), token-level streaming via stream-json, Q&A session resume, the usage ledger, and the lean no-tools invocation (`claude_client.py:100-111`, `:304-495`). Custom backends get stdout streaming and buffered JSON (`claude_client.py:272-291`). **Exit strategy** (the Architect's lock-in redline): the `TLDW_LLM_CMD` seam already exists and is exercised by the code path at `claude_client.py:210-212`. What is lost on exit is the list above.

### Media tooling: `yt-dlp` ⚓, `ffmpeg`, `ffprobe`
External programs on `PATH`, not Python dependencies (`README.md:43-45`), checked by `proc.require` for the CLI (`cli.py:280-283`). `yt-dlp` does YouTube extraction: metadata, subtitles, audio and video (`metadata.py`, `whisper_transcribe.py:102-133`). It is load-bearing because YouTube changes often and `yt-dlp` tracks those changes. tldw reduces how often it calls it: the page supplies the transcript (`server.py:785-797`), and captions are fetched directly once `yt-dlp` has given the URL (`metadata.py:157-163`). `ffmpeg` encodes speech to mp3 as a streaming filter (`audio.py:274-278`) and does all video-mode cutting (`videomode.py`); `ffprobe` measures rendered duration (`videomode.py:101`). **Alternatives considered:** a direct caption GET was chosen over a second `yt-dlp` run, because that extraction is "what YouTube fingerprints" (`metadata.py:157-163`). Alternatives to `yt-dlp` itself: `UNKNOWN — needs human confirmation`.

### Speech: Piper (optional)
"local neural voices" (`pyproject.toml:14`). 13 allowlisted voices, downloaded on first use (`audio.py:41-61`, `:231-262`). Piper's PCM is piped into a long-lived `ffmpeg` so the first audio plays in about a second (`README.md:168-170`, `audio.py:281-321`). Piper has no Windows-on-ARM wheel, and the code says so (`audio.py:208-217`). **Alternatives considered** (cloud TTS, OS voices): `UNKNOWN — needs human confirmation`.

### Transcription: faster-whisper (optional)
For videos with no captions. "4–8× faster than openai-whisper" (`whisper_transcribe.py:1-7`); CPU, `int8`, half the cores (`whisper_transcribe.py:202-210`). **Alternatives considered:** openai-whisper, rejected on speed (`whisper_transcribe.py:3`). The model sizes quoted to the user are faster-whisper's, measured on disk (`whisper_transcribe.py:29-38`).

### Imaging: Pillow
The package's one required dependency, `pillow>=10.3,<12` (`pyproject.toml:10`), used for video-mode overlays (`overlays.py`). **Alternatives considered:** `UNKNOWN — needs human confirmation`.

### Test runners: pytest, and node `vm` harnesses
`pytest>=8` (`pyproject.toml:13`). Thread exceptions are turned into errors so a failing assertion inside a handler thread can't pass silently (`pyproject.toml:30-32`). The extension's HTML-building functions run under node, cut out of the real source by literal markers (`tests/js/render_checks.js:16`), driven from `tests/test_extension_render.py`. There is no browser end-to-end tool. `testing.md` covers the levels.

### Build backend: setuptools
`setuptools>=68` (`pyproject.toml:1-3`); CI pins `>=70.1` in its lock (`scripts/requirements-ci.txt` header). The console script is `tldw = youtube_tldw.cli:main` (`pyproject.toml:17-18`).

## License posture

tldw itself has **no license file**. The starter's MIT license was deliberately not adopted, because adding it would relicense tldw (ADR 0002, `tasks/T-003.md`). Dependency licenses below come from installed package metadata (`*.dist-info/METADATA`) or, for the external tools, from Homebrew's formula metadata on the drafting machine. Builds from other sources may differ.

| Dependency | How tldw uses it | License | Source of the fact |
|---|---|---|---|
| Pillow 11.3.0 | Imported; required | MIT-CMU | dist-info `License-Expression` |
| piper-tts 1.4.2 | Imported (`from piper import …`); optional `[tts]` | **GPL-3.0-or-later** | dist-info `License` |
| faster-whisper 1.2.1 | Imported; optional `[whisper]` | MIT | dist-info `License` |
| ctranslate2 4.8.0 (via faster-whisper) | Transitive | MIT | dist-info |
| onnxruntime 1.27.0 (via piper-tts) | Transitive | MIT | dist-info |
| huggingface_hub 1.21.0 (via faster-whisper) | Transitive | Apache-2.0 | dist-info |
| av (via faster-whisper) | Transitive | BSD-3-Clause | dist-info |
| pytest | Dev only | MIT | dist-info |
| setuptools | Build only | `UNKNOWN — needs human confirmation` | not checked |
| `yt-dlp` | External program on `PATH`, run as a subprocess | Unlicense | Homebrew formula metadata |
| `ffmpeg` / `ffprobe` | External programs, run as subprocesses | GPL-3.0-or-later (the Homebrew build); depends on how it was built | Homebrew formula metadata |
| `claude` CLI | External program, run as a subprocess | `UNKNOWN — needs human confirmation` (Anthropic's terms) | not checked |
| Piper voice models (13, `audio.py:44-58`) | Downloaded data, loaded by Piper | `UNKNOWN — needs human confirmation`; per-voice licenses were not checked | not checked |
| faster-whisper model weights (`models--Systran--faster-whisper-*`, `hygiene.py:234`) | Downloaded data | `UNKNOWN — needs human confirmation` | not checked |
| Other transitive packages in `uv.lock` | Transitive | `UNKNOWN — needs human confirmation`; not audited | not checked |

**Posture, as facts:**
- piper-tts is GPL-3.0-or-later and is imported in-process by an optional extra, while tldw has no license of its own. The license is **accepted for piper-tts — ADR 0004** (`decisions/0004-accept-gpl-tts-dependency.md`), for 1.4.2 as the optional `[tts]` extra only. What that means for distributing tldw: still `UNKNOWN — needs human confirmation`; ADR 0004 doesn't settle it.
- `ffmpeg` and `yt-dlp` are run as separate programs, never linked.
- There is no SBOM, no license scan and no dependency-vulnerability scan in CI. The only supply-chain controls are the hash-locked CI install (`scripts/requirements-ci.txt`) and SHA-pinned actions.
- Whether any dependency has a known critical vulnerability (CVSS ≥ 9.0): `UNKNOWN — needs human confirmation`. Nothing checks for one.

## How this was verified

- Read `pyproject.toml`, `scripts/requirements-ci.txt`'s header, the `uv.lock` package list, `.github/workflows/checks.yml` job names and pinned actions, and `README.md` install sections.
- Every "why" is quoted from a code comment, a commit message (`git show 1dedf94`, `git log --diff-filter=A`), or `docs/reviews/PLAN-extension.md`, cited at each entry. Where none exists the entry says `UNKNOWN`.
- `git log -- uv.lock` and a grep of `scripts/`, `.github/`, both READMEs, `AGENTS.md` and `CLAUDE.md` for `uv.lock` (no references).
- Licenses: read `License` / `License-Expression` from `*.dist-info/METADATA` in the project venvs; `brew info --json=v2 yt-dlp ffmpeg` for the two external tools.

## Update this doc when

- `pyproject.toml` (dependencies, extras, `requires-python`, build backend), `uv.lock` or `scripts/requirements-ci.txt` change.
- A new external program, Python dependency, model, voice or network service is added; `audio.VOICES` or `whisper_transcribe.VALID_MODELS` change.
- `.github/workflows/checks.yml` or `azure-pipelines.yml` change jobs or providers.
- `server.py` stops being stdlib-only, or `serve` stops refusing non-loopback hosts.
- `claude_client.py` changes what only the `claude` backend supports.
- The extension gains a framework, bundler or build step, or moves off MV3.
- A license file is added to tldw.
