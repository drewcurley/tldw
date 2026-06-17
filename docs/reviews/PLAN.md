# youtube-tldr — Implementation Plan

## Goal
A personal CLI that takes a YouTube URL, uses the user's Claude Max subscription
(via headless `claude -p`) to analyze the transcript, and produces a TL;DR in one
of two modes:
- **text**: print the TL;DR and save a Markdown file.
- **video**: download with yt-dlp, have Claude select the key transcript spans,
  recut the source with ffmpeg into a shorter video with crossfades between clips,
  save an MP4.

## Locked requirements
- Claude access: headless `claude -p` (Max subscription, no API keys).
- Interface: CLI with flags. `tldr <url> --mode text|video [options]`.
- Compression: **AI-determined dynamically** from content density; `--ratio FLOAT`
  override; `--max-length` duration cap.
- text mode: print + save `.md`.
- video mode: transcript-with-timestamps → Claude picks spans → ffmpeg cut + concat
  with **crossfades** (explicit visible cuts).
- Captions: `--burn-captions` optional.
- Output dir: `~/Downloads/youtube-tldr/tldrs/{video|text}/`.
- Naming: `{channel} - {video} - tl;dr - {new_length}.{md|mp4}` (sanitized).
- Defaults: delete downloaded source after recut (`--keep-source` to retain);
  abort cleanly when no transcript; `{new_length}` formatted like `3m42s`.

## Architecture (Python, single package)
```
youtube_tldr/
  __init__.py
  cli.py            # argparse, flag wiring, orchestration
  metadata.py       # yt-dlp: fetch channel/title/duration, pick subtitle track
  transcript.py     # download + parse subs (vtt/srt) -> timestamped segments
  summarize.py      # build prompts, call `claude -p`, parse JSON responses
  textmode.py       # render markdown, write .md
  videomode.py      # download video, ffmpeg cut+crossfade concat, write .mp4
  naming.py         # sanitize, format length, build output paths
  claude_client.py  # subprocess wrapper around `claude -p --output-format json`
tests/              # pytest, ffmpeg/claude/yt-dlp mocked
```

## Key flows
### Common
1. Parse URL + flags.
2. yt-dlp: fetch metadata (channel, title, duration) and available subtitle tracks.
3. Get transcript: prefer manual subs, fall back to auto-captions; parse to
   `[{start, end, text}]`. Abort with clear message if none.

### Text mode
4. Prompt Claude with full transcript text. Claude returns JSON:
   `{recommended_summary, key_points[], target_ratio_used}`. `--ratio` is injected
   into the prompt as a hard constraint when provided.
5. Render Markdown (title, source URL, channel, key points, summary). Print + save.

### Video mode
4. Prompt Claude with the **timestamped** transcript. Claude returns JSON:
   `{segments: [{start, end, reason}], estimated_length}`. Constraints in prompt:
   honor `--ratio` if set, never exceed `--max-length`, min clip length to avoid
   choppiness, keep segments in chronological order.
5. yt-dlp downloads the video (chosen format/resolution; merged mp4).
6. ffmpeg: trim each span, apply `xfade` (video) + `acrossfade` (audio) between
   consecutive clips; optional `--burn-captions` via subtitles filter.
7. Write MP4 to output path. Delete source unless `--keep-source`.

## CLI surface
```
tldr <url>
  --mode {text,video}     (required)
  --ratio FLOAT           (optional; 0 < r <= 1; e.g. 0.25 = ~25% of original)
  --max-length DURATION   (optional; e.g. 5m, 90s; HARD cap, wins over --ratio)
  --lang CODE             (optional; preferred subtitle language, default en)
  --burn-captions         (video only; default off)
  --keep-source           (video only; default off)
  --output-dir PATH       (optional; default ~/Downloads/youtube-tldr/tldrs;
                           {video|text} subdirs are ALWAYS appended under it)
```
(`--model` cut from v1 — scope; the Max default model is used.)

## Risks / open technical questions for review
- **Claude output reliability**: parsing JSON from `claude -p`. Mitigate with
  `--output-format json` and a strict response schema + one repair retry.
- **Transcript size vs context**: very long videos. Mitigate: chunked map-reduce
  summarization if transcript exceeds a token threshold.
- **Crossfade mechanics**: `xfade` requires overlapping offsets; need correct
  per-clip duration math. Risk of A/V desync — validate with a test asset.
- **Timestamp accuracy**: auto-caption timings can be loose; pad span boundaries.
- **Filesystem safety**: sanitize channel/title (slashes, emojis, length caps).
- **Cost/latency**: large videos = large downloads; respect `--max-length` early.

## Round 1 review resolutions (authoritative — override anything above on conflict)

### Security (Architect blockers)
- **B1 — argv only.** All subprocesses run via a single chokepoint `proc.py`
  (`run(argv: list, *, stdin=None, timeout)`), `subprocess.run([...], shell=False)`.
  No `shell=True`, no f-string command lines. `metadata.py`, `transcript.py`,
  `videomode.py`, `claude_client.py` all go through it. Untrusted strings
  (title/channel/URL/transcript) are only ever discrete argv elements or stdin.
- **B2 — transcript via stdin.** Prompt template is static; transcript + data are
  piped on stdin (`input=`). Nothing large or untrusted touches argv.
