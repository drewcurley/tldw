import json
import threading
import urllib.error
import urllib.request

import pytest

from youtube_tldw import (
    BadUrlError,
    NoTranscriptError,
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


def test_preflight_rejects_web_origin(srv):
    _, port = srv
    status, _, hdrs = _req(port, "OPTIONS", body=None,
                           headers={"Origin": "https://evil.com"})
    assert status == 204
    assert "Access-Control-Allow-Origin" not in hdrs  # fail closed
