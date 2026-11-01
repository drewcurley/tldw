import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from youtube_tldw import (
    BadUrlError,
    ClaudeError,
    NoTranscriptError,
    TldrError,
    TranscriptTooLongError,
    core,
    server,
)
from youtube_tldw import txcache
from youtube_tldw.metadata import VideoMeta
from youtube_tldw.summarize import TextResult

TOKEN = "test-token-123"
EXT_ORIGIN = "chrome-extension://abcdefghijklmnop"


# Home-directory isolation and the server's registries are reset for every test
# by tests/conftest.py.


@pytest.fixture
def srv():
    s = server._Server(("127.0.0.1", 0), token=TOKEN, allow_origin=None)
    # The default 0.5s poll is pure waiting in shutdown(): 75 tests x 0.5s was
    # most of the suite's runtime.
    threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05},
                     daemon=True).start()
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
    """Routes that carry only a request keep a small cap."""
    _, port = srv
    status, _, _ = _req(port, "POST", "/segments/stream",
                        {"url": "x" * 20000}, _auth())
    assert status == 413


def test_summarize_accepts_a_transcript_sized_body_but_not_an_endless_one(srv):
    """It may carry the caption track the page already fetched — but the cap still
    exists, it's just sized for one."""
    _, port = srv
    assert server.MAX_SUMMARIZE_BYTES > 1_000_000
    # A transcript-sized body is read (it fails later, on the URL, not on size).
    big = {"url": "not-a-youtube-url", "page": {"captions": "x" * 200_000}}
    status, _, _ = _req(port, "POST", "/summarize", big, _auth())
    assert status == 400
    # The cap itself, without pushing megabytes over a socket the server stops
    # reading the moment it decides to refuse.
    assert server._body_limit("/summarize/stream") == server.MAX_SUMMARIZE_BYTES
    assert server._body_limit("/segments/stream") == server.MAX_BODY_BYTES
    assert server._body_limit("/ask/stream") == server.MAX_ASK_BYTES
    assert server._body_limit("/speak/stream") == server.MAX_SPEAK_BYTES


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


def _read_ndjson(port, body, headers, path="/summarize/stream"):
    data = json.dumps(body).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=data, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=5) as r:
        return [json.loads(ln) for ln in r.read().decode().splitlines() if ln.strip()]


def test_summarize_stream_emits_progress_then_result(srv, monkeypatch):
    _, port = srv

    def fake(url, ratio, lang, *, timeout, max_chars, on_progress=None,
             on_partial=None, _prefetched=None):
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

    def boom(url, ratio, lang, *, timeout, max_chars, on_progress=None,
             on_partial=None, _prefetched=None):
        raise NoTranscriptError("no captions")

    monkeypatch.setattr(server.core, "summarize_url", boom)
    events = _read_ndjson(port, {"url": "x"}, _auth())
    assert events[-1] == {"type": "error", "status": 422, "error": "no captions"}


def test_segments_stream(srv, monkeypatch):
    _, port = srv

    def fake_seg(url, ratio, lang, *, max_length_ms=None, timeout=None, on_progress=None,
                 on_segment_ready=None, _prefetched=None):
        segs = [{"start": 1.0, "end": 3.5, "label": "0:01"},
                {"start": 12.0, "end": 18.0, "label": "0:12"}]
        if on_progress:
            on_progress("selecting the key moments…", 22)
        meta = VideoMeta("dQw4w9WgXcQ", "Cool Title", "Chan", 600_000, {}, {})
        if on_segment_ready:
            for s in segs:
                on_segment_ready(s)
        return meta, segs

    monkeypatch.setattr(server.core, "select_segments", fake_seg)
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ"},
                          _auth(), path="/segments/stream")
    types = [e["type"] for e in events]
    # Streaming path: progress → segment_added × N → progress → segments_done
    assert types[0] == "progress"
    assert types[-1] == "segments_done"
    assert types[-2] == "progress"   # "Found N key moments!" just before done
    seg_events = [e for e in events if e["type"] == "segment_added"]
    assert len(seg_events) == 2
    assert seg_events[0]["segment"] == {"start": 1.0, "end": 3.5, "label": "0:01"}
    final = events[-1]
    assert final["title"] == "Cool Title"


def test_voices_endpoint_no_auth(srv):
    _, port = srv
    status, payload, _ = _req(port, "GET", "/voices")
    ids = {v["id"] for v in payload["voices"]}
    assert status == 200 and "amy" in ids and "alan" in ids
    assert all("id" in v and "label" in v for v in payload["voices"])


def _fake_stream(blocks, progress=()):
    """Stand in for audio.stream_speech: report progress, then yield mp3 blocks."""
    def _stream(text, voice, *, timeout=None, on_progress=None):
        for m in progress:
            if on_progress:
                on_progress(m, None)
        yield from blocks
    return _stream


def test_speak_returns_mp3(srv, monkeypatch):
    _, port = srv
    monkeypatch.setattr(server.audio, "require_piper", lambda: None)
    monkeypatch.setattr(server.audio, "stream_speech",
                        _fake_stream([b"ID3fake-", b"mp3-bytes"]))
    body = {"title": "T", "channel": "C", "key_points": ["k"], "summary": "hi",
            "voice": "amy"}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/speak",
                                 data=json.dumps(body).encode(), method="POST")
    for k, v in _auth().items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=5) as r:
        assert r.status == 200
        assert r.headers["Content-Type"] == "audio/mpeg"
        assert r.read() == b"ID3fake-mp3-bytes"   # blocks joined for the buffered route


def test_speak_stream_emits_progress_then_audio(srv, monkeypatch):
    """Without stream_audio, an older extension still gets one buffered clip."""
    import base64
    _, port = srv
    monkeypatch.setattr(server.audio, "require_piper", lambda: None)
    monkeypatch.setattr(server.audio, "stream_speech",
                        _fake_stream([b"MP3", b"DATA"],
                                     progress=["synthesizing speech…", "encoding mp3…"]))
    body = {"title": "T", "channel": "C", "key_points": ["k"], "summary": "hi",
            "voice": "amy"}
    events = _read_ndjson(port, body, _auth(), path="/speak/stream")
    assert [e["type"] for e in events] == ["progress", "progress", "audio"]
    assert events[0]["message"] == "synthesizing speech…"
    assert base64.b64decode(events[-1]["mp3_base64"]) == b"MP3DATA"


def test_speak_stream_chunks_audio_when_opted_in(srv, monkeypatch):
    """stream_audio:true -> a run of audio_chunk blocks, then audio_end."""
    import base64
    _, port = srv
    monkeypatch.setattr(server.audio, "require_piper", lambda: None)
    monkeypatch.setattr(server.audio, "stream_speech",
                        _fake_stream([b"aaa", b"bbb", b"ccc"],
                                     progress=["synthesizing speech… 50%"]))
    body = {"title": "T", "channel": "C", "key_points": ["k"], "summary": "hi",
            "voice": "amy", "stream_audio": True}
    events = _read_ndjson(port, body, _auth(), path="/speak/stream")
    assert [e["type"] for e in events] == [
        "progress", "audio_chunk", "audio_chunk", "audio_chunk", "audio_end"]
    chunks = [e for e in events if e["type"] == "audio_chunk"]
    assert [c["seq"] for c in chunks] == [0, 1, 2]        # ordered, no gaps
    assert b"".join(base64.b64decode(c["mp3_base64"]) for c in chunks) == b"aaabbbccc"
    assert events[-1]["chunks"] == 3 and events[-1]["bytes"] == 9


def test_speak_stream_reports_failure_mid_stream(srv, monkeypatch):
    _, port = srv
    monkeypatch.setattr(server.audio, "require_piper", lambda: None)

    def boom(text, voice, *, timeout=None, on_progress=None):
        yield b"aaa"
        raise TldrError("ffmpeg died")

    monkeypatch.setattr(server.audio, "stream_speech", boom)
    body = {"title": "T", "channel": "C", "key_points": [], "summary": "hi",
            "voice": "amy", "stream_audio": True}
    events = _read_ndjson(port, body, _auth(), path="/speak/stream")
    assert [e["type"] for e in events] == ["audio_chunk", "error"]
    assert events[-1]["status"] == 502 and "ffmpeg died" in events[-1]["error"]


def test_speak_stream_stops_synthesizing_when_client_disconnects(srv, monkeypatch):
    """Stop button: the page drops the connection, so the server must abandon the
    clip instead of synthesizing minutes of audio nobody will hear."""
    _, port = srv
    monkeypatch.setattr(server.audio, "require_piper", lambda: None)
    state = {"yielded": 0, "closed": False, "ran_to_end": False}

    def slow(text, voice, *, timeout=None, on_progress=None):
        try:
            for _ in range(500):
                state["yielded"] += 1
                yield b"x" * 4096
                time.sleep(0.01)
            state["ran_to_end"] = True
        finally:
            state["closed"] = True      # generator closed -> ffmpeg killed, slot freed

    monkeypatch.setattr(server.audio, "stream_speech", slow)
    payload = json.dumps({"title": "T", "channel": "C", "key_points": [],
                          "summary": "hi", "voice": "amy",
                          "stream_audio": True}).encode()
    request = (b"POST /speak/stream HTTP/1.1\r\n"
               b"Host: 127.0.0.1\r\n"
               b"Authorization: Bearer " + TOKEN.encode() + b"\r\n"
               b"Origin: " + EXT_ORIGIN.encode() + b"\r\n"
               b"Content-Type: application/json\r\n"
               b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload)
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(request)
    assert sock.recv(4096)              # streaming has started
    sock.close()                        # hang up mid-clip

    deadline = time.monotonic() + 10
    while not state["closed"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert state["closed"], "synthesis was never stopped"
    assert not state["ran_to_end"], "server kept synthesizing after the client left"
    assert state["yielded"] < 500

    # The concurrency slot came back with it — the next request still works.
    monkeypatch.setattr(server.audio, "stream_speech", _fake_stream([b"MP3"]))
    events = _read_ndjson(port, {"title": "T", "channel": "C", "key_points": [],
                                 "summary": "hi", "voice": "amy"},
                          _auth(), path="/speak/stream")
    assert events[-1]["type"] == "audio"


def test_speak_uses_the_long_synthesis_budget(srv, monkeypatch):
    """A 13-minute spoken summary is ~107s of Piper; guard the headroom."""
    _, port = srv
    monkeypatch.setattr(server.audio, "require_piper", lambda: None)
    seen = {}

    def capture(text, voice, *, timeout=None, on_progress=None):
        seen["timeout"] = timeout
        yield b"MP3"

    monkeypatch.setattr(server.audio, "stream_speech", capture)
    _read_ndjson(port, {"title": "T", "channel": "C", "key_points": [],
                        "summary": "hi", "voice": "amy"}, _auth(), path="/speak/stream")
    assert seen["timeout"] == server.SPEAK_TIMEOUT >= 600


def test_preview_returns_mp3_and_caches(srv, monkeypatch):
    _, port = srv
    monkeypatch.setattr(server, "_preview_cache", {})
    monkeypatch.setattr(server.audio, "require_piper", lambda: None)
    calls = []

    def counting(text, voice, *, timeout=None, on_progress=None):
        calls.append(voice)
        yield b"PREVIEW-MP3"

    monkeypatch.setattr(server.audio, "stream_speech", counting)
    for _ in range(2):
        req = urllib.request.Request(f"http://127.0.0.1:{port}/preview",
                                     data=json.dumps({"voice": "cori"}).encode(),
                                     method="POST")
        for k, v in _auth().items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=5) as r:
            assert r.status == 200
            assert r.headers["Content-Type"] == "audio/mpeg"
            assert r.read() == b"PREVIEW-MP3"
    assert calls == ["cori"]           # second request served from the memo


