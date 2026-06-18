# youtube-tldw

Turn a YouTube video into a succinct **TL;DW** — either as Markdown text or as a
recut MP4 of just the key moments — using your **Claude Max subscription** (no API
keys, no per-token billing).

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

These must be on your `PATH` (all already installed if you use Claude Code + brew):

- `claude` (logged into your Max account)
- `yt-dlp`
- `ffmpeg` / `ffprobe`

## Install

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e .
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
| `--voice {female,male}` | Voice for text-mode TTS (default `female`). |
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
  neural TTS, no API keys). `--voice female|male`. Voice models download once on
  first use into `~/.cache/youtube-tldw/voices/`.

Text-mode TTS needs Piper installed: `pipx inject youtube-tldw piper-tts`
(or `pip install -e ".[tts]"` for a dev checkout).

## Output

```
~/Downloads/youtube-tldw/tldws/
  text/   {channel} - {video} - tl;dw - {read-time}.md
  video/  {channel} - {video} - tl;dw - {length}.mp4
  audio/  {channel} - {video} - tl;dw - {length}.mp3
```

## Browser extension (text only)

Summarize the video you're watching with one toolbar click — the summary appears in
a modal on the page, powered by a small local server:

```bash
tldw serve            # prints a bearer token; binds 127.0.0.1:8765
```

Then load `extension/` unpacked in `chrome://extensions` and paste the token into the
extension options. See [`extension/README.md`](extension/README.md). The server is
loopback-only, token-protected, and accepts only `chrome-extension://` origins;
nothing leaves your machine beyond the usual `yt-dlp` + `claude` calls. This is
**local-only by design** — to share it, others install it on their own machine and
use their own Claude plan (no hosted/shared server).

## Development

```bash
pip install -e ".[dev]"
pytest
```

All external tools (`claude`, `yt-dlp`, `ffmpeg`) run through a single
`subprocess` chokepoint with `shell=False`; untrusted data (titles, transcripts,
URLs) only ever travels as discrete argv elements or on stdin. See
`docs/reviews/` for the design + review history.
