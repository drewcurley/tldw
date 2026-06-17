# Plan — Video Polish (intro / badge / fade-to-black end card)

## Goal
Make video-mode TL;DWs look intentional, not abruptly chopped:
1. **Keep intro** — prepend the source's first N seconds (default 6s).
2. **Mark as TL;DW** — full "TL;DW version" banner during the intro + a persistent
   "TL;DW" corner badge for the rest.
3. **Graceful ending** — fade the last segment to black, then a ~2.5s
   "Made with youtube-tldw" end card that fades in/out.

All ON by default in video mode; `--no-intro`, `--no-badge`, `--no-end-card` to
disable. `--keep-intro N` overrides the intro length.

## Why PNG overlays (not ffmpeg drawtext)
This ffmpeg has no libfreetype/libass, so text is rendered to PNGs with **Pillow**
(new dep) using a system font (Helvetica/Arial) with a `load_default()` fallback,
then composited with ffmpeg `overlay`/`fade`/`xfade` (all present). Overlay text is
FIXED strings (no untrusted input), and PNGs are referenced by fixed basename via
`cwd` — same anti-injection pattern as captions.

## New module: overlays.py
- `render_corner_badge(path, text="TL;DW")` — small rounded translucent badge PNG.
- `render_intro_banner(path, text="TL;DW version")` — lower-third banner PNG (RGBA).
- `render_end_card(path, text="Made with youtube-tldw")` — full 1280x720 black PNG.
- Font loader: try `/System/Library/Fonts/Supplemental/Arial.ttf` then
  `/System/Library/Fonts/Helvetica.ttc`, else `ImageFont.load_default()`.
- All canvases sized to the pipeline's 1280x720 / RGBA.

## Spans assembly (cli + spans.py)
- Build key-segment spans (existing). Enforce `--max-length` on key segments.
- If keep-intro: prepend `Span(0, intro_ms)`, then re-sort + merge overlaps (so a
  key segment starting inside the intro merges in). Intro is first → never trimmed.