def test_preview_rejects_unknown_voice(srv):
    _, port = srv
    status, _, _ = _req(port, "POST", "/preview", {"voice": "../etc/passwd"}, _auth())
    assert status == 400


def test_preview_requires_token(srv):
    _, port = srv
    status, _, _ = _req(port, "POST", "/preview", {"voice": "amy"},
                        {"Content-Type": "application/json"})
    assert status == 401


def test_preview_piper_missing_503(srv, monkeypatch):
    _, port = srv
    monkeypatch.setattr(server, "_preview_cache", {})

    def no_piper():
        raise TldrError("Piper not installed")

    monkeypatch.setattr(server.audio, "require_piper", no_piper)
    status, _, _ = _req(port, "POST", "/preview", {"voice": "amy"}, _auth())
    assert status == 503


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


def _ask_body(**over):
    body = {"url": "https://youtu.be/dQw4w9WgXcQ", "question": "what about DNS?"}
    body.update(over)
    return body


def _stub_transcript(monkeypatch, cues=("hello",)):
    """Serve a transcript without touching yt-dlp, and count the fetches."""
    from youtube_tldw.transcript import Cue
    meta = VideoMeta("dQw4w9WgXcQ", "Cool Title", "Chan", 600_000, {}, {})
    parsed = [Cue(i * 1000, i * 1000 + 900, t) for i, t in enumerate(cues)]
    calls = []

    def fake_fetch(vid, lang="en", *, on_progress=None, pcts=(3, 7, 13)):
        calls.append(vid)
        return meta, parsed

    monkeypatch.setattr(server.core, "fetch_transcript", fake_fetch)
    return calls


def test_ask_streams_progress_then_deltas(srv, monkeypatch):
    _, port = srv
    _stub_transcript(monkeypatch)
    monkeypatch.setattr(server.ask, "stream_answer",
                        lambda meta, cues, history, q, **k:
                        [k["on_delta"](t) for t in ("It ", "covers ", "DNS [12:34].")] and "")
    events = _read_ndjson(port, _ask_body(), _auth(), path="/ask/stream")
    types = [e["type"] for e in events]
    assert types[-1] == "answer_done"
    assert "progress" in types
    deltas = "".join(e["text"] for e in events if e["type"] == "delta")
    assert deltas == "It covers DNS [12:34]."


def test_ask_reuses_the_cached_transcript(srv, monkeypatch):
    """A question right after a summary must not re-fetch the video."""
    _, port = srv
    calls = _stub_transcript(monkeypatch)
    monkeypatch.setattr(server.ask, "stream_answer",
                        lambda *a, **k: k["on_delta"]("ok") or "ok")
    _read_ndjson(port, _ask_body(), _auth(), path="/ask/stream")
    assert calls == ["dQw4w9WgXcQ"]           # first question fetches
    _read_ndjson(port, _ask_body(question="and again?"), _auth(), path="/ask/stream")
    assert calls == ["dQw4w9WgXcQ"]           # second one is served from cache


def test_ask_passes_history_through_to_the_model(srv, monkeypatch):
    _, port = srv
    _stub_transcript(monkeypatch)
    seen = {}

    def capture(meta, cues, history, q, *, timeout=None, on_delta=None,
                session_id=None, on_session=None):
        seen["history"], seen["question"] = history, q
        on_delta("ok")
        return "ok"

    monkeypatch.setattr(server.ask, "stream_answer", capture)
    history = [{"role": "user", "content": "who made it?"},
               {"role": "assistant", "content": "a team"}]
    _read_ndjson(port, _ask_body(history=history), _auth(), path="/ask/stream")
    assert seen["history"] == history and seen["question"] == "what about DNS?"


def test_ask_reports_model_failure_mid_answer(srv, monkeypatch):
    _, port = srv
    _stub_transcript(monkeypatch)

    def boom(meta, cues, history, q, *, timeout=None, on_delta=None,
             session_id=None, on_session=None):
        on_delta("partial ")
        raise ClaudeError("model exploded")

    monkeypatch.setattr(server.ask, "stream_answer", boom)
    events = _read_ndjson(port, _ask_body(), _auth(), path="/ask/stream")
    assert events[-1]["type"] == "error" and events[-1]["status"] == 502
    assert any(e["type"] == "delta" for e in events)     # partial text was delivered


def test_ask_rejects_bad_requests(srv, monkeypatch):
    _, port = srv
    _stub_transcript(monkeypatch)
    for body, expect in [
        ({"question": "q?"}, 400),                                    # no url
        (_ask_body(question=""), 400),                                # blank question
        (_ask_body(question="   "), 400),
        (_ask_body(history="not a list"), 400),
        (_ask_body(lang="../etc"), 400),
        ({"url": "https://example.com/evil", "question": "q?"}, 400),  # not youtube
        (_ask_body(question="x" * 5000), 413),
    ]:
        status, _, _ = _req(port, "POST", "/ask/stream", body, _auth())
        assert status == expect, body


def test_ask_resumes_this_video_conversation(srv, monkeypatch):
    """First question opens a session; the next one resumes it instead of
    re-sending the transcript."""
    _, port = srv
    _stub_transcript(monkeypatch)
    seen = []

    def answer(meta, cues, history, q, *, timeout=None, on_delta=None,
               session_id=None, on_session=None):
        seen.append(session_id)
        if on_session:
            on_session("sess-abc")
        on_delta("ok")
        return "ok"

    monkeypatch.setattr(server.ask, "stream_answer", answer)
    _read_ndjson(port, _ask_body(), _auth(), path="/ask/stream")
    _read_ndjson(port, _ask_body(question="follow up?"), _auth(), path="/ask/stream")
    assert seen == [None, "sess-abc"]

    # A different video must not inherit it.
    other = _ask_body(url="https://youtu.be/aircAruvnKk", question="other?")
    monkeypatch.setattr(server.core, "fetch_transcript",
                        lambda vid, lang="en", **k: (
                            VideoMeta(vid, "T", "C", 1000, {}, {}), []))
    _read_ndjson(port, other, _auth(), path="/ask/stream")
    assert seen[-1] is None


def test_ask_requires_token(srv):
    _, port = srv
    status, _, _ = _req(port, "POST", "/ask/stream", _ask_body(),
                        {"Content-Type": "application/json"})
    assert status == 401


def test_ask_stops_when_the_client_disconnects(srv, monkeypatch):
    """Stop / closed tab must unwind the model call, not just stop writing to a
    dead socket while `claude` keeps generating."""
    _, port = srv
    _stub_transcript(monkeypatch)
    state = {"aborted": False, "ran_to_end": False}

    def slow(meta, cues, history, q, *, timeout=None, on_delta=None,
             session_id=None, on_session=None):
        try:
            for _ in range(400):
                on_delta("word " * 200)     # raises _ClientGone once the peer is gone
                time.sleep(0.01)
        except BaseException:
            state["aborted"] = True
            raise
        state["ran_to_end"] = True
        return "done"

    monkeypatch.setattr(server.ask, "stream_answer", slow)
    payload = json.dumps(_ask_body()).encode()
    request = (b"POST /ask/stream HTTP/1.1\r\nHost: 127.0.0.1\r\n"
               b"Authorization: Bearer " + TOKEN.encode() + b"\r\n"
               b"Origin: " + EXT_ORIGIN.encode() + b"\r\n"
               b"Content-Type: application/json\r\n"
               b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload)
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(request)
    assert sock.recv(4096)
    sock.close()

    deadline = time.monotonic() + 10
    while not state["aborted"] and not state["ran_to_end"] and time.monotonic() < deadline:
        time.sleep(0.02)
    assert state["aborted"], "the model call was never unwound"
    assert not state["ran_to_end"], "kept generating after the client left"


def test_transcript_cache_ttl_is_hours_and_capped(monkeypatch):
    """Long enough for a conversation, bounded so an all-day server can't grow."""
    assert server._CACHE_TTL >= 2 * 3600
    cache = {}
    now = time.monotonic()
    for i in range(server._CACHE_MAX_ENTRIES + 20):
        cache[f"v{i}"] = (now + 1000 + i, None, None)   # all still fresh
    server._prune(cache)
    assert len(cache) == server._CACHE_MAX_ENTRIES
    assert "v0" not in cache and f"v{server._CACHE_MAX_ENTRIES + 19}" in cache


def test_cache_get_touch_renews_the_ttl(monkeypatch):
    meta = VideoMeta("vid", "T", "C", 1000, {}, {})
    server._cache_put("vid", meta, ["cue"])
    with server._cache_lock:
        first = server._transcript_cache["vid"][0]
    time.sleep(0.01)
    assert server._cache_get("vid", touch=True) == (meta, ["cue"])
    with server._cache_lock:
        assert server._transcript_cache["vid"][0] > first
    with server._cache_lock:
        del server._transcript_cache["vid"]


