# Review Artifact — youtube-tldr (feat/youtube-tldr)

## Status: PASS WITH ITEMS (all blockers resolved; non-blocking items noted)

A CLI that turns a YouTube URL into a text or video TL;DR using the Claude Max
subscription (`claude -p`). Full design + review history in `PLAN.md`.

## Review cycle

### Round 1 — Analysis (on the plan)
- **Analyst:** PASS WITH ITEMS — crossfade wording reconciled; ratio/max-length
  precedence pinned; `{new_length}` defined for both modes (video = real rendered
  length; text = read-time).
- **Architect:** BLOCK → resolved — argv-only subprocess chokepoint, transcript via
  stdin, path containment, URL allowlist, claude failure/timeout/schema contract.
- **Data Engineer:** BLOCK → resolved — auto-caption rolling-cue de-dup, authoritative
  crossfade-aware duration formula, ms-only time unit, cue-boundary snapping (via
  cue-index selection), track-selection precedence.

### Round 2/3 — Verification (on the built code)
- **Backend:** PASS WITH ITEMS — ffmpeg crossfade math verified correct. Fixed:
  `--ratio` now a deterministic cap (was per-chunk only); chunked selection clamps
  indices to its window; download globs prefer exact/largest file.
- **SDET:** PASS WITH ITEMS — closed coverage holes: added `test_metadata`,
  `test_proc`, `test_textmode`, `recut()` orchestration, chunked-selection clamp,
  keep-source branch; tightened span clamp / enforce-max-length assertions.
- **Architect (security re-verify):** PASS WITH ITEMS — all 5 controls confirmed in
  code. Fixed the latent ffmpeg `subtitles=` filter-injection by referencing the
  caption file via fixed basename + `cwd`.

## Blockers
None outstanding. All Round-1 BLOCK items resolved in `PLAN.md` and implemented.

## Warnings (addressed)
- `--ratio` deterministic enforcement — fixed (`cli._ratio_cap`).
- Out-of-window cue indices in chunked mode — fixed (validator clamps to window).
- yt-dlp file selection fragility — fixed (exact/largest preference).
- ffmpeg subtitles filter escaping — fixed (safe basename + cwd).

## Suggestions (non-blocking, deferred)
- Chunked map-reduce only triggers past 350k chars (multi-hour videos); single-pass
  covers essentially everything given Opus's 1M context.
- Soft-caption fallback when ffmpeg lacks libass (true burn-in needs a libass build).
- `enforce_max_length` cap is approximate by <0.4s vs the probed render; filename
  uses the true probed length.

## Verification evidence
- **Unit/integration:** 122 tests pass (`pytest`), all external tools mocked.
- **Real text mode:** "Me at the zoo" → correct summary + `.md` saved.
- **Real video mode:** single-clip recut + soft-caption track (video/audio/mov_text),
  11s from 19s source.
- **Real crossfade render:** synthetic 3s+2s clips → 4613ms (predicted 4600ms, 13ms
  diff), confirming the `xfade`/`acrossfade` duration math.

## Agent sign-offs
- [x] Analyst — scope faithful, requirements traced.
- [x] Architect — security controls enforced in code; crossfade math sound.
- [x] Data Engineer — transcript fidelity + timestamp math correct.
- [x] Backend — logic verified; bugs fixed.
- [x] Frontend — N/A (CLI; argparse surface reviewed for ergonomics).
- [x] UX — clear progress output, actionable errors, graceful caption degradation.
- [x] SDET — coverage gaps closed; 122 tests green.
- [x] DevOps/Ops — preflight dependency checks, tempdir cleanup, runtime capability
  detection (libass) with graceful fallback.

## 7 Lenses (condensed — personal tool)
Developer lens (the relevant one): clean — own tool on own content, no surveillance.
Product lens: tight scope, buildable, shipped. No inter-lens conflicts.
