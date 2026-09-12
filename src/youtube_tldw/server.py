"""`tldw serve` — a tiny localhost HTTP API for the browser extension (text only).

Stdlib only. Security posture (see docs/reviews/PLAN-extension.md):
- loopback bind only; bearer token (hmac.compare_digest); CORS fails closed
  (chrome-extension origins or a pinned origin, never web/null/`*`);
- url goes through the youtube allowlist before any subprocess;
- bounded concurrency (non-blocking semaphore -> 429); small body cap;
- single-pass transcripts only (map-reduce-sized -> 413) so a click never hangs.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Transcript cache: after a text summary, store (meta, cues) for the same video so
# "play key moments" can skip the duplicate yt-dlp + subtitle-parse round-trip.
# Segment cache: populated by a background prefetch triggered at the end of text
# summary, so clicking "play key moments" usually gets an instant response.
# Shared TTL for both caches. Hours, not minutes: the transcript is what follow-up
# questions run against, and a conversation can long outlive the summary that
# populated it. These live in this process's memory (not on disk), so they cost RAM
# and vanish on restart — hence the entry cap, since a day of browsing would
# otherwise accumulate every video visited.
_CACHE_TTL = 4 * 3600
_CACHE_MAX_ENTRIES = 64
_cache_lock = threading.Lock()
_transcript_cache: dict = {}    # video_id -> (expires_at, meta, cues)
_seg_cache: dict = {}           # video_id -> (expires_at, meta, segments)
_seg_prefetch_events: dict = {} # video_id -> threading.Event (while prefetch is running)
# video_id -> (expires_at, session_id): the model-side conversation for this video.
# Resuming it means a follow-up question doesn't re-send the transcript.
_ask_sessions: dict = {}


def _prune(cache: dict) -> None:
    """Drop expired entries, then the soonest-to-expire until under the cap."""
    now = time.monotonic()
    for k in [k for k, v in cache.items() if v[0] < now]:
        del cache[k]
    if len(cache) > _CACHE_MAX_ENTRIES:
        for k, _ in sorted(cache.items(), key=lambda kv: kv[1][0])[
                :len(cache) - _CACHE_MAX_ENTRIES]:
            del cache[k]


def _cache_put(video_id: str, meta, cues: list) -> None:
    with _cache_lock:
        _transcript_cache[video_id] = (time.monotonic() + _CACHE_TTL, meta, cues)
        _prune(_transcript_cache)


def _cache_get(video_id: str, *, touch: bool = False):
    """Return (meta, cues) if a fresh entry exists, else None.

    touch=True renews the TTL — an active Q&A conversation keeps its own transcript
    alive rather than expiring out from under the next question.
    """
    with _cache_lock:
        entry = _transcript_cache.get(video_id)
        if entry and entry[0] > time.monotonic():
            if touch:
                _transcript_cache[video_id] = (time.monotonic() + _CACHE_TTL,
                                               entry[1], entry[2])
            return entry[1], entry[2]
        return None


def _seg_cache_put(video_id: str, meta, segments: list) -> None:
    with _cache_lock:
        _seg_cache[video_id] = (time.monotonic() + _CACHE_TTL, meta, segments)
        _prune(_seg_cache)


def _seg_cache_get(video_id: str):
    """Return (meta, segments) if a fresh entry exists, else None."""
    with _cache_lock:
        entry = _seg_cache.get(video_id)
        if entry and entry[0] > time.monotonic():
            return entry[1], entry[2]
        return None


def _session_get(video_id: str):
    with _cache_lock:
        entry = _ask_sessions.get(video_id)
        return entry[1] if entry and entry[0] > time.monotonic() else None


def _session_put(video_id: str, session_id: str) -> None:
    with _cache_lock:
        _ask_sessions[video_id] = (time.monotonic() + _CACHE_TTL, session_id)
        _prune(_ask_sessions)


def _start_seg_prefetch(meta, cues: list) -> None:
    """Start background segment selection. No-op if one is already running for this video.
    Stores a threading.Event in _seg_prefetch_events so _run_segments can wait instead
    of spawning a second concurrent Claude call."""
    vid = meta.video_id
    with _cache_lock:
        if vid in _seg_prefetch_events:
            return  # already running — don't spawn a second one
        evt = threading.Event()
        _seg_prefetch_events[vid] = evt

    def _run():
        from . import metadata as _md
        try:
            with usage.interaction("segments", vid):
                _meta, segs = core.select_segments(
                    _md.watch_url(vid), None, "en",
                    timeout=SEGMENTS_TIMEOUT,
                    _prefetched=(meta, cues),
                )
            _seg_cache_put(vid, _meta, segs)
            print(f"  prefetched {len(segs)} segments for {vid}", flush=True)
        except Exception as exc:
            print(f"  segment prefetch failed for {vid}: {exc}", flush=True)
        finally:
            with _cache_lock:
                _seg_prefetch_events.pop(vid, None)
            evt.set()  # wake any waiter (even on failure, so it doesn't block forever)

    threading.Thread(target=_run, daemon=True).start()

from . import (
    BadUrlError,
    ClaudeError,
    NoTranscriptError,
    TldrError,
    TldrTimeoutError,
    TranscriptTooLongError,
    __version__,
)
from . import metadata as md
from . import ask, audio, config, core, textmode, usage
from .summarize import SINGLE_PASS_CHARS
from .urls import canonical_video_id
from .timing import format_length, parse_duration

MAX_BODY_BYTES = 16 * 1024
MAX_SPEAK_BYTES = 64 * 1024   # /speak carries the summary text
MAX_ASK_BYTES = 64 * 1024     # /ask carries the question + conversation so far
ASK_TIMEOUT = 300.0           # one answer; the transcript is already in hand
PREVIEW_TEXT = "Hi — this is how your T L D W summaries will sound."
_preview_cache: dict[str, bytes] = {}   # voice model -> mp3 (13 voices, ~40KB each)
_preview_lock = threading.Lock()
MAX_CONCURRENCY = 2
REQUEST_TIMEOUT = 120.0   # text summarize budget
SEGMENTS_TIMEOUT = 300.0  # segment selection needs structured JSON — allow more time
# Whole-synthesis budget, not a per-clip one: a 13-minute spoken summary takes
# ~107s of Piper. The client hears the first second immediately and can Stop at any
# point, so a generous ceiling costs nothing.
SPEAK_TIMEOUT = 600.0
_LANG_RE = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,15}$")
TOKEN_FILE = Path.home() / ".config" / "youtube-tldw" / "token"


def load_or_create_token(explicit: str | None) -> tuple[str, bool]:
    """Resolve the token: explicit/env > persisted file > newly generated+saved.

    Returns (token, persisted_path_used). A stable per-install token means the
    extension only needs to be configured once.
    """
    if explicit:
        return explicit, False
    if TOKEN_FILE.exists():
        saved = TOKEN_FILE.read_text().strip()
        if saved:
            return saved, True
    token = secrets.token_urlsafe(32)
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token)
    config.restrict_to_owner(TOKEN_FILE)
    return token, True

# Typed error -> HTTP status.
_STATUS = {
    BadUrlError: 400,
    NoTranscriptError: 422,
    TranscriptTooLongError: 413,
    ClaudeError: 502,
    TldrTimeoutError: 504,
}


class _ClientGone(Exception):
    """Raised out of a streaming callback once the client has hung up, so the work
    behind it unwinds instead of running to completion into a dead socket."""


def _vid_of(url: str) -> str:
    """Best-effort video id for usage attribution; never raises."""
    try:
        return canonical_video_id(url)
    except TldrError:
        return ""


def _origin_allowed(origin: str | None, pinned: str | None) -> bool:
    """Fail closed: only a pinned origin, or (default) any chrome-extension origin.
    Never allow missing/null/web origins."""
    if not origin:
        return False
    if pinned:
        return hmac.compare_digest(origin, pinned)
    return origin.startswith("chrome-extension://")


class _Handler(BaseHTTPRequestHandler):
    server_version = "tldw"
    protocol_version = "HTTP/1.0"  # close per response; avoids keep-alive pitfalls

    # --- helpers -------------------------------------------------------------
    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if _origin_allowed(origin, self.server.allow_origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
            self.send_header("Access-Control-Allow-Headers", "authorization,content-type")
            self.send_header("Access-Control-Allow-Methods", "POST,GET,OPTIONS")
            self.send_header("Access-Control-Max-Age", "600")

    def _send_json(self, status: int, payload: dict) -> None:
        self._send_bytes(status, "application/json", json.dumps(payload).encode("utf-8"))

    def _send_bytes(self, status: int, content_type: str, data: bytes) -> None:
        # content_type is always a server-side constant; no user-derived headers.
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self._cors_headers()
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        header = self.headers.get("Authorization", "")
        prefix = "Bearer "
        if not header.startswith(prefix):
            return False
        return hmac.compare_digest(header[len(prefix):], self.server.token)

    # Default log_message is kept: it logs only "METHOD /path HTTP/x" + status to
    # stderr (no body, no token, no query) — exactly the activity feedback we want.

    # --- routes --------------------------------------------------------------
    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/health":
            self._send_json(200, {"ok": True, "name": "tldw", "version": __version__})
        elif path == "/voices":
            self._send_json(200, {"voices": audio.voice_list()})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        speak = path in ("/speak", "/speak/stream")
        if path == "/preview":
            self._guarded(self._run_preview)
            return
        if path == "/ask/stream":
            self._guarded(self._run_ask, max_bytes=MAX_ASK_BYTES)
            return
        if path not in ("/summarize", "/summarize/stream", "/segments/stream") and not speak:
            self._send_json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "missing or invalid token"})
            return
        body = self._read_body(MAX_SPEAK_BYTES if speak else MAX_BODY_BYTES)
        if body is None:
            return  # _read_body already responded
        if not speak:
            parsed = self._validate(body)
            if parsed is None:
                return
        if not self.server.sem.acquire(blocking=False):
            self._send_json(429, {"error": "busy, try again shortly"})
            return
        try:
            if path == "/speak":
                self._run_speak(body, stream=False)
            elif path == "/speak/stream":
                self._run_speak(body, stream=True)
            elif path == "/summarize/stream":
                self._run_stream(*parsed)
            elif path == "/segments/stream":
                self._run_segments(*parsed, body)
            else:
                self._run_buffered(*parsed)
        finally:
            self.server.sem.release()

    def _guarded(self, handler, *, max_bytes: int = MAX_BODY_BYTES) -> None:
        """auth -> bounded body -> concurrency slot, then run `handler(body)`."""
        if not self._authorized():
            self._send_json(401, {"error": "missing or invalid token"})
            return
        body = self._read_body(max_bytes)
        if body is None:
            return  # _read_body already responded
        if not self.server.sem.acquire(blocking=False):
            self._send_json(429, {"error": "busy, try again shortly"})
            return
        try:
            handler(body)
        finally:
            self.server.sem.release()

    def _read_body(self, max_bytes: int = MAX_BODY_BYTES) -> dict | None:
        if "chunked" in self.headers.get("Transfer-Encoding", "").lower():
            self._send_json(400, {"error": "chunked transfer not supported"})
            return None
        ctype = self.headers.get("Content-Type", "")
        if not ctype.startswith("application/json"):
            self._send_json(415, {"error": "expected application/json"})
            return None
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self._send_json(411, {"error": "Content-Length required"})
            return None
        if length <= 0 or length > max_bytes:
            self._send_json(413, {"error": "request body too large"})
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            self._send_json(400, {"error": "invalid JSON"})
            return None

    def _validate(self, body: dict):
        """Return (url, ratio, lang) or None (after sending a 400)."""
        if not isinstance(body, dict) or not isinstance(body.get("url"), str):
            self._send_json(400, {"error": "missing 'url'"}); return None
        ratio = body.get("ratio")
        if ratio is not None:
            try:
                ratio = float(ratio)
            except (TypeError, ValueError):
                self._send_json(400, {"error": "ratio must be a number"}); return None
            if not (0 < ratio <= 1):
                self._send_json(400, {"error": "ratio must be in (0, 1]"}); return None
        lang = body.get("lang", "en")
        if not isinstance(lang, str) or not _LANG_RE.match(lang):
            self._send_json(400, {"error": "invalid lang"}); return None
        return body["url"], ratio, lang

    def _logger(self, start: float):
        return lambda m, pct=None, creep=False: print(
            f"  [{time.monotonic()-start:5.1f}s] {m}", flush=True)

    def _run_buffered(self, url, ratio, lang) -> None:
        start = time.monotonic()
        try:
            with usage.interaction("summarize", _vid_of(url)):
                summary = core.summarize_url(
                    url, ratio, lang, timeout=REQUEST_TIMEOUT,
                    max_chars=SINGLE_PASS_CHARS, on_progress=self._logger(start))
                usage.note_video(summary.meta.video_id, summary.meta.duration_ms,
                                 textmode.summary_word_count(summary.result))
        except TldrError as exc:
            status = next((s for cls, s in _STATUS.items() if isinstance(exc, cls)), 500)
            print(f"  failed ({status}) in {time.monotonic()-start:.1f}s: {exc}", flush=True)
            self._send_json(status, {"error": str(exc)})
            return
        print(f"  summarized '{summary.meta.title}' in {time.monotonic()-start:.1f}s", flush=True)
        self._send_json(200, _to_payload(summary))

    def _run_stream(self, url, ratio, lang) -> None:
        """NDJSON stream: one {type:progress|result|error} JSON object per line."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors_headers()
        self.end_headers()
        start = time.monotonic()
        tlog = self._logger(start)

        def emit(obj):
            self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
            self.wfile.flush()

        def progress(m, pct=None, creep=False):
            tlog(m)
            emit({"type": "progress", "message": m, "percent": pct, "creep": creep})

        try:
            with usage.interaction("summarize", _vid_of(url)):
                summary = core.summarize_url(
                    url, ratio, lang, timeout=REQUEST_TIMEOUT,
                    max_chars=SINGLE_PASS_CHARS, on_progress=progress)
                usage.note_video(summary.meta.video_id, summary.meta.duration_ms,
                                 textmode.summary_word_count(summary.result))
        except TldrError as exc:
            status = next((s for cls, s in _STATUS.items() if isinstance(exc, cls)), 500)
            print(f"  failed ({status}) in {time.monotonic()-start:.1f}s: {exc}", flush=True)
            emit({"type": "error", "status": status, "error": str(exc)})
            return
        except Exception as exc:  # never leave the stream hanging on an unexpected error
            emit({"type": "error", "status": 500, "error": "internal error"})
            print(f"  unexpected error: {exc!r}", flush=True)
            return
        print(f"  summarized '{summary.meta.title}' in {time.monotonic()-start:.1f}s", flush=True)
        if summary.cues:
            _cache_put(summary.meta.video_id, summary.meta, summary.cues)
            # Kick off segment selection in the background so "play key moments" is
            # ready by the time the user reads the summary.
            _start_seg_prefetch(summary.meta, summary.cues)
        emit({"type": "result", **_to_payload(summary), "stats": usage.stats()})

    def _validate_speak(self, body: dict):
        """Return (script, voice) or None (after sending the proper error status)."""
        if not all(isinstance(body.get(k), str) for k in ("title", "channel", "summary")):
            self._send_json(400, {"error": "missing title/channel/summary"}); return None
        kp = body.get("key_points", [])
        if not isinstance(kp, list) or not all(isinstance(x, str) for x in kp):
            self._send_json(400, {"error": "key_points must be a list of strings"}); return None
        voice = body.get("voice", audio.DEFAULT_VOICE)
        if not isinstance(voice, str):
            self._send_json(400, {"error": "voice must be a string"}); return None
        try:
            audio.resolve_voice(voice)  # allowlist -> model, before anything runs
        except TldrError as exc:
            self._send_json(400, {"error": str(exc)}); return None
        try:
            audio.require_piper()
        except TldrError as exc:
            self._send_json(503, {"error": str(exc)}); return None
        script = audio.build_spoken_script(body["title"], body["channel"], kp,
                                           body["summary"])
        return script, voice

    def _run_segments(self, url, ratio, lang, body: dict) -> None:
        """Stream progress, then the key source time-spans for in-player skipping."""
        max_ms = None
        ml = body.get("max_length")
        if isinstance(ml, str) and ml.strip():
            try:
                max_ms = parse_duration(ml)
            except TldrError:
                pass  # ignore an unparseable cap rather than failing the request
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors_headers()
        self.end_headers()
        start = time.monotonic()
        tlog = self._logger(start)

        def emit(obj):
            self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
            self.wfile.flush()

        def progress(m, pct=None):
            tlog(m)
            emit({"type": "progress", "message": m, "percent": pct})

        from . import BadUrlError
        try:
            vid = canonical_video_id(url)
        except BadUrlError:
            vid = None

        def _wait_with_heartbeat(wait_evt, cue_label):
            """Block on wait_evt in 5s chunks, emitting progress each tick.
            Returns True if the event fired, False if SEGMENTS_TIMEOUT exceeded."""
            progress(f"Analyzing {cue_label}…", 22)
            t0 = time.monotonic()
            while not wait_evt.wait(timeout=5.0):
                elapsed = time.monotonic() - t0
                if elapsed >= SEGMENTS_TIMEOUT:
                    return False
                pct = round(min(25 + elapsed * 0.85, 92), 1)
                progress(f"Analyzing {cue_label}… ({int(elapsed)}s)", pct)
            return True

        def _emit_segments(meta, segments):
            progress(f"Found {len(segments)} key moment(s)!", 98)
            print(f"  {len(segments)} key segments for '{meta.title}' "
                  f"in {time.monotonic()-start:.1f}s", flush=True)
            emit({"type": "segments", "segments": segments, "title": meta.title,
                  "channel": meta.channel, "source_url": md.watch_url(meta.video_id)})

        def _emit_segments_done(meta, segments):
            progress(f"Found {len(segments)} key moment(s)!", 98)
            print(f"  {len(segments)} key segments for '{meta.title}' "
                  f"in {time.monotonic()-start:.1f}s", flush=True)
            emit({"type": "segments_done", "title": meta.title,
                  "channel": meta.channel, "source_url": md.watch_url(meta.video_id)})

        # 1. Instant cache hit
        if vid and max_ms is None and ratio is None:
            seg_hit = _seg_cache_get(vid)
            if seg_hit:
                meta, segments = seg_hit
                print(f"  cached segments for {vid} ({len(segments)} moments, "
                      f"{time.monotonic()-start:.1f}s)", flush=True)
                _emit_segments(meta, segments)
                return

        # Cue count for informative heartbeat messages
        tx_hit = _cache_get(vid) if vid else None
        cue_label = f"{len(tx_hit[1])} cues" if tx_hit else "transcript"

        # 2. Wait for in-progress prefetch (heartbeat while blocked)
        if vid and max_ms is None and ratio is None:
            with _cache_lock:
                prefetch_evt = _seg_prefetch_events.get(vid)
            if prefetch_evt:
                print(f"  waiting for prefetch for {vid}", flush=True)
                _wait_with_heartbeat(prefetch_evt, cue_label)
                seg_hit = _seg_cache_get(vid)
                if seg_hit:
                    _emit_segments(*seg_hit)
                    return
                # prefetch failed — fall through to direct call

        # 3. Direct call — emit segment_added for each span as Claude finds it so the
        #    client can start playback immediately without waiting for the full list.
        prefetched = tx_hit
        streamed_segs: list = []

        def _on_segment_ready(seg: dict) -> None:
            streamed_segs.append(seg)
            emit({"type": "segment_added", "segment": seg})

        try:
            with usage.interaction("segments", vid or ""):
                meta, segments = core.select_segments(
                    url, ratio, lang, max_length_ms=max_ms,
                    timeout=SEGMENTS_TIMEOUT, on_progress=progress,
                    on_segment_ready=_on_segment_ready,
                    _prefetched=prefetched)
        except TldrError as exc:
            status = next((s for cls, s in _STATUS.items() if isinstance(exc, cls)), 500)
            print(f"  segments failed ({status}) in {time.monotonic()-start:.1f}s: {exc}",
                  flush=True)
            emit({"type": "error", "status": status, "error": str(exc)})
            return
        except Exception as exc:
            print(f"  unexpected error: {exc!r}", flush=True)
            emit({"type": "error", "status": 500, "error": "internal error"})
            return
        if vid:
            _seg_cache_put(vid, meta, segments)
        if streamed_segs:
            _emit_segments_done(meta, segments)
        else:
            _emit_segments(meta, segments)

    def _run_speak(self, body: dict, *, stream: bool) -> None:
        parsed = self._validate_speak(body)
        if parsed is None:
            return  # error already sent (these precede any 200/stream)
        script, voice = parsed
        # Opt-in: an older extension gets the single buffered {"type":"audio"} event.
        chunked = stream and body.get("stream_audio") is True
        gone = threading.Event()   # set when the client stops listening
        start = time.monotonic()
        tlog = self._logger(start)

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self._cors_headers()
            self.end_headers()
            # Progress comes from the synthesis thread while audio blocks are yielded
            # on this one — one lock so a line is never interleaved with another.
            wlock = threading.Lock()

            def emit(obj):
                # A failed write means the client hung up (Stop button, closed tab).
                # Record it instead of raising: the synthesis thread calls this too,
                # and an exception there would surface as a confusing server error.
                if gone.is_set():
                    return
                line = (json.dumps(obj) + "\n").encode("utf-8")
                with wlock:
                    try:
                        self.wfile.write(line)
                        self.wfile.flush()
                    except OSError:
                        gone.set()

            def progress(m, pct=None):
                tlog(m)
                emit({"type": "progress", "message": m, "percent": pct})
        else:
            def progress(m, pct=None):
                tlog(m)

        total, seq = 0, 0
        blocks = []
        speech = audio.stream_speech(script, voice, timeout=SPEAK_TIMEOUT,
                                     on_progress=progress)
        try:
            for block in speech:
                total += len(block)
                if chunked:
                    emit({"type": "audio_chunk", "seq": seq,
                          "mp3_base64": base64.b64encode(block).decode("ascii")})
                    seq += 1
                else:
                    blocks.append(block)
                if gone.is_set():
                    break
        except (TldrTimeoutError, TldrError) as exc:
            status = 504 if isinstance(exc, TldrTimeoutError) else 502
            print(f"  speak failed ({status}): {exc}", flush=True)
            if stream:
                emit({"type": "error", "status": status, "error": str(exc)})
            else:
                self._send_json(status, {"error": str(exc)})
            return
        except Exception as exc:
            print(f"  unexpected speak error: {exc!r}", flush=True)
            if stream:
                emit({"type": "error", "status": 500, "error": "internal error"})
            else:
                self._send_json(500, {"error": "internal error"})
            return
        finally:
            # Closing the generator unwinds into stream_filter, which kills ffmpeg and
            # unblocks Piper — without this an abort would synthesize to the end.
            speech.close()
        if gone.is_set():
            print(f"  speak stopped by client after {time.monotonic()-start:.1f}s "
                  f"({total//1024}KB, voice={voice})", flush=True)
            return
        print(f"  spoke {total//1024}KB in {time.monotonic()-start:.1f}s "
              f"(voice={voice}{', streamed' if chunked else ''})", flush=True)
        if chunked:
            emit({"type": "audio_end", "chunks": seq, "bytes": total})
        elif stream:
            emit({"type": "audio",
                  "mp3_base64": base64.b64encode(b"".join(blocks)).decode("ascii")})
        else:
            self._send_bytes(200, "audio/mpeg", b"".join(blocks))

    def _run_ask(self, body: dict) -> None:
        """Stream an answer to a follow-up question about a video's transcript."""
        if not isinstance(body, dict):
            self._send_json(400, {"error": "invalid body"}); return
        url = body.get("url")
        question = body.get("question")
        if not isinstance(url, str):
            self._send_json(400, {"error": "missing 'url'"}); return
        if not isinstance(question, str) or not question.strip():
            self._send_json(400, {"error": "missing 'question'"}); return
        if len(question) > ask.MAX_QUESTION_CHARS:
            self._send_json(413, {"error": "question too long"}); return
        history = body.get("history", [])
        if not isinstance(history, list):
            self._send_json(400, {"error": "history must be a list"}); return
        lang = body.get("lang", "en")
        if not isinstance(lang, str) or not _LANG_RE.match(lang):
            self._send_json(400, {"error": "invalid lang"}); return
        try:
            vid = canonical_video_id(url)          # BadUrlError before any work
        except TldrError as exc:
            self._send_json(400, {"error": str(exc)}); return

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors_headers()
        self.end_headers()
        start = time.monotonic()
        tlog = self._logger(start)
        gone = threading.Event()
        wlock = threading.Lock()

        def emit(obj):
            if gone.is_set():
                return
            line = (json.dumps(obj) + "\n").encode("utf-8")
            with wlock:
                try:
                    self.wfile.write(line)
                    self.wfile.flush()
                except OSError:
                    gone.set()   # client hung up (Stop, or the tab went away)

        def progress(m, pct=None):
            tlog(m)
            emit({"type": "progress", "message": m, "percent": pct})

        ask_scope = usage.interaction("ask", vid)
        ask_scope.__enter__()
        try:
            # Touch on read: an active conversation keeps its transcript alive.
            hit = _cache_get(vid, touch=True)
            if hit:
                meta, cues = hit
                tlog(f"cached transcript for {vid} ({len(cues)} cues)")
            else:
                progress("Fetching the transcript…", 5)
                meta, cues = core.fetch_transcript(vid, lang, on_progress=progress)
                _cache_put(vid, meta, cues)
            if gone.is_set():
                return
            progress("Thinking…", 20)

            def on_delta(text: str) -> None:
                emit({"type": "delta", "text": text})
                if gone.is_set():
                    raise _ClientGone      # stop the model, don't just stop writing

            resume = _session_get(vid)
            if resume:
                tlog("resuming this video's conversation (no transcript re-sent)")
            answered = ask.stream_answer(
                meta, cues, history, question, timeout=ASK_TIMEOUT,
                on_delta=on_delta, session_id=resume,
                on_session=lambda sid: _session_put(vid, sid))
        except _ClientGone:
            print(f"  ask stopped by client after {time.monotonic()-start:.1f}s",
                  flush=True)
            return
        except TldrError as exc:
            status = next((s for cls, s in _STATUS.items() if isinstance(exc, cls)), 500)
            print(f"  ask failed ({status}) in {time.monotonic()-start:.1f}s: {exc}",
                  flush=True)
            emit({"type": "error", "status": status, "error": str(exc)})
            return
        except Exception as exc:
            print(f"  unexpected ask error: {exc!r}", flush=True)
            emit({"type": "error", "status": 500, "error": "internal error"})
            return
        finally:
            ask_scope.__exit__(None, None, None)
        if gone.is_set():
            print(f"  ask stopped by client after {time.monotonic()-start:.1f}s",
                  flush=True)
            return
        print(f"  answered ({len(answered)} chars) in {time.monotonic()-start:.1f}s",
              flush=True)
        emit({"type": "answer_done"})

    def _run_preview(self, body: dict) -> None:
        """A short spoken sample of one voice, for the voice pickers. Memoized."""
        if not isinstance(body, dict):
            self._send_json(400, {"error": "invalid body"}); return
        voice = body.get("voice", audio.DEFAULT_VOICE)
        if not isinstance(voice, str):
            self._send_json(400, {"error": "voice must be a string"}); return
        try:
            model = audio.resolve_voice(voice)  # allowlist -> model, before anything runs
        except TldrError as exc:
            self._send_json(400, {"error": str(exc)}); return
        try:
            audio.require_piper()
        except TldrError as exc:
            self._send_json(503, {"error": str(exc)}); return
        with _preview_lock:
            cached = _preview_cache.get(model)
        if cached:
            self._send_bytes(200, "audio/mpeg", cached)
            return
        start = time.monotonic()
        try:
            data = b"".join(audio.stream_speech(PREVIEW_TEXT, voice,
                                                timeout=SPEAK_TIMEOUT))
        except (TldrTimeoutError, TldrError) as exc:
            status = 504 if isinstance(exc, TldrTimeoutError) else 502
            print(f"  preview failed ({status}): {exc}", flush=True)
            self._send_json(status, {"error": str(exc)}); return
        except Exception as exc:
            print(f"  unexpected preview error: {exc!r}", flush=True)
            self._send_json(500, {"error": "internal error"}); return
        with _preview_lock:
            _preview_cache[model] = data
        print(f"  preview {model} ({len(data)//1024}KB) in "
              f"{time.monotonic()-start:.1f}s", flush=True)
        self._send_bytes(200, "audio/mpeg", data)