def test_summarize_stream_sends_the_summary_as_it_is_written(srv, monkeypatch):
    """meta first (enough to draw the layout), then each key point and paragraph,
    then the full result — reading can start long before the model finishes."""
    _, port = srv

    def fake(url, ratio, lang, *, timeout, max_chars, on_progress=None,
             on_partial=None, _prefetched=None):
        summary = _summary()
        on_partial({"kind": "meta", "meta": summary.meta})
        on_partial({"kind": "key_point", "text": "point one"})
        on_partial({"kind": "paragraph", "text": "the body"})
        return summary

    monkeypatch.setattr(server.core, "summarize_url", fake)
    monkeypatch.setattr(server, "_start_seg_prefetch", lambda *a, **k: None)
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ"}, _auth())
    types = [e["type"] for e in events]
    assert types == ["meta", "partial", "partial", "result"]
    meta = events[0]
    assert meta["title"] == "Cool Title" and meta["channel"] == "Chan"
    assert meta["original_length"] and meta["video_id"] == "dQw4w9WgXcQ"
    assert events[1] == {"type": "partial", "kind": "key_point", "text": "point one"}
    assert events[2] == {"type": "partial", "kind": "paragraph", "text": "the body"}


def test_requested_model_is_pinned_for_the_request(srv, monkeypatch):
    _, port = srv
    seen = []

    def fake(url, ratio, lang, *, timeout, max_chars, on_progress=None,
             on_partial=None, _prefetched=None):
        seen.append(server.claude_client.current_model())
        return _summary()

    monkeypatch.setattr(server.core, "summarize_url", fake)
    monkeypatch.setattr(server, "_start_seg_prefetch", lambda *a, **k: None)
    _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ", "model": "sonnet"}, _auth())
    _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ"}, _auth())
    assert seen == ["sonnet", None]          # no model -> the CLI's default


def test_unknown_model_is_refused_not_defaulted(srv):
    """It ends up in argv — refuse it outright."""
    _, port = srv
    for path, body in [("/summarize/stream", {"url": "x", "model": "gpt-4"}),
                       ("/ask/stream", {"url": "x", "question": "q", "model": "--help"})]:
        status, payload, _ = _req(port, "POST", path, body, _auth())
        assert status == 400 and payload["error"] == "unknown model", path


def test_segment_prefetch_inherits_the_request_model(monkeypatch):
    """It runs on its own thread, where the request's model scope doesn't reach."""
    seen, done = [], threading.Event()

    def fake_select(url, ratio, lang, *, timeout, _prefetched, on_progress=None,
                    on_segment_ready=None):
        seen.append(server.claude_client.current_model())
        done.set()
        return _prefetched[0], []

    monkeypatch.setattr(server.core, "select_segments", fake_select)
    meta = VideoMeta("prefetchvid", "T", "C", 1000, {}, {})
    server._start_seg_prefetch(meta, [], "sonnet")
    assert done.wait(5)
    assert seen == ["sonnet"]


def test_ask_uses_the_requested_model(srv, monkeypatch):
    _, port = srv
    _stub_transcript(monkeypatch)
    seen = []

    def answer(*a, **k):
        seen.append(server.claude_client.current_model())
        k["on_delta"]("ok")
        return "ok"

    monkeypatch.setattr(server.ask, "stream_answer", answer)
    _read_ndjson(port, _ask_body(model="sonnet"), _auth(), path="/ask/stream")
    assert seen == ["sonnet"]


def test_token_persists_across_calls(monkeypatch, tmp_path):
    tf = tmp_path / "token"
    monkeypatch.setattr(server, "TOKEN_FILE", tf)
    t1, p1 = server.load_or_create_token(None)
    assert p1 and tf.exists()
    t2, p2 = server.load_or_create_token(None)
    assert t2 == t1 and p2                     # stable across restarts
    if os.name != "nt":
        # Windows has no POSIX mode bits; the file is protected by the profile
        # directory's ACLs instead. See config.restrict_to_owner.
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


# --- segment progress ---------------------------------------------------------

def test_progress_reports_position_in_the_video_not_elapsed_time():
    """It used to tick "(5s)… (10s)…" off a clock, which said nothing about how far
    along the selection actually was."""
    half = {"found": 3, "pos_ms": 300_000, "duration_ms": 600_000}
    msg, pct = server._seg_progress_line(half, "1570 cues", elapsed=5)
    assert pct == 62.5                       # 30 + 65 * 0.5
    assert "62%" in msg and "3 clips so far" in msg
    assert "5s" not in msg


def test_progress_before_the_first_clip_stays_below_the_real_range():
    """Nothing to measure yet — the crawl must not overshoot where real progress
    starts, or the bar would jump backwards once a clip arrives."""
    msg, pct = server._seg_progress_line({}, "cues", elapsed=120)
    assert pct <= 30 and "Reading the transcript" in msg
    _, real = server._seg_progress_line(
        {"found": 1, "pos_ms": 1, "duration_ms": 600_000}, "cues", elapsed=120)
    assert real >= pct


def test_progress_is_capped_and_singular_for_one_clip():
    msg, pct = server._seg_progress_line(
        {"found": 1, "pos_ms": 999_999, "duration_ms": 600_000}, "cues", 1)
    assert pct == 95 and "1 clip so far" in msg      # not "1 clips"


def test_prefetch_publishes_progress_as_it_finds_clips(monkeypatch):
    seen, done = [], threading.Event()

    def fake_select(url, ratio, lang, *, timeout, _prefetched, on_progress=None,
                    on_segment_ready=None):
        assert on_segment_ready is not None       # must take the streaming path
        on_segment_ready({"start": 10.0, "end": 60.0, "label": "0:10"})
        seen.append(next((dict(v) for k, v in server._seg_progress.items()
                          if k[0] == "progvid"), {}))
        done.set()
        return _prefetched[0], []

    monkeypatch.setattr(server.core, "select_segments", fake_select)
    meta = VideoMeta("progvid", "T", "C", 600_000, {}, {})
    server._start_seg_prefetch(meta, [])
    assert done.wait(5)
    assert seen[0]["found"] == 1 and seen[0]["pos_ms"] == 60_000
    assert seen[0]["duration_ms"] == 600_000
    time.sleep(0.2)
    assert not any(k[0] == "progvid" for k in server._seg_progress)  # cleaned up


# --- trim strength ------------------------------------------------------------

def test_selections_are_cached_per_trim_strength(monkeypatch):
    """A tighter cut must not be served the looser selection prefetched earlier."""
    meta = VideoMeta("ratiovid", "T", "C", 600_000, {}, {})
    key = lambda r: server._seg_key("ratiovid", r)
    server._seg_cache_put(key(None), meta, [{"start": 0, "end": 300}])
    server._seg_cache_put(key(0.25), meta, [{"start": 0, "end": 100}])
    assert server._seg_cache_get(key(None))[1][0]["end"] == 300
    assert server._seg_cache_get(key(0.25))[1][0]["end"] == 100
    assert server._seg_cache_get(key(0.4)) is None      # not yet selected


def test_auto_and_an_explicit_ratio_are_different_keys():
    assert server._seg_key("v", None) != server._seg_key("v", 0.25)
    assert server._seg_key("v", 0.25) == server._seg_key("v", 0.25000001)  # rounded


def test_segment_ratio_from_the_summarize_body():
    """Separate from `ratio`, which shapes the written summary."""
    assert server._segment_ratio({"segment_ratio": 0.4}) == 0.4
    assert server._segment_ratio({"segment_ratio": "0.4"}) == 0.4
    assert server._segment_ratio({}) is None
    for bad in ["auto", None, 0, -1, 1.5, [], {"a": 1}]:
        assert server._segment_ratio({"segment_ratio": bad}) is None, bad
    assert server._segment_ratio("not a dict") is None


def test_prefetch_uses_the_pages_trim_strength(srv, monkeypatch):
    """Otherwise it caches a selection under a key nothing will ask for."""
    _, port = srv
    seen = {}

    def fake(url, ratio, lang, *, timeout, max_chars, on_progress=None,
             on_partial=None, _prefetched=None):
        base = _summary()
        # The prefetch only starts when there's a transcript to select from.
        return core.Summary(base.meta, base.result, base.cue_count, ["a cue"])

    def fake_prefetch(meta, cues, model=None, ratio=None, source=None):
        seen["ratio"] = ratio

    monkeypatch.setattr(server.core, "summarize_url", fake)
    monkeypatch.setattr(server, "_start_seg_prefetch", fake_prefetch)
    _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ", "segment_ratio": 0.25},
                 _auth())
    assert seen["ratio"] == 0.25
    _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ"}, _auth())
    assert seen["ratio"] is None


# --- transcripts on disk ------------------------------------------------------

def test_transcript_survives_a_restart(monkeypatch, tmp_path):
    """The cache was memory-only, so every `tldw serve` restart re-fetched every
    video — and YouTube answers bursts with 429."""
    from youtube_tldw import txcache
    monkeypatch.setattr(txcache, "CACHE_DIR", tmp_path)
    from youtube_tldw.transcript import Cue
    meta = VideoMeta("diskvid", "T", "C", 1000,
                     {"en": [{"url": "https://example/caption-track"}]}, {})
    cues = [Cue(0, 900, "hello"), Cue(900, 1800, "there")]

    server._cache_put("diskvid", meta, cues)
    with server._cache_lock:                      # simulate a restart
        server._transcript_cache.clear()

    hit = server._cache_get("diskvid")
    assert hit is not None, "re-fetched instead of reading the cache"
    got_meta, got_cues = hit
    assert got_meta.video_id == "diskvid" and got_meta.duration_ms == 1000
    # Caption-track URL maps are deliberately not kept: they're only ever used on
    # freshly fetched metadata, and they were most of each file.
    assert got_meta.subtitles == {} and got_meta.auto_captions == {}
    assert [(c.start_ms, c.end_ms, c.text) for c in got_cues] == \
        [(0, 900, "hello"), (900, 1800, "there")]
    with server._cache_lock:                      # and it's back in memory
        assert "diskvid" in server._transcript_cache


