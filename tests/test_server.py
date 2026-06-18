import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from youtube_tldw import (
    BadUrlError,
    NoTranscriptError,
    TldrError,
    TranscriptTooLongError,
    core,
    server,
)
from youtube_tldw.metadata import VideoMeta
from youtube_tldw.summarize import TextResult

TOKEN = "test-token-123"
EXT_ORIGIN = "chrome-extension://abcdefghijklmnop"


@pytest.fixture
def srv():
    s = server._Server(("127.0.0.1", 0), token=TOKEN, allow_origin=None)
    threading.Thread(target=s.serve_forever, daemon=True).start()
    yield s, s.server_address[1]
    s.shutdown()
    s.server_close()


def _summary():
    return core.Summary(
        VideoMeta("dQw4w9WgXcQ", "Cool Title", "Chan", 600_000, {}, {}),
        TextResult(["point one"], "the body", 0.2, "why"), 5,
    )


def _req(port, method="POST", path="/summarize", body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", data=data, method=method)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or "null"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null"), dict(e.headers)


def _auth(extra=None):
    h = {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json",
         "Origin": EXT_ORIGIN}
    h.update(extra or {})
    return h


def test_health_no_auth(srv):
    _, port = srv
    status, payload, _ = _req(port, "GET", "/health")
    assert status == 200 and payload["ok"] and payload["name"] == "tldw"


def test_summarize_requires_token(srv):
    _, port = srv
    status, payload, _ = _req(port, body={"url": "x"},
                              headers={"Content-Type": "application/json"})
    assert status == 401


def test_summarize_happy(srv, monkeypatch):
    _, port = srv
    monkeypatch.setattr(server.core, "summarize_url", lambda *a, **k: _summary())
    status, payload, hdrs = _req(port, body={"url": "https://youtu.be/dQw4w9WgXcQ"},
                                 headers=_auth())
    assert status == 200
    assert payload["title"] == "Cool Title" and payload["key_points"] == ["point one"]
    assert payload["summary_md"] == "the body"
    assert hdrs.get("Access-Control-Allow-Origin") == EXT_ORIGIN


@pytest.mark.parametrize("exc,code", [
    (BadUrlError("bad"), 400),
    (NoTranscriptError("none"), 422),
    (TranscriptTooLongError("long"), 413),
])
def test_summarize_error_status_mapping(srv, monkeypatch, exc, code):
    _, port = srv

    def boom(*a, **k):
        raise exc

    monkeypatch.setattr(server.core, "summarize_url", boom)
    status, _, _ = _req(port, body={"url": "x"}, headers=_auth())
    assert status == code


def test_bad_json(srv):
    _, port = srv
    req = urllib.request.Request(f"http://127.0.0.1:{port}/summarize",
                                 data=b"{not json", method="POST")
    for k, v in _auth().items():
        req.add_header(k, v)
    try:
        urllib.request.urlopen(req, timeout=5)
        assert False
    except urllib.error.HTTPError as e:
        assert e.code == 400


def test_wrong_content_type(srv):
    _, port = srv
    status, _, _ = _req(port, body={"url": "x"},
                        headers={"Authorization": f"Bearer {TOKEN}",
                                 "Content-Type": "text/plain", "Origin": EXT_ORIGIN})
    assert status == 415


def test_oversized_body(srv):
    _, port = srv
    status, _, _ = _req(port, body={"url": "x" * 20000}, headers=_auth())
    assert status == 413


def test_busy_returns_429(srv, monkeypatch):
    s, port = srv
    monkeypatch.setattr(server.core, "summarize_url", lambda *a, **k: _summary())
    s.sem.acquire()
    s.sem.acquire()  # saturate
    try:
        status, _, _ = _req(port, body={"url": "x"}, headers=_auth())
        assert status == 429
    finally:
        s.sem.release()
        s.sem.release()


def test_preflight_allows_extension_origin(srv):
    _, port = srv
    status, _, hdrs = _req(port, "OPTIONS", body=None,
                           headers={"Origin": EXT_ORIGIN,
                                    "Access-Control-Request-Method": "POST"})
    assert status == 204
    assert hdrs.get("Access-Control-Allow-Origin") == EXT_ORIGIN