- Intro clip carries real captions (it's source footage); end card has none.

## Video pipeline (videomode.py)
Inputs: content clips `c0..c(N-1)`, then PNG inputs (badge/banner) and a normalized
**end-card clip** (`-loop 1 -t E endcard.png` + `anullsrc` silent audio, encoded to
the same WxH/fps/SAR/48k as clips so xfade/acrossfade match).

Single filter_complex:
1. Content xfade chain (existing `build_xfade_filter`, transition=fade) → `[vC][aC]`,
   `Lc = Σd − (N−1)·T`. (N==1 → `[vC]=[0:v]`, `[aC]=[0:a]`.)
2. End card via fade-to-black: `[vC][ec:v]xfade=transition=fadeblack:duration=Tb:
   offset=(Lc−Tb)[vCE]`; `[aC][ec:a]acrossfade=d=Tb[aCE]`. `Lce = Lc + E − Tb`.
3. Corner badge over whole video (single-frame input, default `eof_action=repeat`):
   `[vCE][badge]overlay=W-w-24:24[vb]`.
4. Intro banner during intro only:
   `[vb][banner]overlay=(W-w)/2:H-h-48:enable='between(t,0,intro_dur)'[vbn]`.
5. Fade in/out: `[vbn]fade=t=in:st=0:d=Fi,fade=t=out:st=(Lce−Fo):d=Fo[vout]`.
Maps: `[vout]`, `[aCE]`. Defaults: T=0.4s, Tb=0.6s, Fi=0.5s, Fo=0.7s, E=2.5s.

Soft/burned captions: SRT built over the CONTENT clips (incl. intro) with xfade T as
today — the appended end card doesn't shift earlier caption times. Soft track muxed
as an extra input + `-c:s mov_text` (unchanged).

## Authoritative length / filename
`{new_length}` still = ffprobe of the final output (now includes intro + end card).
`--max-length` continues to bound only the key-segment selection; intro/end-card are
additive (documented). Filename remains accurate (probed).

## Flags
```
--keep-intro [N]   default ON, N defaults to 6s ; --no-intro disables
--no-badge         disable TL;DW badge + intro banner
--no-end-card      disable fade-to-black end card
```

## Risks (for review)
- Filtergraph offset math for the fadeblack join and `fade=out` start (`Lce−Fo`);
  must use real (probed) clip durations. Validate with a real render.
- Single-frame PNG overlay persistence (rely on default `eof_action=repeat`).
- N==1 and "all features off" must collapse cleanly (no dangling labels).
- End-card clip params must EXACTLY match content clips or xfade errors.
- Pillow missing freetype → fallback font must still produce a legible card.

## Review resolutions (authoritative — override above on conflict)

### Backend BLOCK → two-pass render (kills B1/B2/B3 timing+drift)
- **Pass 1 (assemble):** content clips → `xfade=fade`(T) chain; final join to the
  normalized **end-card clip** via `xfade=transition=fadeblack`(Tb); `acrossfade`
  mirrors each join; `-shortest` backstop → `assembled.mp4`. Then **ffprobe its true
  duration** `Lprobe`.
- **Pass 2 (decorate):** input `assembled.mp4` (clean PTS from 0) + `badge.png` +
  `banner.png`; overlay badge (whole), overlay banner `enable='between(t,0,intro_s)'`,
  `fade=t=in:st=0:d=Fi`, `fade=t=out:st=Lprobe-Fo:d=Fo`, then `format=yuv420p,setsar=1`
  → final. Soft captions muxed here. No reliance on nominal `Lce`; all `st=` from
  `Lprobe`.
- **B4:** end-card clip built through the SAME normalization as `_extract_clip`
  (`_VF` incl. `format=yuv420p`+`setsar=1`, `-r 30`, `-video_track_timescale 30000`,
  `-ar 48000 -ac 2`); audio = `anullsrc=channel_layout=stereo:sample_rate=48000`,
  `-t E`. Test ffprobes a content clip and the end-card clip and asserts identical
  v/a stream params (so xfade won't error).
- **Generalized chain:** add `spans.build_xfade_chain(durations, joins)` where
  `joins[i]=(duration_ms, transition)`; offsets computed iteratively
  (`L_{i+1}=L_i+d[i+1]-t_i`, `offset_i=L_i-t_i`). Existing `build_xfade_filter`
  (uniform) stays for back-compat.
- **W1:** overlays use single-frame PNG inputs; rely on default `eof_action=repeat`,
  never `shortest=1`. **W4:** with any polish on (default), even N==1 goes through the
  two-pass compositor (never `_encode_single`, which would drop polish). Plain path
  (`--no-intro --no-badge --no-banner --no-end-card`) keeps the existing 1-pass encode.
- **S4:** a real synthetic-render smoke test (content+endcard) asserts probed
  duration within tolerance — substring tests can't catch PTS/timebase faults.

### Architect/UX
- **UX-1:** `--no-intro` and `--keep-intro N` in a mutually-exclusive argparse group.
- **UX-4:** split into `--no-badge` (corner) and `--no-banner` (intro lower-third).
  Banner only renders when an intro is kept.
- **UX-2/UX-3:** stderr notes — a one-line "polish on; disable with --no-…" hint, and
  when intro/end-card push past `--max-length`, print the breakdown.
- **ARCH-1:** pin `pillow>=10.3,<12` in pyproject.
- **ARCH-4/UX-5:** font loader try/except each candidate path → `load_default(size=)`
  fallback; badge+banner get a **semi-opaque rounded backing plate** for guaranteed
  contrast; fallback-font path tested (CI has no macOS fonts) and must yield a legible
  card. **Invariant (code comment):** text → PNG only; never into a filter string.
- **ARCH-3:** badge/banner/endcard.png/endcard.mp4/assembled.mp4/captions.srt all
  referenced by fixed basename via `cwd`; a test asserts filter/input args carry bare
  basenames only.

## Addendum v2 — source URL + per-cut timestamps

### Source URL (on intro banner + end card)
- `overlays.render_intro_banner(path, text, url=None)` and
  `overlays.render_end_card(path, text, url=None)` gain a second, smaller line with
  the canonical watch URL. Band grows to fit two lines. URL is drawn into the PNG
  ONLY (never a filter string); it's built from the validated 11-char id.
- `Polish.source_url: str | None`; cli passes `md.watch_url(video_id)`.

### Per-cut timestamp (fades in/out at each segment start)
- Goal: at the start of each kept segment, briefly show the segment's SOURCE start
  time (e.g. `12:34`) so a cut is visible and referenceable.
- `timing.format_clock(ms)` → `M:SS` or `H:MM:SS`.
- `overlays.render_timestamp(path, text)` → small translucent plate (RGBA), top-left.
- Folded into `_extract_clip` (NO extra encode generation): when a ts_text is given,
  switch from `-vf` to `filter_complex`:
  inputs `-ss start -t dur -i source` and `-loop 1 -t SHOW -i ts_NNNN.png`; graph
  `[0:v]{_VF}[base];[1:v]format=rgba,fade=t=in:st=0:d=FD:alpha=1,
   fade=t=out:st=SHOW-FD:d=FD:alpha=1[ts];[base][ts]overlay=24:24:eof_action=pass[v]`;
  map `[v]` + `0:a` (audio `aresample=48000`). `eof_action=pass` so after SHOW the ts
  vanishes and the base shows through; main(base) sets output length. Defaults
  SHOW=1.8s, FD=0.35s. Shown on every content clip (intro clip → `0:00`).
- `Polish.timestamps: bool = True`; flag `--no-timestamps`.

### Risks for review
- Alpha fade on a looped single-image input; `eof_action=pass` vs `repeat`.
- `-ss`/`-t` as INPUT options on input 0 while input 1 is `-loop 1 -t SHOW`.
- Mixing `filter_complex` (video) with `-af` (audio) + `-map 0:a`.
- Clip shorter than SHOW (min clip 1.2s): ts may not fully fade out — acceptable.

## Tests
- overlays: each render writes a PNG of expected size/mode (Pillow real).
- filtergraph builder: intro+badge+endcard, none, single-clip, captions — assert
  labels/overlay/enable/fade substrings and that maps resolve.
- spans: intro prepend + merge + max-length protection.
- cli: defaults ON; `--no-*` flags flip pieces; mocked recut still saves file.
- real render smoke (optional flag): synthetic clips + endcard → probes within tol.