def test_expired_disk_entries_are_ignored(monkeypatch, tmp_path):
    from youtube_tldw import txcache
    monkeypatch.setattr(txcache, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(txcache, "TTL_SECONDS", -1)
    from youtube_tldw.transcript import Cue
    txcache.put("oldvid", VideoMeta("oldvid", "T", "C", 1, {}, {}), [Cue(0, 1, "x")])
    assert txcache.get("oldvid") is None


def test_a_corrupt_cache_file_is_a_miss_not_an_error(monkeypatch, tmp_path):
    from youtube_tldw import txcache
    monkeypatch.setattr(txcache, "CACHE_DIR", tmp_path)
    (tmp_path / "badvid.json").write_text("{ this is not json")
    assert txcache.get("badvid") is None
    (tmp_path / "shortvid.json").write_text('{"saved_at": 0}')
    assert txcache.get("shortvid") is None


def test_caching_never_breaks_the_request_it_speeds_up(monkeypatch, tmp_path):
    from youtube_tldw import txcache
    monkeypatch.setattr(txcache, "CACHE_DIR", tmp_path / "nope")
    monkeypatch.setattr(txcache, "_prune", lambda: 1 / 0)      # anything at all
    txcache.put("v", VideoMeta("v", "T", "C", 1, {}, {}), ["not even a cue"])
    assert txcache.get("v") is None


def test_summarizing_again_does_not_refetch_the_video(srv, monkeypatch, tmp_path):
    """Summarizing wrote to the transcript cache and read from it never, so every
    server restart re-fetched from YouTube — the surest way to get 429'd."""
    _, port = srv
    from youtube_tldw.transcript import Cue
    meta = VideoMeta("dQw4w9WgXcQ", "Cool Title", "Chan", 600_000, {}, {})
    cues = [Cue(0, 900, "hello")]
    fetches, prefetched = [], []

    def fake_fetch(vid, lang="en", *, on_progress=None, pcts=(3, 7, 13)):
        fetches.append(vid)
        return meta, cues

    def fake_summarize(cues_, channel, title, ratio, *, timeout, on_partial=None,
                       on_progress=None):
        return TextResult(["k"], "body", 0.2, "why")

    monkeypatch.setattr(server.core, "fetch_transcript", fake_fetch)
    monkeypatch.setattr(server.core.summarize, "summarize_text", fake_summarize)
    monkeypatch.setattr(server, "_start_seg_prefetch",
                        lambda *a, **k: prefetched.append(1))

    body = {"url": "https://youtu.be/dQw4w9WgXcQ"}
    assert _read_ndjson(port, body, _auth())[-1]["type"] == "result"
    assert fetches == ["dQw4w9WgXcQ"]           # first time: fetched

    with server._cache_lock:                    # simulate a server restart
        server._transcript_cache.clear()
    assert _read_ndjson(port, body, _auth())[-1]["type"] == "result"
    assert fetches == ["dQw4w9WgXcQ"], "re-fetched a transcript we already had"


def test_cached_transcript_lookup_survives_a_bad_url():
    assert server._cached_transcript("https://example.com/not-a-video") is None
    assert server._cached_transcript("https://youtu.be/dQw4w9WgXcQ") is None


# --- summary cache ------------------------------------------------------------

def _count_summaries(monkeypatch, port):
    """Summarize twice, returning how many times the model was actually called."""
    from youtube_tldw.transcript import Cue
    meta = VideoMeta("dQw4w9WgXcQ", "Cool Title", "Chan", 600_000, {}, {})
    calls = []

    monkeypatch.setattr(server.core, "fetch_transcript",
                        lambda vid, lang="en", **k: (meta, [Cue(0, 900, "hi")]))
    monkeypatch.setattr(server.core.summarize, "summarize_text",
                        lambda *a, **k: calls.append(1) or TextResult(
                            ["k"], "body", 0.2, "why"))
    monkeypatch.setattr(server, "_start_seg_prefetch", lambda *a, **k: None)
    return calls


def test_a_second_summary_costs_no_model_call(srv, monkeypatch):
    """The page's own cache dies with the service worker or an extension reload —
    exactly when paying for the summary again hurts most."""
    _, port = srv
    calls = _count_summaries(monkeypatch, port)
    body = {"url": "https://youtu.be/dQw4w9WgXcQ"}

    first = _read_ndjson(port, body, _auth())[-1]
    assert first["type"] == "result" and not first.get("cached")
    assert calls == [1]

    second = _read_ndjson(port, body, _auth())[-1]
    assert second["type"] == "result" and second["cached"] is True
    assert calls == [1], "summarized again instead of using the cache"
    assert second["summary_md"] == first["summary_md"]
    assert second["title"] == first["title"]


def test_a_cached_summary_still_sends_meta_first(srv, monkeypatch):
    """The page draws its layout from meta; a cache hit must not skip it."""
    _, port = srv
    _count_summaries(monkeypatch, port)
    body = {"url": "https://youtu.be/dQw4w9WgXcQ"}
    _read_ndjson(port, body, _auth())
    events = _read_ndjson(port, body, _auth())
    assert [e["type"] for e in events] == ["meta", "result"]
    assert events[0]["title"] == "Cool Title" and events[0]["original_length"]


def test_the_key_separates_model_ratio_and_prompt(monkeypatch):
    base = server._summary_key("vid", None)
    with server.claude_client.use_model("sonnet"):
        assert server._summary_key("vid", None) != base      # model
    assert server._summary_key("vid", 0.25) != base          # trim ratio
    assert server._summary_key("other", None) != base        # video
    monkeypatch.setattr(server.summarize, "PROMPT_FINGERPRINT", "changed")
    assert server._summary_key("vid", None) != base          # reworded prompt


def test_a_reworded_prompt_invalidates_cached_summaries(srv, monkeypatch):
    """Editing a prompt used to be invisible to a cache, which would then serve
    text the current code would never produce."""
    _, port = srv
    calls = _count_summaries(monkeypatch, port)
    body = {"url": "https://youtu.be/dQw4w9WgXcQ"}
    _read_ndjson(port, body, _auth())
    monkeypatch.setattr(server.summarize, "PROMPT_FINGERPRINT", "after-an-edit")
    _read_ndjson(port, body, _auth())
    assert calls == [1, 1], "served a summary written by the old prompt"


def test_a_corrupt_summary_file_is_a_miss(monkeypatch, tmp_path):
    from youtube_tldw import txcache
    monkeypatch.setattr(txcache, "SUMMARY_DIR", tmp_path)
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "k.json").write_text("{not json")
    assert txcache.get_summary("k") is None
    txcache.put_summary("k2", {"summary_md": "x"})
    assert txcache.get_summary("k2") == {"summary_md": "x"}


def test_refresh_reruns_the_model_but_keeps_the_transcript(srv, monkeypatch):
    """For comparing two versions of the same video after a tweak. Re-fetching the
    video would only risk a rate limit."""
    _, port = srv
    from youtube_tldw.transcript import Cue
    meta = VideoMeta("dQw4w9WgXcQ", "Cool Title", "Chan", 600_000, {}, {})
    fetches, calls = [], []
    monkeypatch.setattr(server.core, "fetch_transcript",
                        lambda vid, lang="en", **k: fetches.append(vid) or
                        (meta, [Cue(0, 900, "hi")]))
    monkeypatch.setattr(server.core.summarize, "summarize_text",
                        lambda *a, **k: calls.append(1) or TextResult(
                            ["k"], "body", 0.2, "why"))
    monkeypatch.setattr(server, "_start_seg_prefetch", lambda *a, **k: None)
    body = {"url": "https://youtu.be/dQw4w9WgXcQ"}

    _read_ndjson(port, body, _auth())
    _read_ndjson(port, body, _auth())
    assert calls == [1] and fetches == ["dQw4w9WgXcQ"]      # second was cached

    forced = _read_ndjson(port, {**body, "refresh": True}, _auth())[-1]
    assert calls == [1, 1], "refresh did not re-run the model"
    assert fetches == ["dQw4w9WgXcQ"], "refresh re-fetched the video"
    assert not forced.get("cached")

    _read_ndjson(port, body, _auth())                        # and it re-cached
    assert calls == [1, 1]


def test_the_key_separates_backends(monkeypatch):
    """A summary written by somebody's local model must never be served as if the
    configured backend had produced it."""
    base = server._summary_key("vid", None)
    monkeypatch.setenv("TLDW_LLM_CMD", "ollama run llama3")
    assert server._summary_key("vid", None) != base
    monkeypatch.setenv("TLDW_LLM_CMD", "llm -m gpt-4o")
    assert server._summary_key("vid", None) != base


# --- transcript supplied by the page ------------------------------------------

VTT = ("WEBVTT\n\n00:00:00.000 --> 00:00:02.000\nhello there\n\n"
       "00:00:02.000 --> 00:00:04.000\nsecond line\n")


def test_the_page_can_supply_the_transcript(srv, monkeypatch):
    """The whole point: the browser already has this, in its own session, so the
    server asks YouTube for nothing."""
    _, port = srv
    monkeypatch.setattr(server.core, "fetch_transcript",
                        lambda *a, **k: pytest.fail("contacted YouTube"))
    monkeypatch.setattr(server.core.summarize, "summarize_text",
                        lambda cues, channel, title, ratio, **k: TextResult(
                            [f"{len(cues)} cues from {channel}"], title, 0.2, ""))
    monkeypatch.setattr(server, "_start_seg_prefetch", lambda *a, **k: None)
    body = {"url": "https://youtu.be/dQw4w9WgXcQ",
            "page": {"title": "Page Title", "channel": "Page Channel",
                     "duration_ms": 600_000, "captions": VTT}}
    result = _read_ndjson(port, body, _auth())[-1]
    assert result["type"] == "result"
    assert result["key_points"] == ["2 cues from Page Channel"]
    assert result["title"] == "Page Title"


def test_a_page_transcript_is_reused_by_questions(srv, monkeypatch):
    """It lands in the same cache, so follow-ups never fetch either."""
    _, port = srv
    monkeypatch.setattr(server.core, "fetch_transcript",
                        lambda *a, **k: pytest.fail("contacted YouTube"))
    monkeypatch.setattr(server.core.summarize, "summarize_text",
                        lambda *a, **k: TextResult(["k"], "b", 0.2, ""))
    monkeypatch.setattr(server, "_start_seg_prefetch", lambda *a, **k: None)
    _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                        "page": {"captions": VTT}}, _auth())
    assert server._cache_get("dQw4w9WgXcQ") is not None


@pytest.mark.parametrize("page", [
    None, "a string", {}, {"captions": ""}, {"captions": "   "},
    {"captions": "this is not a caption file at all"},
    {"captions": 12345},
])
def test_junk_from_the_page_falls_back_to_fetching(srv, monkeypatch, page):
    """Untrusted input: anything unusable means fetch it properly, not fail."""
    _, port = srv
    fetched = []
    from youtube_tldw.transcript import Cue
    monkeypatch.setattr(server.core, "fetch_transcript",
                        lambda vid, lang="en", **k: fetched.append(vid) or
                        (VideoMeta(vid, "T", "C", 1000, {}, {}), [Cue(0, 900, "x")]))
    monkeypatch.setattr(server.core.summarize, "summarize_text",
                        lambda *a, **k: TextResult(["k"], "b", 0.2, ""))
    monkeypatch.setattr(server, "_start_seg_prefetch", lambda *a, **k: None)
    _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ", "page": page}, _auth())
    assert fetched == ["dQw4w9WgXcQ"]


