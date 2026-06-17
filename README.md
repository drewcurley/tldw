# youtube-tldr

Turn a YouTube video into a succinct **TL;DR** — either as Markdown text or as a
recut MP4 of just the key moments — using your **Claude Max subscription** (no API
keys, no per-token billing).

## How it works

1. Validates the URL and pulls metadata + the best subtitle track with `yt-dlp`.
2. Parses and de-duplicates the transcript (handles YouTube auto-caption
   "rolling" cues).
3. Sends it to Claude via headless `claude -p`, which decides how aggressively to
   compress based on the content.
4. **text mode** → prints the TL;DR and saves a `.md`.
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
# Text TL;DR (AI decides the length)
tldr "https://youtu.be/VIDEO_ID" --mode text

# Video TL;DR, capped at 5 minutes, with burned-in captions
tldr "https://youtu.be/VIDEO_ID" --mode video --max-length 5m --burn-captions

# Force a target compression and keep the original download
tldr "https://youtu.be/VIDEO_ID" --mode video --ratio 0.2 --keep-source
```

### Options

| Flag | Description |
|------|-------------|
| `--mode {text,video}` | Required. |
| `--ratio FLOAT` | Target fraction of original length (`0 < r <= 1`). Omit to let the AI choose. |
| `--max-length DUR` | Hard cap (`5m`, `90s`, `1m30s`). **Wins over `--ratio`.** |
| `--lang CODE` | Preferred subtitle language (default `en`). |
| `--burn-captions` | (video) burn recut-aligned captions into the output. |
| `--keep-source` | (video) keep the full downloaded source too. |
| `--output-dir PATH` | Base output dir (default `~/Downloads/youtube-tldr/tldrs`). |

## Output

```
~/Downloads/youtube-tldr/tldrs/
  text/   {channel} - {video} - tl;dr - {read-time}.md
  video/  {channel} - {video} - tl;dr - {length}.mp4
```

## Development

```bash
pip install -e ".[dev]"
pytest
```

All external tools (`claude`, `yt-dlp`, `ffmpeg`) run through a single
`subprocess` chokepoint with `shell=False`; untrusted data (titles, transcripts,
URLs) only ever travels as discrete argv elements or on stdin. See
`docs/reviews/` for the design + review history.
