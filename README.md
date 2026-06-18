# youtube-tldw

Turn a YouTube video into a succinct **TL;DW** — either as Markdown text or as a
recut MP4 of just the key moments — using the **`claude` CLI** (works with any Claude
plan — Pro/Max/Team — or an Anthropic API key; no per-token billing on a subscription).
You can also point it at **another model** (OpenAI, Gemini, local/Ollama) — see
[Using other models](#using-other-models).

## How it works

1. Validates the URL and pulls metadata + the best subtitle track with `yt-dlp`.
2. Parses and de-duplicates the transcript (handles YouTube auto-caption
   "rolling" cues).
3. Sends it to Claude via headless `claude -p`, which decides how aggressively to
   compress based on the content.
4. **text mode** → prints the TL;DW and saves a `.md`.
   **video mode** → Claude picks the key cue ranges, `ffmpeg` cuts them from the
   source and stitches them with crossfades (a deliberate visual signal that
   content was skipped), and saves an `.mp4`.

## Requirements

- **Python 3.11+**
- **`claude` CLI** — logged into any Claude plan (Pro/Max/Team) or with
  `ANTHROPIC_API_KEY` set. Install from
  [claude.com/claude-code](https://www.claude.com/product/claude-code) (or
  `npm i -g @anthropic-ai/claude-code`), then run `claude` once to log in.
  *(Or use a different model via [Using other models](#using-other-models) — then
  `claude` isn't needed.)*
- **`yt-dlp`** and **`ffmpeg`/`ffprobe`** on your `PATH`:
  - macOS: `brew install yt-dlp ffmpeg`
  - Linux: e.g. `sudo apt install ffmpeg` + `pipx install yt-dlp`

Developed and tested on macOS; Linux should work the same. (The browser extension is
Chromium-based — see [Browser extension](#browser-extension-text-only).)

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

**Browser support:** it's a Chromium MV3 extension — works in **Chrome, Edge, Brave,
Arc, Opera, Vivaldi** (load unpacked via `chrome://extensions` → Developer mode). Firefox
and Safari aren't supported yet (Firefox needs a small manifest port; Safari needs an
Xcode wrapper).

The server is loopback-only, token-protected, and accepts only `chrome-extension://`
origins; nothing leaves your machine beyond the usual `yt-dlp` + `claude` calls. This is
**local-only by design** — to share it, others install it on their own machine and use
their own Claude plan (no hosted/shared server).

## Using other models

By default the summarizer shells out to the `claude` CLI (whatever it's logged into:
Claude Pro/Max/Team, or `ANTHROPIC_API_KEY`). To use a different model, set a backend
command that **reads the prompt on stdin and prints the model's text on stdout**:

```bash
# any run, or `tldw serve`, accepts --llm-cmd (or set TLDW_LLM_CMD)
tldw <url> --mode text --llm-cmd "llm -m gpt-4o"        # Simon Willison's llm CLI
tldw <url> --mode text --llm-cmd "ollama run llama3.1"  # fully local
TLDW_LLM_CMD="llm -m gemini-1.5-pro" tldw serve
```

The backend is **operator config only** (never set by an extension/HTTP request).
Text summaries port cleanly to most models; the **video** cue-selection needs reliable
structured JSON, where smaller/local models may be less consistent than Claude.

## Development

```bash
pip install -e ".[dev]"
pytest
```

All external tools (`claude`, `yt-dlp`, `ffmpeg`) run through a single
`subprocess` chokepoint with `shell=False`; untrusted data (titles, transcripts,
URLs) only ever travels as discrete argv elements or on stdin. See
`docs/reviews/` for the design + review history.