def test_page_metadata_is_bounded_and_has_fallbacks():
    """Title and channel reach a prompt and a filename; duration reaches arithmetic."""
    body = {"page": {"title": "T" * 5000, "channel": "", "duration_ms": -5,
                     "captions": VTT}}
    meta, cues = server._page_transcript(body, "https://youtu.be/dQw4w9WgXcQ")
    assert len(meta.title) <= 300
    assert meta.channel == "(unknown)"
    assert meta.duration_ms == cues[-1].end_ms      # implausible value ignored
    assert meta.video_id == "dQw4w9WgXcQ"


# --- the transcript as the page's own panel rendered it -------------------------
#
# This is the path that keeps the server from talking to YouTube at all. The older
# `captions` shape is still accepted so an un-updated extension keeps working.

PANEL_CUES = [{"start_ms": 0, "text": "first line"},
              {"start_ms": 5000, "text": "second line"}]


def test_panel_cues_are_used_without_contacting_youtube(srv, monkeypatch):
    _, port = srv
    monkeypatch.setattr(server.core, "fetch_transcript",
                        lambda *a, **k: pytest.fail("contacted YouTube"))
    monkeypatch.setattr(server.core.summarize, "summarize_text",
                        lambda *a, **k: TextResult(["k"], "b", 0.2, ""))
    monkeypatch.setattr(server, "_start_seg_prefetch", lambda *a, **k: None)
    _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                        "page": {"cues": PANEL_CUES, "title": "T", "channel": "C"}},
                 _auth())
    assert server._cache_get("dQw4w9WgXcQ") is not None


def test_panel_cues_carry_the_pages_metadata():
    body = {"page": {"cues": PANEL_CUES, "title": "A Title", "channel": "A Channel",
                     "duration_ms": 600_000}}
    meta, cues = server._page_transcript(body, "https://youtu.be/dQw4w9WgXcQ")
    assert (meta.title, meta.channel) == ("A Title", "A Channel")
    assert meta.duration_ms == 600_000
    assert [c.text for c in cues] == ["first line", "second line"]
    assert cues[-1].end_ms == 600_000       # the last line runs to the end


def test_panel_cues_without_a_duration_fall_back_to_the_last_cue():
    body = {"page": {"cues": PANEL_CUES}}
    meta, cues = server._page_transcript(body, "https://youtu.be/dQw4w9WgXcQ")
    assert meta.duration_ms == cues[-1].end_ms


@pytest.mark.parametrize("cues", [
    [], "not a list", [{}], [{"start_ms": "x", "text": "y"}], [None],
])
def test_unusable_panel_cues_fall_back_to_fetching(cues):
    """Same rule as every other page input: unusable means fetch it properly."""
    body = {"page": {"cues": cues, "title": "T"}}
    assert server._page_transcript(body, "https://youtu.be/dQw4w9WgXcQ") is None


def test_cues_win_over_a_caption_track_when_both_arrive():
    body = {"page": {"cues": PANEL_CUES, "captions": VTT}}
    _, cues = server._page_transcript(body, "https://youtu.be/dQw4w9WgXcQ")
    assert [c.text for c in cues] == ["first line", "second line"]


def test_an_older_extension_still_gets_its_caption_track_parsed():
    body = {"page": {"captions": VTT}}
    result = server._page_transcript(body, "https://youtu.be/dQw4w9WgXcQ")
    assert result is not None and result[1]


# --- where a transcript came from ----------------------------------------------
#
# A transcript produced from audio has a different accuracy profile from a caption
# track, costs minutes rather than seconds, and is keyed by the model that made it.
# None of that may break the cache files written before any of it existed.

def test_a_cache_file_from_before_provenance_is_still_a_hit(monkeypatch, tmp_path):
    """Reading `source` as a required key would turn every pre-upgrade entry into a
    miss — and on a captionless video a miss now costs minutes, not one fetch."""
    monkeypatch.setattr(txcache, "CACHE_DIR", tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "dQw4w9WgXcQ.json").write_text(json.dumps({
        "saved_at": time.time(),
        "meta": {"video_id": "dQw4w9WgXcQ", "title": "T", "channel": "C",
                 "duration_ms": 1000, "subtitles": {}, "auto_captions": {}},
        "cues": [[0, 900, "hello"]],          # note: no "source" key at all
    }), encoding="utf-8")
    hit = txcache.get("dQw4w9WgXcQ")
    assert hit is not None
    meta, cues, source = hit
    assert [c.text for c in cues] == ["hello"]
    assert source == txcache.CAPTIONS      # an unmarked entry is a caption track


def test_provenance_survives_a_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(txcache, "CACHE_DIR", tmp_path)
    from youtube_tldw.transcript import Cue
    meta = VideoMeta("dQw4w9WgXcQ", "T", "C", 1000, {}, {})
    txcache.put("dQw4w9WgXcQ", meta, [Cue(0, 900, "x")], "audio:small")
    assert txcache.get("dQw4w9WgXcQ")[2] == "audio:small"


def test_a_nonsense_source_reads_as_captions(monkeypatch, tmp_path):
    monkeypatch.setattr(txcache, "CACHE_DIR", tmp_path)
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "dQw4w9WgXcQ.json").write_text(json.dumps({
        "saved_at": time.time(),
        "meta": {"video_id": "dQw4w9WgXcQ", "title": "T", "channel": "C",
                 "duration_ms": 1000, "subtitles": {}, "auto_captions": {}},
        "cues": [[0, 900, "hello"]],
        "source": {"not": "a string"},
    }), encoding="utf-8")
    assert txcache.get("dQw4w9WgXcQ")[2] == txcache.CAPTIONS


def test_the_in_memory_cache_reports_provenance_too():
    from youtube_tldw.transcript import Cue
    meta = VideoMeta("dQw4w9WgXcQ", "T", "C", 1000, {}, {})
    server._cache_put("dQw4w9WgXcQ", meta, [Cue(0, 900, "x")], "audio:small")
    assert server._cache_source("dQw4w9WgXcQ") == "audio:small"
    assert server._cache_get("dQw4w9WgXcQ") is not None     # still a 2-tuple


def test_touching_a_cached_transcript_keeps_its_provenance():
    """An active Q&A renews the TTL; that must not quietly relabel the source."""
    from youtube_tldw.transcript import Cue
    meta = VideoMeta("dQw4w9WgXcQ", "T", "C", 1000, {}, {})
    server._cache_put("dQw4w9WgXcQ", meta, [Cue(0, 900, "x")], "audio:small")
    server._cache_get("dQw4w9WgXcQ", touch=True)
    assert server._cache_source("dQw4w9WgXcQ") == "audio:small"


def test_an_unknown_video_is_captions_by_default():
    assert server._cache_source("neverSeenXX") == txcache.CAPTIONS


# --- transcribing a video that has no captions ---------------------------------
#
# The expensive path: minutes of CPU, started only on purpose, bounded at both
# ends, and never run twice for the same video.

def _captionless(monkeypatch, duration_ms=600_000):
    """Make summarize fail the way a video with no caption track fails."""
    meta = VideoMeta("dQw4w9WgXcQ", "No Captions", "Chan", duration_ms, {}, {})

    def fake(url, ratio, lang, *, timeout, max_chars, on_progress=None,
             on_partial=None, _prefetched=None):
        if _prefetched:                      # transcription supplied the cues
            return _summary()
        exc = NoTranscriptError("This video has no subtitles or auto-captions.")
        exc.meta = meta
        raise exc

    monkeypatch.setattr(server.core, "summarize_url", fake)
    monkeypatch.setattr(server, "_start_seg_prefetch", lambda *a, **k: None)
    return meta


def _record_transcriptions(monkeypatch):
    """Record every _start_transcription call so the test's own thread can assert.

    A pytest.fail passed in as a stub runs inside the HTTP handler thread, where
    socketserver.handle_error swallows it -- the test passes no matter what the
    server does. Four guards in this file were decorative for exactly that reason.
    """
    started = []
    monkeypatch.setattr(server, "_start_transcription",
                        lambda meta, model: started.append((meta.video_id, model)))
    return started


def _kinds(events):
    return [e.get("type") for e in events]


def test_off_keeps_todays_error(srv, monkeypatch):
    _, port = srv
    _captionless(monkeypatch)
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "off"}, _auth())
    assert "needs_transcription" not in _kinds(events)
    assert events[-1]["status"] == 422


def test_an_absent_setting_keeps_the_old_behaviour_exactly(srv, monkeypatch):
    """An extension that knows about this always sends the field. One that doesn't
    has no branch for `needs_transcription` and would surface "the summary stream
    ended unexpectedly" instead of the clear 422 it used to get."""
    _, port = srv
    _captionless(monkeypatch)
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    started = _record_transcriptions(monkeypatch)
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ"}, _auth())
    assert "needs_transcription" not in _kinds(events)
    assert events[-1]["status"] == 422
    assert started == []


def test_a_ceiling_of_true_is_not_a_one_minute_ceiling():
    """int(True) == 1, which would refuse every video over sixty seconds."""
    import youtube_tldw.config as cfg
    real = cfg.get
    try:
        cfg.get = lambda k, *a: True if k == "whisper_max_minutes" else real(k, *a)
        assert server._max_transcribe_ms() == server.MAX_TRANSCRIBE_MS
    finally:
        cfg.get = real


def test_an_unknown_setting_is_refused_not_defaulted(srv, monkeypatch):
    """It decides whether we spend minutes of someone's CPU."""
    _, port = srv
    _captionless(monkeypatch)
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "sometimes"}, _auth())
    assert events[-1]["status"] == 400


def test_the_offer_carries_a_real_estimate(srv, monkeypatch):
    _, port = srv
    _captionless(monkeypatch, duration_ms=45 * 60 * 1000)
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "ask"}, _auth())
    offer = next(e for e in events if e["type"] == "needs_transcription")
    assert offer["duration_ms"] == 45 * 60 * 1000
    assert 7 * 60 <= offer["estimate_s"] <= 9 * 60      # the measured 5.5x
    assert offer["installed"] is True
    assert offer["title"] == "No Captions"


def test_ask_does_not_transcribe_until_it_is_accepted(srv, monkeypatch):
    _, port = srv
    _captionless(monkeypatch)
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    started = _record_transcriptions(monkeypatch)
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "ask"}, _auth())
    assert started == []
    assert "needs_transcription" in _kinds(events)


