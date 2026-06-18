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
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

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
from . import audio, core, textmode
from .summarize import SINGLE_PASS_CHARS
from .timing import format_length

MAX_BODY_BYTES = 16 * 1024
MAX_SPEAK_BYTES = 64 * 1024   # /speak carries the summary text
MAX_CONCURRENCY = 2
REQUEST_TIMEOUT = 120.0  # per-request claude budget (shorter than the CLI's)
SPEAK_TIMEOUT = 120.0    # per-request piper/ffmpeg budget
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
    os.chmod(TOKEN_FILE, 0o600)
    return token, True

# Typed error -> HTTP status.
_STATUS = {
    BadUrlError: 400,
    NoTranscriptError: 422,
    TranscriptTooLongError: 413,
    ClaudeError: 502,
    TldrTimeoutError: 504,
}


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
        if path not in ("/summarize", "/summarize/stream") and not speak:
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
            else:
                self._run_buffered(*parsed)
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
            summary = core.summarize_url(
                url, ratio, lang, timeout=REQUEST_TIMEOUT,
                max_chars=SINGLE_PASS_CHARS, on_progress=self._logger(start))
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
            summary = core.summarize_url(
                url, ratio, lang, timeout=REQUEST_TIMEOUT,
                max_chars=SINGLE_PASS_CHARS, on_progress=progress)
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
        emit({"type": "result", **_to_payload(summary)})

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

    def _run_speak(self, body: dict, *, stream: bool) -> None:
        parsed = self._validate_speak(body)
        if parsed is None:
            return  # error already sent (these precede any 200/stream)
        script, voice = parsed
        workdir = Path(tempfile.mkdtemp(prefix="youtube-tldw-speak-"))
        start = time.monotonic()
        tlog = self._logger(start)

        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self._cors_headers()
            self.end_headers()

            def emit(obj):
                self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
                self.wfile.flush()

            def progress(m, pct=None):
                tlog(m)
                emit({"type": "progress", "message": m, "percent": pct})
        else:
            def progress(m, pct=None):
                tlog(m)

        try:
            out = workdir / "out.mp3"
            audio.synthesize_speech(script, out, voice, workdir,
                                    timeout=SPEAK_TIMEOUT, on_progress=progress)
            data = out.read_bytes()
        except (TldrTimeoutError, TldrError) as exc:
            status = 504 if isinstance(exc, TldrTimeoutError) else 502
            print(f"  speak failed ({status}): {exc}", flush=True)
            if stream:
                emit({"type": "error", "status": status, "error": str(exc)})
            else:
                self._send_json(status, {"error": str(exc)})
            return
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
        print(f"  spoke {len(data)//1024}KB in {time.monotonic()-start:.1f}s "
              f"(voice={voice})", flush=True)
        if stream:
            emit({"type": "audio", "mp3_base64": base64.b64encode(data).decode("ascii")})
        else:
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
