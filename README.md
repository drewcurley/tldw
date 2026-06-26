# youtube-tldw

Turn a YouTube video into a succinct **TL;DW** — either as Markdown text or as a
recut MP4 of just the key moments.

**Use any model you like** → [Choose your model](#choose-your-model):

- **Local** (Ollama / any local server) — fully offline, no keys, no cost
- **OpenAI / Gemini / Mistral / …** via one setting
- **Claude** via the `claude` CLI (any Claude plan or API key) — the zero-config default

## How it works

1. Validates the URL and pulls metadata + the best subtitle track with `yt-dlp`.
2. Parses and de-duplicates the transcript (handles YouTube auto-caption
   "rolling" cues).
3. Sends it to **your model**, which decides how aggressively to compress.
4. **text mode** → prints the TL;DW and saves a `.md`.
   **video mode** → the model picks the key cue ranges, `ffmpeg` cuts them from the
   source and stitches them with crossfades (a deliberate visual signal that
   content was skipped), and saves an `.mp4`.

## Requirements

- **Python 3.11+**
- **A model backend** — see [Choose your model](#choose-your-model). The simplest are
  a local model (Ollama) or any provider via the `llm` CLI; Claude is the default if
  you have the `claude` CLI.
- **`yt-dlp`** and **`ffmpeg`/`ffprobe`** on your `PATH`:
  - macOS: `brew install yt-dlp ffmpeg`
  - Linux: e.g. `sudo apt install ffmpeg` + `pipx install yt-dlp`

Developed and tested on macOS; Linux should work the same. (The browser extension is
Chromium/Firefox — see [Browser extension](#browser-extension).)

## Install

```bash
git clone https://github.com/drewcurley/tldw.git
cd tldw

# Recommended: a global `tldw` command (also what the browser extension's server uses)
pipx install .
pipx inject youtube-tldw piper-tts      # optional: text-to-speech voices

# …or a local virtualenv instead of pipx:
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[tts]"
```

## Choose your model

The summarizer just shells out to a command that **reads the prompt on stdin and prints
the model's text on stdout** — so you can point it at anything. Set it once with
`tldw config set llm-cmd "<command>"` (persists), or per run with `--llm-cmd`, or via the
`TLDW_LLM_CMD` env var.

**Local — fully offline, no keys, no cost** ([Ollama](https://ollama.com)):
```bash
ollama pull llama3.1
tldw config set llm-cmd "ollama run llama3.1"
```

**OpenAI / Gemini / Mistral / Anthropic / 100+ others** via the
[`llm`](https://llm.datasette.io) CLI:
```bash
pipx install llm
llm keys set openai                       # paste your key (or: llm install llm-gemini && llm keys set gemini)
tldw config set llm-cmd "llm -m gpt-4o"   # or "llm -m gemini-1.5-pro", "llm -m mistral-large", …
```

**Claude (default)** — if the [`claude` CLI](https://www.claude.com/product/claude-code)
is installed and logged in (any Claude plan, or `ANTHROPIC_API_KEY`), it's used
automatically with no config. `npm i -g @anthropic-ai/claude-code` then run `claude` once
to log in.

```bash
tldw config get        # shows the effective model
```

Resolution order: `--llm-cmd` > `TLDW_LLM_CMD` > `tldw config` > the `claude` CLI. Text
summaries work well on most models; the **video** cue-selection needs reliable structured
JSON, where larger models (Claude, GPT-4o, etc.) are more consistent than small local ones.
The backend is **operator config only** — never set by the browser extension / an HTTP request.

## Usage

```bash
# Text TL;DW (AI decides the length)
tldw "https://youtu.be/VIDEO_ID" --mode text

# Video TL;DW, capped at 5 minutes, with burned-in captions
tldw "https://youtu.be/VIDEO_ID" --mode video --max-length 5m --burn-captions

# Force a target compression and keep the original download
tldw "https://youtu.be/VIDEO_ID" --mode video --ratio 0.2 --keep-source
```

### Options

| Flag | Description |
|------|-------------|
| `--mode {text,video}` | Defaults to `video`. |
| `--render-audio` | Also save an mp3: video → the recut audio; text → spoken summary (TTS). |
| `--voice VOICE` | Text-mode TTS voice: `female`/`male` or a named US/UK voice (`amy`, `ryan`, `cori`, `alan`, …). |
| `--ratio FLOAT` | Target fraction of original length (`0 < r <= 1`). Omit to let the AI choose. |
| `--max-length DUR` | Hard cap (`5m`, `90s`, `1m30s`). **Wins over `--ratio`.** |
| `--lang CODE` | Preferred subtitle language (default `en`). |
| `--burn-captions` | (video) burn recut-aligned captions into the output. |
| `--keep-source` | (video) keep the full downloaded source too. |
| `--keep-intro N` | (video) keep the first N seconds as an intro (default 6s). |
| `--no-intro` | (video) don't preserve the intro. |
| `--no-badge` | (video) no "TL;DW" corner badge. |
| `--no-banner` | (video) no "TL;DW version" intro banner. |
| `--no-end-card` | (video) no fade-to-black "Made with youtube-tldw" end card. |
| `--output-dir PATH` | Base output dir (default `~/Downloads/youtube-tldw/tldws`). |

### Video polish (on by default)

Video TL;DWs are "finished" automatically so they don't look abruptly chopped:
- **Intro preserved** — the first ~6s of the source is kept as the opener.
- **TL;DW marks** — a "TL;DW version" banner over the intro + a persistent "TL;DW"
  corner badge for the rest.
- **Graceful ending** — the last segment fades to black, then a "Made with
  youtube-tldw" end card fades in/out.

Disable any piece with the `--no-*` flags above. Text is rendered with Pillow and
composited by ffmpeg (works even without libass/libfreetype). Note: `--max-length`
caps the key segments; the intro and end card are additive, so the final file can be
a few seconds longer (the printed filename always reflects the true rendered length).

The URL argument also accepts a **bare 11-character video id** (e.g. `tldw 86QbFlOHuTs`).

## Audio (`--render-audio`)

- **video mode** → extracts the recut video's audio to an mp3.
- **text mode** → synthesizes the summary to natural speech using **Piper** (local
  neural TTS, no API keys). 13 US/UK voices (`--voice female|male` or a named voice
  like `amy`/`ryan`/`cori`/`alan`). Voice models download once on first use into
  `~/.cache/youtube-tldw/voices/`.

Text-mode TTS needs Piper installed: `pipx inject youtube-tldw piper-tts`
(or `pip install -e ".[tts]"` for a dev checkout).

## Output

```
~/Downloads/youtube-tldw/tldws/
  text/   {channel} - {video} - tl;dw - {read-time}.md
  video/  {channel} - {video} - tl;dw - {length}.mp4
  audio/  {channel} - {video} - tl;dw - {length}.mp3
```

Set a persistent **render folder** so saved files go where you want (no `--output-dir`
each run):

```bash
tldw config set output-dir ~/Media/tldw     # persists to ~/.config/youtube-tldw/config.json
tldw config get                             # show config + the effective output dir
tldw config unset output-dir                # back to the default
```

Precedence: `--output-dir` (per run) > `TLDW_OUTPUT_DIR` (env) > `tldw config` > default.

## Captionless videos (Whisper fallback)

If a video has no subtitles or auto-captions, tldw normally exits with an error.
Add `--whisper-fallback` to download the audio instead and transcribe it locally
with [faster-whisper](https://github.com/SYSTRAN/faster-whisper):

```bash
tldw "https://youtu.be/VIDEO_ID" --whisper-fallback
```

Install faster-whisper once:
```bash
pipx inject youtube-tldw faster-whisper   # if using pipx
pip install faster-whisper                # if using a venv
# or: pip install -e ".[whisper]"         # dev checkout
```

The model downloads once to `~/.cache/youtube-tldw/whisper/` on first use.
Default model is `small` (~244 MB, good accuracy/speed). Change it with:

```bash
tldw config set whisper-model small    # tiny/base/small/medium/large
```

| Model | Size | Speed (relative) | Notes |
|-------|------|-----------------|-------|
| tiny | 75 MB | fastest | usable for simple speech |
| base | 145 MB | fast | |
| small | **244 MB** | **default** | good balance |
| medium | 769 MB | slower | noticeably better accuracy |
| large | 1.5 GB | slowest | best accuracy |

Transcription typically takes 0.5–2× the video length on CPU. The browser
extension does not use this fallback (requests would time out for long videos);
use the CLI instead.

## Browser extension

One toolbar click on a YouTube page summarizes the video in an on-page modal, and from
there you can also **🔊 Listen** (text-to-speech of the summary) or **⏭ Play key
moments** (auto-skip the real YouTube player through just the key segments). Powered by
a small local server:

```bash
tldw serve            # prints a bearer token (saved + reused); binds 127.0.0.1:8765
```

Then load `extension/` unpacked and paste the token into the extension options — full
steps in [`extension/README.md`](extension/README.md).

**Browser support:** MV3 extension for **Chrome, Edge, Brave, Arc, Opera, Vivaldi**
(load `extension/` unpacked) and **Firefox** (run `extension/build-firefox.sh`, then
load via `about:debugging`). Safari isn't supported (it needs an Xcode wrapper).

The server is loopback-only, token-protected, and accepts only `chrome-extension://`
origins; nothing leaves your machine beyond the usual `yt-dlp` + model calls. This is
**local-only by design** — to share it, others install it on their own machine and use
their own model (no hosted/shared server).

## Development

```bash
pip install -e ".[dev]"
pytest
```

All external tools (the model backend, `yt-dlp`, `ffmpeg`) run through a single
`subprocess` chokepoint with `shell=False`; untrusted data (titles, transcripts,
URLs) only ever travels as discrete argv elements or on stdin. The model backend
command is operator config, never request-controlled. See `docs/reviews/` for the
design + review history.