def test_off_beats_an_accepted_offer(srv, monkeypatch):
    """`transcribe: true` is a client assertion; `off` is the user's own answer."""
    _, port = srv
    _captionless(monkeypatch)
    started = _record_transcriptions(monkeypatch)
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "off", "transcribe": True}, _auth())
    assert events[-1]["status"] == 422
    assert started == []


def test_a_server_side_pin_overrides_whatever_the_client_sends(srv, monkeypatch):
    _, port = srv
    _captionless(monkeypatch)
    monkeypatch.setattr(server.config, "get",
                        lambda k, *a: "off" if k == "whisper" else None)
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "always"}, _auth())
    assert events[-1]["status"] == 422


def test_a_video_over_the_ceiling_is_refused_before_any_audio(srv, monkeypatch):
    _, port = srv
    _captionless(monkeypatch, duration_ms=5 * 3600 * 1000)
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    started = _record_transcriptions(monkeypatch)
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "always"}, _auth())
    assert events[-1]["status"] == 413
    assert "whisper-max-minutes" in events[-1]["error"]
    assert started == []


def test_a_missing_dependency_is_reported_before_anything_is_downloaded(srv,
                                                                        monkeypatch):
    _, port = srv
    _captionless(monkeypatch)
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: False)
    started = _record_transcriptions(monkeypatch)
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "always", "transcribe": True}, _auth())
    assert started == []
    offer = next(e for e in events if e["type"] == "needs_transcription")
    assert offer["installed"] is False
    assert "pipx inject" in offer["install_hint"]


def test_always_transcribes_and_then_summarizes(srv, monkeypatch):
    _, port = srv
    _captionless(monkeypatch)
    from youtube_tldw.transcript import Cue
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video",
                        lambda vid, wd, **k: [Cue(0, 900, "spoken words")])
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "always"}, _auth())
    assert events[-1]["type"] == "result"
    assert server._cache_source("dQw4w9WgXcQ").startswith("audio:")


def test_a_transcribed_summary_says_where_its_words_came_from(srv, monkeypatch):
    _, port = srv
    _captionless(monkeypatch)
    from youtube_tldw.transcript import Cue
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video",
                        lambda vid, wd, **k: [Cue(0, 900, "spoken words")])
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "always"}, _auth())
    assert events[-1]["transcript_source"].startswith("audio:")


def test_two_requests_for_one_video_run_one_transcription(srv, monkeypatch):
    """Two tabs must cost one eight-minute job -- and the second must get the
    answer, not a 429 or a hang. Asserting only the run count would pass against
    a second caller that failed outright."""
    _, port = srv
    _captionless(monkeypatch)
    from youtube_tldw.transcript import Cue
    runs = []
    release = threading.Event()

    def slow(vid, wd, **k):
        runs.append(vid)
        release.wait(15)            # held until both requests have joined
        return [Cue(0, 900, "spoken words")]

    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video", slow)

    # Count arrivals at the job rather than sleeping and hoping: the job cannot
    # finish until we release it, so once both callers are here the dedupe question
    # is settled with no timing window at all.
    real_start = server._start_transcription
    joined = []
    joined_lock = threading.Lock()

    def counting_start(meta, model):
        job = real_start(meta, model)
        with joined_lock:
            joined.append(model)
        return job

    monkeypatch.setattr(server, "_start_transcription", counting_start)
    out = []

    def call():
        try:
            out.append(_read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                           "whisper": "always"}, _auth()))
        except Exception as exc:          # a 429 or a drop is a failure, not a pass
            out.append(exc)

    threads = [threading.Thread(target=call) for _ in range(2)]
    for t in threads:
        t.start()
    deadline = time.monotonic() + 15
    while len(joined) < 2 and time.monotonic() < deadline:
        time.sleep(0.02)
    assert len(joined) == 2, "both requests should have reached the job"
    release.set()
    for t in threads:
        t.join(timeout=20)

    assert runs == ["dQw4w9WgXcQ"]                       # exactly one run
    assert len(out) == 2
    for result in out:
        assert not isinstance(result, Exception), f"a caller failed: {result!r}"
        assert result[-1]["type"] == "result"            # both got the summary


def test_the_second_caller_sees_the_running_jobs_progress(srv, monkeypatch):
    """'The second attaches to it and sees its progress' -- otherwise the second
    tab is a spinner with nothing behind it."""
    from youtube_tldw.transcript import Cue
    meta = VideoMeta("dQw4w9WgXcQ", "T", "C", 600_000, {}, {})
    running, release = threading.Event(), threading.Event()

    def slow(vid, wd, *, on_progress=None, **k):
        if on_progress:
            on_progress("transcribing... 10s / 600s", 42.0)
        running.set()
        release.wait(5)
        return [Cue(0, 900, "spoken")]

    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video", slow)
    job = server._start_transcription(meta, "small")
    assert running.wait(5)
    published = server._tx_progress("dQw4w9WgXcQ")
    assert published is not None
    assert published["percent"] == 42.0
    assert "transcribing" in published["message"]
    # A second caller for the same video joins rather than starting another.
    assert server._start_transcription(meta, "small") is job
    release.set()
    assert job["event"].wait(10)


def test_a_different_model_is_a_different_job(monkeypatch):
    """Keyed by video and model: joining across models would label a summary with
    a model that did not produce it."""
    from youtube_tldw.transcript import Cue
    meta = VideoMeta("dQw4w9WgXcQ", "T", "C", 600_000, {}, {})
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video",
                        lambda vid, wd, **k: [Cue(0, 900, "x")])
    a = server._start_transcription(meta, "small")
    b = server._start_transcription(meta, "medium")
    assert a is not b
    assert a["event"].wait(10) and b["event"].wait(10)


def test_a_retry_does_not_inherit_the_previous_failure(monkeypatch):
    """Joining a job that already gave up means the second click fails for the
    first click's reason."""
    from youtube_tldw.transcript import Cue
    meta = VideoMeta("dQw4w9WgXcQ", "T", "C", 600_000, {}, {})
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video",
                        lambda vid, wd, **k: (_ for _ in ()).throw(
                            TldrError("the first failure")))
    first = server._start_transcription(meta, "small")
    assert first["event"].wait(10)
    assert first["error"] == "the first failure"

    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video",
                        lambda vid, wd, **k: [Cue(0, 900, "second time lucky")])
    second = server._start_transcription(meta, "small")
    assert second is not first
    assert second["event"].wait(10)
    assert second["error"] is None


def test_a_finished_transcript_is_cached_even_if_nobody_is_listening(monkeypatch):
    """It cost minutes; a closed socket is no reason to throw it away."""
    from youtube_tldw.transcript import Cue
    meta = VideoMeta("dQw4w9WgXcQ", "T", "C", 600_000, {}, {})
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video",
                        lambda vid, wd, **k: [Cue(0, 900, "spoken")])
    job = server._start_transcription(meta, "small")      # no request involved
    assert job["event"].wait(10)
    assert server._cache_get("dQw4w9WgXcQ") is not None
    assert server._cache_source("dQw4w9WgXcQ") == "audio:small"


def test_stopping_works_while_the_server_is_at_its_limit(srv, monkeypatch):
    """The request that frees the server must not be the one getting a 429."""
    s, port = srv
    from youtube_tldw.transcript import Cue
    meta = VideoMeta("dQw4w9WgXcQ", "T", "C", 600_000, {}, {})
    started, stopped = threading.Event(), threading.Event()

    def slow(vid, wd, *, should_cancel=None, **k):
        started.set()
        for _ in range(200):
            if should_cancel and should_cancel():
                stopped.set()
                raise TldrError("Transcription cancelled.")
            time.sleep(0.02)
        return [Cue(0, 900, "x")]

    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video", slow)
    server._start_transcription(meta, "small")
    assert started.wait(5)

    # Take every ordinary concurrency slot, as two long requests would.
    held = [s.sem.acquire(blocking=False) for _ in range(server.MAX_CONCURRENCY)]
    try:
        assert all(held)
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/transcribe/stop",
            data=json.dumps({"url": "https://youtu.be/dQw4w9WgXcQ"}).encode(),
            method="POST")
        for k, v in _auth().items():
            req.add_header(k, v)
        with urllib.request.urlopen(req, timeout=5) as r:
            assert json.loads(r.read())["stopped"] is True
    finally:
        for _ in held:
            s.sem.release()
    assert stopped.wait(5)


def test_stopping_something_that_is_not_running_is_not_an_error(srv):
    _, port = srv
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/transcribe/stop",
        data=json.dumps({"url": "https://youtu.be/dQw4w9WgXcQ"}).encode(),
        method="POST")
    for k, v in _auth().items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=5) as r:
        assert json.loads(r.read())["stopped"] is False


def test_stopping_needs_a_token(srv):
    _, port = srv
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/transcribe/stop",
        data=json.dumps({"url": "https://youtu.be/dQw4w9WgXcQ"}).encode(),
        method="POST")
    req.add_header("Content-Type", "application/json")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 401


def test_the_server_announces_a_transcription_before_waiting_on_it(srv, monkeypatch):
    """The panel can't infer this: on "always" it never asked to transcribe, and
    after a close/re-open it has no memory of having done so. Without the event
    there is no stop button and the panel's own timer fires mid-run."""
    _, port = srv
    _captionless(monkeypatch)
    from youtube_tldw.transcript import Cue
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video",
                        lambda vid, wd, **k: [Cue(0, 900, "spoken")])
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "always"}, _auth())
    announced = next(e for e in events if e["type"] == "transcribing")
    assert announced["video_id"] == "dQw4w9WgXcQ"
    assert announced["estimate_s"] > 0
    assert _kinds(events).index("transcribing") < len(events) - 1


def test_a_video_of_unknown_length_is_not_quoted_an_estimate(srv, monkeypatch):
    """yt-dlp reports 0 when it doesn't know. Quoting that would promise "under a
    minute" for a video of any length, and it would slip past the ceiling."""
    _, port = srv
    _captionless(monkeypatch, duration_ms=0)
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "ask"}, _auth())
    offer = next(e for e in events if e["type"] == "needs_transcription")
    assert offer["estimate_s"] == 0        # the client renders this as "unknown"