- **B3 — path containment.** `naming.py`: strip path separators + control/NUL
  chars, decode then drop emojis/non-printables, reject `.`/`..`, collapse
  whitespace, cap to a **byte** budget (≤255 incl. extension). After building the
  path, `Path.resolve()` and assert `is_relative_to(output_dir.resolve())` before
  any write. Separators in the template (` - `, `tl;dr`): sanitizer strips/escapes
  ` - ` and `;` from channel/title so the template stays parseable. Hostile-title
  unit tests required.
- **B4 — URL allowlist.** Validate scheme `https`, host in
  {youtube.com, www.youtube.com, m.youtube.com, youtu.be}; extract canonical
  11-char video ID; reject playlists/other extractors/local paths. yt-dlp `-o`
  template and format selectors are static constants, never built from input.
- **B5 — claude contract.** `claude_client.py`: explicit `timeout=`; defined clean
  aborts (actionable message, no partial files written) on non-zero exit, empty
  stdout, timeout, and not-logged-in. Exactly one bounded repair retry on bad JSON.
  Validate parsed JSON against schema: spans `start<end`, within `[0,duration]`,
  chronological after sort/merge — reject and retry once, then abort.

### Data fidelity (Data Engineer blockers + warnings)
- **D1 — rolling-cue de-dup.** `transcript.py` explicitly: strip inline `<c>`/timing
  tags + VTT cue-setting headers, HTML-entity decode, collapse YouTube "paint-on"
  rolling cues to one logical non-overlapping line per utterance, join multi-line
  cues with a space. Tested against a REAL auto-caption VTT fixture (rolling cues +
  `<c>` tags), not just clean SRT.
- **D2 — authoritative duration formula.** With N clips and crossfade `d`:
  `final = Σ(clip_i) − (N−1)·d`. This single value drives the `{new_length}`
  filename, the `--max-length` clamp, and ratio accounting — never Claude's
  estimate. For video, `{new_length}` is measured/derived from the actual rendered
  output (ffprobe the result). Real-asset test asserts output duration within
  tolerance and A/V sync.
- **Time unit.** Parse VTT (`HH:MM:SS.mmm` / optional-hours `MM:SS.mmm`, dot) and
  SRT (`,mmm` comma) into a single internal unit (**int milliseconds**) end-to-end;
  convert to ffmpeg `HH:MM:SS.mmm` only at command construction.
- **Span hygiene.** Snap Claude `start` down to enclosing cue start, `end` up to
  enclosing cue end (avoid mid-word cuts); then pad → clamp to `[0,duration]` →
  sort → merge overlapping/adjacent spans → drop zero/negative. This order runs
  before any crossfade offset math so fades never invert.
- **Track selection.** Deterministic precedence: manual in `--lang` > manual any >
  auto in `--lang` > auto translated; log which track was chosen.
- **xfade math.** offset recurrence is cumulative; N clips → N−1 fades; `acrossfade`
  mirrors `xfade` exactly to prevent drift.

### Scope / acceptance (Analyst items)
- **Crossfade intent reconciled.** Clips are joined with crossfade transitions
  (NOT hard cuts); the crossfade is the deliberate visual signal that content was
  skipped between key moments. Wording in requirements corrected accordingly.
- **Enforcement precedence.** `--max-length` is a hard cap and wins over `--ratio`;
  `--ratio` is the target. Both enforced deterministically against measured span
  durations (post-crossfade), not self-reported by Claude.
- **Text `{new_length}`.** = estimated read time at 200 wpm, formatted like `3m`
  (mirrors video `3m42s` style).
- **Long transcripts.** Map-reduce ONLY past a defined token threshold. Text mode:
  chunk-summarize then reduce. Video mode: chunks carry global ms timestamps; spans
  merged/de-duped in the global timeline. A defensive upper bound caps transcript
  size sent to Claude regardless.

### Ops hygiene (Architect suggestions)
- Startup preflight: verify `claude`, `yt-dlp`, `ffmpeg` present (clear error if not).
- Work in a tool-owned tempdir; track created files by absolute path; delete only
  those; `try/finally` cleanup on failure. Source deletion never uses globs.
- `cli.py` validates `0 < ratio <= 1` and `max-length > 0`, fails fast.
- Never log full transcript or full command lines at info level.

## Testing
- Unit: naming/sanitization incl. **hostile titles** (path traversal, NUL, emojis,
  ` - `/`;` collisions, byte-length cap) + path-containment assertion; duration
  parsing; VTT+SRT parsing incl. **real auto-caption rolling-cue de-dup fixture**;
  span hygiene (snap/pad/clamp/sort/merge ordering); the `final = Σclip−(N−1)d`
  duration formula; URL allowlist (valid/invalid/playlist/local-path); prompt
  building; Claude JSON parse + schema validation + malformed-repair path;
  claude_client failure contract (non-zero/empty/timeout/not-logged-in);
  ffmpeg xfade/acrossfade command construction.
- Integration (mocked yt-dlp/claude/ffmpeg): full text + video orchestration;
  **no-transcript clean abort**; `--burn-captions` path; `--max-length` wins over
  `--ratio`; source deleted vs `--keep-source` retained.
- Optional smoke test behind a flag using a tiny real public-domain clip: asserts
  rendered output duration within tolerance + A/V sync.
```