def _read_ndjson(port, body, headers):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/summarize/stream",
                                 data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=5) as r:
        return [json.loads(ln) for ln in r.read().decode().splitlines() if ln.strip()]


def test_summarize_stream_emits_progress_then_result(srv, monkeypatch):
    _, port = srv

    def fake(url, ratio, lang, *, timeout, max_chars, on_progress=None):
        if on_progress:
            on_progress("fetching metadata…")
            on_progress("summarizing with Claude…")
        return _summary()

    monkeypatch.setattr(server.core, "summarize_url", fake)
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ"}, _auth())
    assert [e["type"] for e in events] == ["progress", "progress", "result"]
    assert events[0]["message"] == "fetching metadata…"
    assert events[-1]["title"] == "Cool Title" and events[-1]["key_points"] == ["point one"]


def test_summarize_stream_error_is_in_band(srv, monkeypatch):
    _, port = srv

    def boom(url, ratio, lang, *, timeout, max_chars, on_progress=None):
        raise NoTranscriptError("no captions")

    monkeypatch.setattr(server.core, "summarize_url", boom)
    events = _read_ndjson(port, {"url": "x"}, _auth())
    assert events[-1] == {"type": "error", "status": 422, "error": "no captions"}


def test_voices_endpoint_no_auth(srv):
    _, port = srv
    status, payload, _ = _req(port, "GET", "/voices")
    ids = {v["id"] for v in payload["voices"]}
    assert status == 200 and "amy" in ids and "alan" in ids
    assert all("id" in v and "label" in v for v in payload["voices"])


def test_speak_returns_mp3(srv, monkeypatch):
    _, port = srv
    monkeypatch.setattr(server.audio, "require_piper", lambda: None)

    def fake_synth(text, out, voice, workdir, *, timeout=None):
        Path(out).write_bytes(b"ID3fake-mp3-bytes")

    monkeypatch.setattr(server.audio, "synthesize_speech", fake_synth)
    body = {"title": "T", "channel": "C", "key_points": ["k"], "summary": "hi",
            "voice": "amy"}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/speak",
                                 data=json.dumps(body).encode(), method="POST")
    for k, v in _auth().items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200
        assert r.headers["Content-Type"] == "audio/mpeg"
        assert r.read() == b"ID3fake-mp3-bytes"


def test_speak_rejects_unknown_voice(srv):
    _, port = srv
    status, _, _ = _req(port, "POST", "/speak",
                        {"title": "T", "channel": "C", "summary": "s",
                         "voice": "../etc/passwd"}, _auth())
    assert status == 400  # allowlisted before anything touches piper/paths


def test_speak_piper_missing_503(srv, monkeypatch):
    _, port = srv

    def no_piper():
        raise TldrError("Piper not installed")

    monkeypatch.setattr(server.audio, "require_piper", no_piper)
    status, _, _ = _req(port, "POST", "/speak",
                        {"title": "T", "channel": "C", "summary": "s", "voice": "amy"},
                        _auth())
    assert status == 503


def test_speak_requires_token(srv):
    _, port = srv
    status, _, _ = _req(port, "POST", "/speak",
                        {"title": "T", "channel": "C", "summary": "s"},
                        {"Content-Type": "application/json"})
    assert status == 401


def test_token_persists_across_calls(monkeypatch, tmp_path):
    tf = tmp_path / "token"
    monkeypatch.setattr(server, "TOKEN_FILE", tf)
    t1, p1 = server.load_or_create_token(None)
    assert p1 and tf.exists()
    t2, p2 = server.load_or_create_token(None)
    assert t2 == t1 and p2                     # stable across restarts
    assert oct(tf.stat().st_mode)[-3:] == "600"  # not world-readable


def test_explicit_token_not_persisted(monkeypatch, tmp_path):
    tf = tmp_path / "token"
    monkeypatch.setattr(server, "TOKEN_FILE", tf)
    t, persisted = server.load_or_create_token("explicit-token")
    assert t == "explicit-token" and not persisted
    assert not tf.exists()


def test_preflight_rejects_web_origin(srv):
    _, port = srv
    status, _, hdrs = _req(port, "OPTIONS", body=None,
                           headers={"Origin": "https://evil.com"})
    assert status == 204
    assert "Access-Control-Allow-Origin" not in hdrs  # fail closed