def test_a_transcription_survives_the_client_hanging_up(srv, monkeypatch):
    """Criterion 8, as a client actually experiences it. This is the stated reason
    the job lives outside the request: _run_stream's emit has no OSError guard, so
    an in-request transcription would be discarded by a closed socket."""
    _, port = srv
    _captionless(monkeypatch)
    from youtube_tldw.transcript import Cue
    release, running = threading.Event(), threading.Event()

    def slow(vid, wd, *, on_progress=None, **k):
        running.set()
        if on_progress:
            on_progress("transcribing...", 20.0)
        release.wait(15)
        return [Cue(0, 900, "words from the audio")]

    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video", slow)

    payload = json.dumps({"url": "https://youtu.be/dQw4w9WgXcQ",
                          "whisper": "always"}).encode()
    request = (b"POST /summarize/stream HTTP/1.1\r\n"
               b"Host: 127.0.0.1\r\n"
               b"Authorization: Bearer " + TOKEN.encode() + b"\r\n"
               b"Origin: " + EXT_ORIGIN.encode() + b"\r\n"
               b"Content-Type: application/json\r\n"
               b"Content-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload)
    sock = socket.create_connection(("127.0.0.1", port), timeout=5)
    sock.sendall(request)
    assert sock.recv(4096)              # the stream has started
    assert running.wait(5)
    sock.close()                        # walk away mid-transcription
    release.set()

    deadline = time.monotonic() + 15
    while server._cache_get("dQw4w9WgXcQ") is None and time.monotonic() < deadline:
        time.sleep(0.02)
    hit = server._cache_get("dQw4w9WgXcQ")
    assert hit is not None, "a finished transcript was thrown away with the socket"
    assert [c.text for c in hit[1]] == ["words from the audio"]
    assert server._cache_source("dQw4w9WgXcQ") == "audio:small"


def test_other_requests_are_served_while_a_transcription_runs(srv, monkeypatch):
    """Waiting is not work. Holding a MAX_CONCURRENCY slot for the length of a
    transcription would 429 every captioned summarize, question and stop -- the
    starvation that putting the job outside the request was meant to prevent."""
    _, port = srv
    from youtube_tldw.transcript import Cue
    release = threading.Event()
    joined = []

    meta = VideoMeta("dQw4w9WgXcQ", "No Captions", "Chan", 600_000, {}, {})

    def captionless_then_normal(url, ratio, lang, *, timeout, max_chars,
                                on_progress=None, on_partial=None, _prefetched=None):
        if "oHg5SJYRHA0" in url:                 # a different, captioned video
            return _summary()
        if _prefetched:
            return _summary()
        exc = NoTranscriptError("no captions")
        exc.meta = meta
        raise exc

    def slow(vid, wd, **k):
        joined.append(vid)
        release.wait(15)
        return [Cue(0, 900, "spoken")]

    monkeypatch.setattr(server.core, "summarize_url", captionless_then_normal)
    monkeypatch.setattr(server, "_start_seg_prefetch", lambda *a, **k: None)
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video", slow)

    blocked = threading.Thread(target=lambda: _read_ndjson(
        port, {"url": "https://youtu.be/dQw4w9WgXcQ", "whisper": "always"}, _auth()))
    blocked.start()
    try:
        deadline = time.monotonic() + 10
        while not joined and time.monotonic() < deadline:
            time.sleep(0.02)
        assert joined, "the transcription never started"
        # A completely unrelated, fully-captioned video must still be served.
        events = _read_ndjson(port, {"url": "https://youtu.be/oHg5SJYRHA0"}, _auth())
        assert events[-1]["type"] == "result"
    finally:
        release.set()
        blocked.join(timeout=20)


def test_changing_the_whisper_model_retranscribes(srv, monkeypatch):
    """Criterion 6. The summary lookup runs before any transcript work, so without
    the source in its key a better model returns a fortnight of the old answer."""
    _, port = srv
    _captionless(monkeypatch)
    from youtube_tldw.transcript import Cue
    models = []

    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video",
                        lambda vid, wd, **k: models.append(k.get("model_size"))
                        or [Cue(0, 900, "spoken")])

    monkeypatch.setattr(server.whisper_transcribe, "resolve_model", lambda: "small")
    _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                        "whisper": "always"}, _auth())
    assert models == ["small"]
    assert server._cache_source("dQw4w9WgXcQ") == "audio:small"

    monkeypatch.setattr(server.whisper_transcribe, "resolve_model", lambda: "medium")
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "always"}, _auth())
    assert models == ["small", "medium"], "the cached small transcript was reused"
    assert events[-1].get("cached") is not True
    assert server._cache_source("dQw4w9WgXcQ") == "audio:medium"


def test_the_same_model_still_uses_the_cache(monkeypatch):
    """The model check must not turn every repeat into another eight minutes."""
    from youtube_tldw.transcript import Cue
    monkeypatch.setattr(server.whisper_transcribe, "resolve_model", lambda: "small")
    meta = VideoMeta("dQw4w9WgXcQ", "T", "C", 1000, {}, {})
    server._cache_put("dQw4w9WgXcQ", meta, [Cue(0, 900, "x")], "audio:small")
    assert server._cached_transcript("https://youtu.be/dQw4w9WgXcQ") is not None


def test_a_caption_transcript_is_never_invalidated_by_the_model(monkeypatch):
    monkeypatch.setattr(server.whisper_transcribe, "resolve_model", lambda: "medium")
    from youtube_tldw.transcript import Cue
    meta = VideoMeta("dQw4w9WgXcQ", "T", "C", 1000, {}, {})
    server._cache_put("dQw4w9WgXcQ", meta, [Cue(0, 900, "x")], txcache.CAPTIONS)
    assert server._cached_transcript("https://youtu.be/dQw4w9WgXcQ") is not None


def test_real_captions_from_the_page_beat_an_audio_transcript(monkeypatch):
    """A video that has since gained a caption track should stop being served a
    machine transcription of itself."""
    from youtube_tldw.transcript import Cue
    monkeypatch.setattr(server.whisper_transcribe, "resolve_model", lambda: "small")
    meta = VideoMeta("dQw4w9WgXcQ", "T", "C", 1000, {}, {})
    server._cache_put("dQw4w9WgXcQ", meta, [Cue(0, 900, "from audio")], "audio:small")
    body = {"page": {"cues": [{"start_ms": 0, "text": "from real captions"}],
                     "title": "T", "channel": "C"}}
    chosen = server._preferred_transcript(body, "https://youtu.be/dQw4w9WgXcQ")
    assert [c.text for c in chosen[1]] == ["from real captions"]


def test_a_transcription_failure_keeps_its_status(srv, monkeypatch):
    """Criterion 13: the message said why, the status said 500 regardless."""
    _, port = srv
    _captionless(monkeypatch)
    from youtube_tldw import TldrTimeoutError
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video",
                        lambda vid, wd, **k: (_ for _ in ()).throw(
                            TldrTimeoutError("took too long")))
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "always"}, _auth())
    assert events[-1]["status"] == 504          # not a blanket 500


def test_silent_audio_is_reported_as_having_no_transcript(srv, monkeypatch):
    _, port = srv
    _captionless(monkeypatch)
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video",
                        lambda vid, wd, **k: (_ for _ in ()).throw(
                            NoTranscriptError("the audio may be silent")))
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "always"}, _auth())
    assert events[-1]["status"] == 422
    assert "silent" in events[-1]["error"]


def test_a_cached_summary_still_says_it_came_from_audio(srv, monkeypatch):
    """The case criterion 14 was written for: the summary-cache path never touches
    a transcript, so provenance has to live in the payload."""
    _, port = srv
    _captionless(monkeypatch)
    from youtube_tldw.transcript import Cue
    monkeypatch.setattr(server.whisper_transcribe, "is_available", lambda: True)
    monkeypatch.setattr(server.whisper_transcribe, "transcribe_video",
                        lambda vid, wd, **k: [Cue(0, 900, "spoken")])
    first = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                "whisper": "always"}, _auth())
    assert first[-1]["transcript_source"] == "audio:small"
    second = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "whisper": "always"}, _auth())
    assert second[-1].get("cached") is True
    assert second[-1]["transcript_source"] == "audio:small"


def test_a_bad_body_to_the_stop_route_is_a_400(srv):
    _, port = srv
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/transcribe/stop",
        data=json.dumps([1, 2]).encode(), method="POST")
    for k, v in _auth().items():
        req.add_header(k, v)
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 400


# --- segment selections: made once, kept, never paid for twice -----------------
#
# Selection is the most expensive step tldw runs (54% of spend), and it used to
# be recomputed on every restart, on every re-open of a cached summary for anyone
# with a numeric segment ratio, and after every regenerate.

def _counting_select(monkeypatch, segments=None):
    calls = []

    def fake(url, ratio, lang, *, timeout, _prefetched=None, max_length_ms=None,
             on_progress=None, on_segment_ready=None):
        calls.append({"ratio": ratio, "model": server.claude_client.current_model(),
                      "max": max_length_ms})
        meta = _prefetched[0] if _prefetched else VideoMeta(
            "dQw4w9WgXcQ", "T", "C", 600_000, {}, {})
        return meta, list(segments or [{"start": 1.0, "end": 9.0, "label": "0:01"}])

    monkeypatch.setattr(server.core, "select_segments", fake)
    return calls


def _wait_prefetch_idle(timeout=5):
    deadline = time.monotonic() + timeout
    while server._seg_prefetch_events and time.monotonic() < deadline:
        time.sleep(0.01)


def _meta_cues():
    from youtube_tldw.transcript import Cue
    return (VideoMeta("dQw4w9WgXcQ", "T", "C", 600_000, {}, {}),
            [Cue(0, 900, "a"), Cue(900, 1800, "b")])


def test_a_selection_survives_a_restart(monkeypatch):
    calls = _counting_select(monkeypatch)
    meta, cues = _meta_cues()
    server._start_seg_prefetch(meta, cues, "opus", 0.25)
    _wait_prefetch_idle()
    with server._cache_lock:
        server._seg_cache.clear()                 # simulate a restart
    server._start_seg_prefetch(meta, cues, "opus", 0.25)
    _wait_prefetch_idle()
    assert len(calls) == 1, "a restart threw away a selection already paid for"


def test_prefetch_checks_its_own_cache_so_no_caller_can_pay_twice(monkeypatch):
    """The check used to sit at one call site and not the other."""
    calls = _counting_select(monkeypatch)
    meta, cues = _meta_cues()
    for _ in range(3):
        server._start_seg_prefetch(meta, cues, "opus", None)
        _wait_prefetch_idle()
    assert len(calls) == 1


