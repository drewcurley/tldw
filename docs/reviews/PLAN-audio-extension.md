# Plan — "Generate audio" in the browser extension (TTS + player)

## Goal
After the text summary renders in the modal, a **Generate audio** button synthesizes
the summary to MP3 (Piper, server-side) and plays it in a simple HTML5 `<audio>`
player. Voice is chosen in extension options via a **dropdown** of real Piper voices.

## Server
- **Voice registry** in `audio.py`: `VOICES = {id: (model, label)}` for a curated set
  of verified en_US voices (Amy/Lessac/Kristin/HFC-female/LJSpeech + Ryan/Joe/John/
  HFC-male/Norman). `DEFAULT_VOICE="amy"`. `VOICE_ALIASES={"female":"amy","male":"ryan"}`
  for CLI back-compat. `resolve_voice(id)->model`; `ensure_voice(id)` downloads on first
  use; `synthesize_speech(text, out_mp3, id, workdir)`.
- **`build_spoken_script(title, channel, key_points, summary)`** — refactor from
  (meta,result) to plain fields so the server can build it from the request body (no
  server-side summary cache; stateless).
- **`GET /voices`** (no auth): `[{id,label}]` from VOICES — populates the dropdown.
- **`POST /speak`** (auth): body `{title, channel, key_points[], summary, voice}` ->
  build script -> `require_piper()` -> ensure+synthesize to a per-request tempdir ->
  read mp3 bytes -> respond `audio/mpeg` (Content-Length, no-store, nosniff, CORS).
  Reuses the non-blocking `Semaphore(2)` -> 429. Voice validated against VOICES (400).
  Piper missing -> 503. Stays single-pass-friendly (summary is already short).

## Extension
- **options**: a `<select id="voice">` populated from `GET /voices` (hardcoded fallback
  list if the server is down); saved to `chrome.storage.local` (default "amy").
- **content modal**: after the summary, a **Generate audio** button + an audio area.
  Click -> open a port, restart the 20s keepalive ping, post `{type:"speak", payload}`
  (the summary fields from the background's cached payload), show a small spinner.
  On audio -> render `<audio controls>` with the returned clip and re-enable. Errors
  (no piper, server down, timeout) show inline near the button.
- **background**: handle the port `speak` message -> read `voice` from settings ->
  POST `/speak` with the summary fields -> `arrayBuffer` -> base64 `data:audio/mpeg`
  URL (chunked encode) -> post `{type:"audio", dataUrl}` over the port. AbortController
  ~170s (first-use model download can be slow); content safety timeout ~180s. The
  data URL avoids cross-context blob-URL issues and keeps the token out of any media src.

## Why stateless (summary in the /speak body)
The background already caches the rendered payload per videoId; it sends those fields
to /speak. No server summary cache, no "expired" failure mode. Body stays < 16KB.

## Risks for review
- Returning a multi-MB MP3 as a base64 data URL over a runtime port — size/perf; chunk
  the encode. Is base64-over-port acceptable vs. alternatives?
- Voice registry refactor must NOT break the CLI (`--voice female|male`); keep aliases.
- /speak body validation (voice allowlist, types), Piper-missing -> clear 503.
- TTS time + first-use model download (~30s) vs the keepalive/timeout budget.
- require_piper on the server host; voice models cached in ~/.cache/youtube-tldw/voices.

## Tests
- audio: resolve_voice + aliases; build_spoken_script(new signature) strips markdown.
- server: GET /voices shape; POST /speak happy path (mock synth -> bytes) returns
  audio/mpeg; bad voice 400; piper-missing 503; auth/CORS unchanged.
- cli: `--voice` still accepts female/male (+ new ids); existing audio tests pass.
- extension JS: node --check.