def _to_payload(summary: core.Summary) -> dict:
    meta, result = summary.meta, summary.result
    return {
        "video_id": meta.video_id,
        "title": meta.title,
        "channel": meta.channel,
        "source_url": md.watch_url(meta.video_id),
        "original_length": format_length(meta.duration_ms),
        "length_label": textmode.length_label(result),
        "key_points": result.key_points,
        "summary_md": result.summary,
        "rationale": result.rationale,
    }


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, token: str, allow_origin: str | None):
        super().__init__(addr, _Handler)
        self.token = token
        self.allow_origin = allow_origin
        self.sem = threading.Semaphore(MAX_CONCURRENCY)


def serve(host: str = "127.0.0.1", port: int = 8765,
          token: str | None = None, allow_origin: str | None = None) -> None:
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise TldrError(
            "Refusing to bind a non-loopback host. Hosting for others needs TLS and "
            "API billing — see docs/reviews/PLAN-extension.md."
        )
    token, persisted = load_or_create_token(token)
    httpd = _Server((host, port), token, allow_origin)
    print(f"tldw serve listening on http://{host}:{port}", flush=True)
    print(f"  token: {token}")
    if persisted:
        print(f"  (saved to {TOKEN_FILE} — stable across restarts; configure the "
              "extension once)")
    print("  Ctrl-C to stop. Requests are logged below:")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        # serve_forever() has already unwound here, so only close the socket
        # (calling shutdown() from this thread would deadlock).
        httpd.server_close()