def test_reopening_a_cached_summary_with_a_numeric_ratio_doesnt_reselect(srv,
                                                                         monkeypatch):
    """The old check used the summary ratio, which the extension never sends, so
    anyone with a segment ratio paid for a fresh selection on every re-open."""
    _, port = srv
    calls = _counting_select(monkeypatch)
    monkeypatch.setattr(server.core, "summarize_url",
                        lambda *a, **k: core.Summary(_summary().meta, _summary().result,
                                                     2, _meta_cues()[1]))
    body = {"url": "https://youtu.be/dQw4w9WgXcQ", "model": "opus",
            "segment_ratio": 0.25}
    _read_ndjson(port, body, _auth())             # summarize + prefetch
    _wait_prefetch_idle()
    for _ in range(3):                            # re-open, served from summary cache
        events = _read_ndjson(port, body, _auth())
        assert events[-1].get("cached") is True
        _wait_prefetch_idle()
    assert len(calls) == 1


def test_regenerate_redoes_the_summary_not_the_selection(srv, monkeypatch):
    _, port = srv
    calls = _counting_select(monkeypatch)
    monkeypatch.setattr(server.core, "summarize_url",
                        lambda *a, **k: core.Summary(_summary().meta, _summary().result,
                                                     2, _meta_cues()[1]))
    body = {"url": "https://youtu.be/dQw4w9WgXcQ", "model": "opus"}
    _read_ndjson(port, body, _auth())
    _wait_prefetch_idle()
    _read_ndjson(port, {**body, "refresh": True}, _auth())
    _wait_prefetch_idle()
    assert len(calls) == 1


def test_a_selection_by_one_model_is_not_served_to_another(monkeypatch):
    calls = _counting_select(monkeypatch)
    meta, cues = _meta_cues()
    server._start_seg_prefetch(meta, cues, "sonnet", None)
    _wait_prefetch_idle()
    server._start_seg_prefetch(meta, cues, "opus", None)
    _wait_prefetch_idle()
    assert [c["model"] for c in calls] == ["sonnet", "opus"]


def test_a_request_does_not_wait_on_another_models_prefetch(srv, monkeypatch):
    """A prefetch under model A is not something model B's request can use, so
    waiting for it would only add its whole runtime before paying anyway."""
    _, port = srv
    meta, cues = _meta_cues()
    server._cache_put("dQw4w9WgXcQ", meta, cues)
    gate, calls = threading.Event(), []

    def fake(url, ratio, lang, *, timeout, _prefetched=None, max_length_ms=None,
             on_progress=None, on_segment_ready=None):
        model = server.claude_client.current_model()
        calls.append(model)
        if model == "sonnet":
            gate.wait(10)                         # the other model's slow prefetch
        return _prefetched[0], [{"start": 1.0, "end": 9.0, "label": "0:01"}]

    monkeypatch.setattr(server.core, "select_segments", fake)
    server._start_seg_prefetch(meta, cues, "sonnet", None)
    try:
        t0 = time.monotonic()
        events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                     "model": "opus"}, _auth(), path="/segments/stream")
        assert time.monotonic() - t0 < 4, "waited on a prefetch it couldn't use"
        assert events[-1]["type"] in ("segments", "segments_done")
        assert "opus" in calls
    finally:
        gate.set()
        _wait_prefetch_idle()


def test_a_capped_request_neither_reads_nor_overwrites_the_shared_selection(
        srv, monkeypatch):
    _, port = srv
    meta, cues = _meta_cues()
    server._cache_put("dQw4w9WgXcQ", meta, cues)
    shared = [{"start": 1.0, "end": 200.0, "label": "0:01"}]
    server._seg_cache_put(server._seg_key("dQw4w9WgXcQ", None), meta, shared)
    calls = _counting_select(monkeypatch, [{"start": 1.0, "end": 9.0, "label": "0:01"}])
    _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ", "max_length": "30s"},
                 _auth(), path="/segments/stream")
    assert len(calls) == 1                        # didn't read the uncapped one
    assert server._seg_cache_get(server._seg_key("dQw4w9WgXcQ", None))[1] == shared


def test_the_segment_fingerprint_moves_with_the_listing_format(monkeypatch):
    from youtube_tldw import summarize
    before = core._segment_fingerprint()
    monkeypatch.setattr(summarize, "LISTING_FORMAT", "v3: something else")
    assert core._segment_fingerprint() != before


def test_the_segment_fingerprint_moves_with_the_span_constants(monkeypatch):
    before = core._segment_fingerprint()
    monkeypatch.setattr(core, "BOUNDARY_PAD_MS", core.BOUNDARY_PAD_MS + 1)
    assert core._segment_fingerprint() != before


# --- is key-moments prefetch worth what it costs? ------------------------------

def _usage_rows():
    from youtube_tldw import usage
    return usage.read_rows()


def test_selection_rows_say_whether_they_were_prefetched_or_requested(srv,
                                                                       monkeypatch):
    _, port = srv
    from youtube_tldw import usage
    rec = lambda step: usage.record(usage.Usage(step=step, cost_usd=0.01))

    def fake(url, ratio, lang, *, timeout, _prefetched=None, max_length_ms=None,
             on_progress=None, on_segment_ready=None):
        rec("segments")
        meta = _prefetched[0] if _prefetched else _meta_cues()[0]
        return meta, [{"start": 1.0, "end": 9.0, "label": "0:01"}]

    monkeypatch.setattr(server.core, "select_segments", fake)
    meta, cues = _meta_cues()
    server._start_seg_prefetch(meta, cues, "opus", 0.25)
    _wait_prefetch_idle()
    _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ", "ratio": 0.4},
                 _auth(), path="/segments/stream")
    triggers = [r.get("trigger") for r in _usage_rows() if r.get("step") == "segments"]
    assert triggers == ["prefetch", "request"]


def test_every_way_a_selection_reaches_the_panel_is_counted(srv, monkeypatch):
    _, port = srv
    _counting_select(monkeypatch)
    meta, cues = _meta_cues()
    server._cache_put("dQw4w9WgXcQ", meta, cues)
    url = "https://youtu.be/dQw4w9WgXcQ"
    _read_ndjson(port, {"url": url}, _auth(), path="/segments/stream")    # live
    _read_ndjson(port, {"url": url}, _auth(), path="/segments/stream")    # cache
    served = [r["how"] for r in _usage_rows() if r.get("kind") == "segments_served"]
    assert served == ["live", "cache"]


# --- asking for a cached summary before reading the page -------------------------
#
# The panel used to open YouTube's transcript panel (0.5-13s, plus a request to
# YouTube) before anything checked whether the summary was already cached.

def _post_json(port, path, body, headers):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}",
                                 data=json.dumps(body).encode(), method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read())


def _summarize_once(port, monkeypatch, model):
    monkeypatch.setattr(server.core, "summarize_url",
                        lambda *a, **k: core.Summary(_summary().meta, _summary().result,
                                                     2, _meta_cues()[1]))
    monkeypatch.setattr(server, "_start_seg_prefetch", lambda *a, **k: None)
    events = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "model": model}, _auth())
    assert events[-1]["type"] == "result"
    return events[-1]


@pytest.mark.parametrize("model", ["opus", "sonnet"])
def test_a_cached_summary_is_found_without_the_page(srv, monkeypatch, model):
    """Under the request's model: the key includes the backend fingerprint, so a
    lookup outside that scope would quietly miss every time."""
    _, port = srv
    written = _summarize_once(port, monkeypatch, model)
    monkeypatch.setattr(server.core, "summarize_url",
                        lambda *a, **k: pytest.fail("summarized again"))
    got = _post_json(port, "/summarize/cached",
                     {"url": "https://youtu.be/dQw4w9WgXcQ", "model": model}, _auth())
    assert got["type"] == "result" and got["cached"] is True
    assert got["summary_md"] == written["summary_md"]
    assert "stats" in got


def test_the_probe_finds_exactly_what_the_real_request_would(srv, monkeypatch):
    """Same key, not a look-alike: whatever /summarize/stream serves from cache,
    the probe must find, and vice versa."""
    _, port = srv
    _summarize_once(port, monkeypatch, "opus")
    stream = _read_ndjson(port, {"url": "https://youtu.be/dQw4w9WgXcQ",
                                 "model": "opus"}, _auth())
    probe = _post_json(port, "/summarize/cached",
                       {"url": "https://youtu.be/dQw4w9WgXcQ", "model": "opus"}, _auth())
    assert stream[-1].get("cached") is True and probe["type"] == "result"
    assert stream[-1]["summary_md"] == probe["summary_md"]


def test_a_summary_by_another_model_is_a_miss(srv, monkeypatch):
    _, port = srv
    _summarize_once(port, monkeypatch, "sonnet")
    got = _post_json(port, "/summarize/cached",
                     {"url": "https://youtu.be/dQw4w9WgXcQ", "model": "opus"}, _auth())
    assert got == {"type": "miss"}


def test_a_miss_fetches_and_starts_nothing(srv, monkeypatch):
    """Read-only: the panel's own flow does the work on a miss."""
    _, port = srv
    monkeypatch.setattr(server.core, "fetch_transcript",
                        lambda *a, **k: pytest.fail("fetched on a probe"))
    monkeypatch.setattr(server.core, "summarize_url",
                        lambda *a, **k: pytest.fail("summarized on a probe"))
    started = _record_transcriptions(monkeypatch)
    got = _post_json(port, "/summarize/cached",
                     {"url": "https://youtu.be/dQw4w9WgXcQ"}, _auth())
    assert got == {"type": "miss"}
    assert started == []


def test_a_hit_still_gets_key_moments_ready(srv, monkeypatch):
    """What the cached path through /summarize/stream has always done."""
    _, port = srv
    _summarize_once(port, monkeypatch, "opus")
    meta, cues = _meta_cues()
    server._cache_put("dQw4w9WgXcQ", meta, cues)
    seen = []
    monkeypatch.setattr(server, "_start_seg_prefetch",
                        lambda m, c, model=None, ratio=None, source=None:
                        seen.append((model, ratio)))
    _post_json(port, "/summarize/cached", {"url": "https://youtu.be/dQw4w9WgXcQ",
                                           "model": "opus", "segment_ratio": 0.25},
               _auth())
    assert seen == [("opus", 0.25)]


def test_the_probe_needs_a_token(srv):
    _, port = srv
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/summarize/cached",
        data=json.dumps({"url": "https://youtu.be/dQw4w9WgXcQ"}).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req, timeout=5)
    assert e.value.code == 401


@pytest.mark.parametrize("body", [{"url": "not a video"}, {}])
def test_a_bad_probe_is_a_400(srv, body):
    _, port = srv
    with pytest.raises(urllib.error.HTTPError) as e:
        _post_json(port, "/summarize/cached", body, _auth())
    assert e.value.code == 400
