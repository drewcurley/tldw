"""T-020 word timing: align.py and POST /align/stream.

Nothing here needs ffmpeg, yt-dlp or faster-whisper: every one of them is stubbed,
both ways, so the result is the same on a laptop that has them and a CI runner that
doesn't. Word fixtures are made up.
"""

from __future__ import annotations

import importlib.metadata
import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import pytest

from youtube_tldw import TldrError, TldrTimeoutError, align, server, txcache
from youtube_tldw import whisper_transcribe as wt
from youtube_tldw.proc import ProcResult

TOKEN = "test-token-123"
VID = "dQw4w9WgXcQ"
URL = f"https://www.youtube.com/watch?v={VID}"


# --- parse_windows -------------------------------------------------------------

def test_windows_parse_to_int_pairs():
    assert align.parse_windows([{"start_ms": 0, "end_ms": 5000},
                                {"start_ms": 4000, "end_ms": 9000}]) == [(0, 5000), (4000, 9000)]


@pytest.mark.parametrize("raw, field", [
    (None, "windows"), ({}, "windows"), ([], "windows"),
    ([{"start_ms": 0, "end_ms": 1}] * 201, "windows"),
    (["x"], "windows[0]"),
    ([{"start_ms": True, "end_ms": 5}], "windows[0].start_ms"),
    ([{"start_ms": 0, "end_ms": 5.5}], "windows[0].end_ms"),
    ([{"start_ms": 5, "end_ms": 5}], "windows[0]"),
    ([{"start_ms": -1, "end_ms": 5}], "windows[0]"),
    ([{"start_ms": 0, "end_ms": 12_001}], "windows[0]"),
])
def test_bad_windows_name_the_field(raw, field):
    with pytest.raises(align.WindowError) as exc:
        align.parse_windows(raw)
    assert field in str(exc.value)


# --- the ffmpeg cut -------------------------------------------------------------

def test_cut_argv_is_all_server_made(tmp_path):
    audio = tmp_path / "-dQw4w9WgXcQ.m4a"
    argv = align.cut_argv(audio, tmp_path, tmp_path / "w0.wav", 61_500, 70_000)
    assert argv[:4] == ["ffmpeg", "-nostdin", "-loglevel", "error"]
    assert argv[argv.index("-ss") + 1] == "00:01:01.500"
    assert argv[argv.index("-t") + 1] == "00:00:08.500"
    assert argv[argv.index("-i") + 1] == str(audio)       # absolute: never an option
    assert argv[argv.index("-ar") + 1] == "16000" and argv[argv.index("-ac") + 1] == "1"


def test_cut_refuses_a_relative_or_outside_path(tmp_path):
    with pytest.raises(ValueError):
        align.cut_argv(Path("x.m4a"), tmp_path, tmp_path / "w.wav", 0, 1000)
    with pytest.raises(ValueError):
        align.cut_argv(tmp_path.parent / "x.m4a", tmp_path, tmp_path / "w.wav", 0, 1000)


# --- fingerprint and cache key --------------------------------------------------

def _versions(monkeypatch, fw="1.2.1", ct="4.5.0"):
    vals = {"faster-whisper": fw, "ctranslate2": ct}

    def version(name):
        if vals.get(name) is None:
            raise importlib.metadata.PackageNotFoundError(name)
        return vals[name]
    monkeypatch.setattr(align.importlib.metadata, "version", version)
    monkeypatch.setattr(align, "_fingerprint_memo", None)


def test_importing_the_server_computes_no_fingerprint():
    """In a fresh interpreter, where conftest's reset can't hide anything."""
    import subprocess
    import sys
    code = ("import importlib.metadata as m\n"
            "def boom(name): raise AssertionError('version read at import')\n"
            "m.version = boom\n"
            "import youtube_tldw.server, youtube_tldw.align as a\n"
            "assert a._fingerprint_memo is None\n")
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                          timeout=60)
    assert proc.returncode == 0, proc.stderr


def test_fingerprint_is_lazy_and_memoised(monkeypatch):
    _versions(monkeypatch)
    fp = align.fingerprint()
    assert len(fp) == 12 and align.fingerprint() == fp


def test_fingerprint_is_none_without_a_library(monkeypatch):
    _versions(monkeypatch, ct=None)
    assert align.fingerprint() is None


@pytest.mark.parametrize("change", [
    lambda m: _versions(m, fw="9.9"),
    lambda m: _versions(m, ct="9.9"),
    lambda m: m.setattr(align, "ALIGN_SHAPE", 2),
    lambda m: m.setattr(align, "TRANSCRIBE_KWARGS", {**align.TRANSCRIBE_KWARGS, "beam_size": 1}),
    lambda m: m.setattr(align, "MAX_WORDS_PER_WINDOW", 10),
    lambda m: m.setattr(align, "MAX_WORD_CHARS", 10),
    lambda m: m.setattr(align, "SAMPLE_RATE", 8000) or m.setattr(
        align, "_CUT_SHAPE", align._CUT_SHAPE[:-4] + ["8000", "-f", "wav", "-y"]),
    lambda m: m.setattr(wt, "MODEL_COMPUTE_TYPE", "float32"),
    lambda m: m.setattr(wt, "MODEL_DEVICE", "cuda"),
    lambda m: m.setattr(align, "BAD_TIMESTAMP_SLACK_MS", 1),
])
def test_every_fingerprint_input_changes_the_key(monkeypatch, change):
    _versions(monkeypatch)
    before = align.cache_name(VID, 0, 5000, "small", align.fingerprint())
    change(monkeypatch)
    monkeypatch.setattr(align, "_fingerprint_memo", None)
    assert align.cache_name(VID, 0, 5000, "small", align.fingerprint()) != before


def test_the_key_names_the_language_term():
    assert "|lang=auto|" in align.cache_key(VID, 0, 5000, "small", "fp")


def test_the_fingerprinted_cut_shape_matches_the_real_cut(tmp_path):
    """_CUT_SHAPE is cut_argv with its times and paths taken out. If cut_argv gains
    an option and the shape doesn't, the cache would keep old results."""
    argv = align.cut_argv(tmp_path / "a.m4a", tmp_path, tmp_path / "w0.wav", 1000, 6000)
    values = {argv.index(flag) + 1 for flag in ("-ss", "-t", "-i")} | {len(argv) - 1}
    assert [a for k, a in enumerate(argv) if k not in values] == align._CUT_SHAPE


def test_key_covers_video_window_model_and_auto_language():
    names = {align.cache_name(*args, "fp") for args in [
        (VID, 0, 5000, "small"), ("aaaaaaaaaaa", 0, 5000, "small"),
        (VID, 1, 5000, "small"), (VID, 0, 5001, "small"), (VID, 0, 5000, "base")]}
    assert len(names) == 5


# --- the store -------------------------------------------------------------------

WORDS = [[1000, 1400, "blorp"], [1500, 1800, "zarf"]]


def test_window_cache_round_trip():
    align.put_window(VID, 0, 5000, "small", "fp", WORDS)
    assert align.get_window(VID, 0, 5000, "small", "fp") == WORDS
    assert align.get_window(VID, 0, 5000, "small", "other") is None


def test_window_cache_expires(monkeypatch):
    align.put_window(VID, 0, 5000, "small", "fp", WORDS)
    real = time.time()
    monkeypatch.setattr(align.time, "time", lambda: real + txcache.TTL_SECONDS + 5)
    assert align.get_window(VID, 0, 5000, "small", "fp") is None


@pytest.mark.parametrize("mutate", [
    lambda d: d.update(video_id="aaaaaaaaaaa"), lambda d: d.update(start_ms=1),
    lambda d: d.update(end_ms=4999), lambda d: d.update(words="x"),
    lambda d: d.update(words=[[2000, 1000, "x"]]), lambda d: d.update(words=[[0, 6000, "x"]]),
    lambda d: d.update(words=[[0, 100, ""]]), lambda d: d.update(words=[[0, 100, "x" * 65]]),
    lambda d: d.update(words=[[True, 100, "x"]]), lambda d: d.update(words=[[0, 1, "x"]] * 201),
    lambda d: d.pop("saved_at"),
    # False == 0 and 5000.0 == 5000, so these pass the equality check and must be
    # refused by the type check (build review, Data W3).
    lambda d: d.update(start_ms=False), lambda d: d.update(end_ms=5000.0),
    lambda d: d.update(start_ms="0"),
])
def test_a_bad_entry_is_a_miss(mutate):
    align.put_window(VID, 0, 5000, "small", "fp", WORDS)
    path = align.ALIGN_DIR / f"{align.cache_name(VID, 0, 5000, 'small', 'fp')}.json"
    data = json.loads(path.read_text())
    mutate(data)
    path.write_text(json.dumps(data))
    assert align.get_window(VID, 0, 5000, "small", "fp") is None


def test_a_corrupt_entry_is_a_miss():
    align.ALIGN_DIR.mkdir(parents=True, exist_ok=True)
    (align.ALIGN_DIR / f"{align.cache_name(VID, 0, 5000, 'small', 'fp')}.json").write_text("{")
    assert align.get_window(VID, 0, 5000, "small", "fp") is None


def test_prune_enforces_the_store_cap(monkeypatch):
    monkeypatch.setattr(align, "ALIGN_MAX_ENTRIES", 3)
    for i in range(5):
        align.put_window(VID, i, 5000, "small", "fp", WORDS)
    align.prune()
    assert len(list(align.ALIGN_DIR.glob("*.json"))) == 3


def test_txcache_prune_default_cap_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.setattr(txcache, "MAX_ENTRIES", 2)
    for i in range(4):
        (tmp_path / f"{i}.json").write_text("{}")
    txcache._prune(tmp_path)
    assert len(list(tmp_path.glob("*.json"))) == 2


# --- words -----------------------------------------------------------------------

def test_words_become_absolute_ms_and_bad_ones_are_dropped():
    raw = [(0.5, 0.9, " blorp "), (2.0, 1.0, "back"), (-2.0, -1.5, "early"),
           (0.95, 4.6, "edge"), (1.0, 1.2, "   ")]
    assert align.words_from_raw(raw, 10_000, 14_000) == [
        [10_500, 10_900, "blorp"], [10_950, 14_000, "edge"]]


def test_an_infinite_timestamp_is_dropped_not_raised():
    raw = [(float("inf"), 1.0, "x"), (0.5, float("nan"), "y"), (0.5, 0.9, "blorp")]
    assert align.words_from_raw(raw, 10_000, 14_000) == [[10_500, 10_900, "blorp"]]


def test_words_are_capped():
    raw = [(0.0, 0.1, "x" * 80)] * 300
    out = align.words_from_raw(raw, 0, 5000)
    assert len(out) == align.MAX_WORDS_PER_WINDOW and len(out[0][2]) == 64


def _seg(*words):
    return SimpleNamespace(words=[SimpleNamespace(start=a, end=b, word=w) for a, b, w in words])


class _Model:
    def __init__(self, segs=None, boom=None):
        self.segs, self.boom, self.calls = segs or [_seg((0.5, 0.9, "blorp"))], boom, []

    def transcribe(self, path, **kw):
        self.calls.append(kw)
        if self.boom:
            raise self.boom
        return iter(self.segs), SimpleNamespace(duration=5.0)


def test_time_window_cuts_transcribes_and_cleans_up(monkeypatch, tmp_path):
    seen = {}

    def fake_run(argv, **kw):
        seen.update(argv=argv, kw=kw)
        Path(argv[-1]).write_bytes(b"wav")
        return ProcResult(0, "", "")
    monkeypatch.setattr(align, "run", fake_run)
    model = _Model()
    words = align.time_window(model, tmp_path / "a.m4a", tmp_path, 3, 2000, 6000,
                              deadline=time.monotonic() + 30)
    assert words == [[2500, 2900, "blorp"]]
    assert seen["kw"]["timeout"] == align.ALIGN_CUT_TIMEOUT_S
    assert model.calls[0]["word_timestamps"] is True and model.calls[0]["language"] is None
    assert model.calls[0]["vad_filter"] is False
    assert not (tmp_path / "w3.wav").exists()


def test_time_window_honours_its_deadline(monkeypatch, tmp_path):
    monkeypatch.setattr(align, "run", lambda argv, **kw: ProcResult(0, "", ""))
    with pytest.raises(TldrTimeoutError):
        align.time_window(_Model(), tmp_path / "a.m4a", tmp_path, 0, 0, 5000,
                          deadline=0, clock=lambda: 1)


# --- whisper_transcribe changes -------------------------------------------------

def _download_argv(monkeypatch, tmp_path, **kw):
    seen = {}

    def fake_run(argv, **_):
        seen["argv"] = argv
        (tmp_path / f"{VID}.m4a").write_bytes(b"a")
        return ProcResult(0, str(tmp_path / f"{VID}.m4a"), "")
    monkeypatch.setattr(wt, "run", fake_run)
    wt.download_audio(VID, tmp_path, **kw)
    return seen["argv"]


def test_download_audio_argv_unchanged_without_a_ceiling(monkeypatch, tmp_path):
    """Byte-identical to the argv before T-020 (copied from that version)."""
    argv = _download_argv(monkeypatch, tmp_path)
    assert argv == [
        "yt-dlp", "--ignore-config", "-f", "bestaudio",
        "--no-playlist", "--match-filter", "!is_live",
        "--max-filesize", str(wt.MAX_AUDIO_BYTES),
        "--print", "after_move:filepath", "--no-simulate",
        "-o", "%(id)s.%(ext)s",
        f"https://www.youtube.com/watch?v={VID}",
    ]


def test_download_audio_ceiling_goes_in_the_filter(monkeypatch, tmp_path):
    argv = _download_argv(monkeypatch, tmp_path, max_duration_s=7200)
    assert argv[argv.index("--match-filter") + 1] == "!is_live & duration <= 7200"


def test_load_model_local_only_never_downloads(monkeypatch):
    seen = {}

    class FakeModel:
        def __init__(self, size, **kw):
            seen.update(size=size, **kw)
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper",
                        SimpleNamespace(WhisperModel=FakeModel))
    wt._load_model("small", local_only=True)
    assert seen["local_files_only"] is True and seen["compute_type"] == "int8"
    seen.clear()
    wt._load_model("small")
    assert seen["size"] == "small" and "local_files_only" not in seen


def test_model_on_disk_asks_the_local_cache_only(monkeypatch):
    seen = {}

    def download_model(size, **kw):
        seen.update(kw)
        if size == "absent":
            raise FileNotFoundError(size)
    monkeypatch.setitem(__import__("sys").modules, "faster_whisper",
                        SimpleNamespace(download_model=download_model))
    assert wt.model_on_disk("small") is True and seen["local_files_only"] is True
    assert wt.model_on_disk("absent") is False


# --- the route ---------------------------------------------------------------------

@pytest.fixture
def srv():
    s = server._Server(("127.0.0.1", 0), token=TOKEN, allow_origin=None)
    threading.Thread(target=s.serve_forever, kwargs={"poll_interval": 0.05},
                     daemon=True).start()
    yield s.server_address[1]
    s.shutdown()
    s.server_close()


def _post(port, body, token=TOKEN):
    req = urllib.request.Request(f"http://127.0.0.1:{port}/align/stream",
                                 data=json.dumps(body).encode(), method="POST")
    req.add_header("Content-Type", "application/json")
    req.add_header("Origin", "chrome-extension://abcdefghijklmnop")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            text = r.read().decode()
            return r.status, [json.loads(x) for x in text.splitlines() if x.strip()]
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or "null")


def _body(**over):
    return {"url": URL, "whisper": "ask",
            "windows": [{"start_ms": 1000, "end_ms": 6000}], **over}


@pytest.fixture
def ready(monkeypatch, tmp_path):
    """A machine with everything installed, every external call stubbed."""
    _versions(monkeypatch)
    calls = {"download": 0, "cut": 0}
    monkeypatch.setattr(wt, "is_available", lambda: True)
    monkeypatch.setattr(wt, "model_on_disk", lambda size: True)
    monkeypatch.setattr(wt, "_load_model", lambda size, local_only=False: _Model())
    monkeypatch.setattr(server.config, "get", lambda key, default=None: None)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/" + name)

    def fake_download(vid, workdir, **kw):
        calls["download"] += 1
        calls["download_kw"] = kw
        p = Path(workdir) / f"{vid}.m4a"
        p.write_bytes(b"a")
        return p
    monkeypatch.setattr(wt, "download_audio", fake_download)

    def fake_run(argv, **kw):
        calls["cut"] += 1
        Path(argv[-1]).write_bytes(b"wav")
        return ProcResult(0, "", "")
    monkeypatch.setattr(align, "run", fake_run)
    return calls


def _kinds(events):
    return [e["type"] for e in events]


def test_align_needs_the_token(srv):
    assert _post(srv, _body(), token=None)[0] == 401


@pytest.mark.parametrize("body", [
    {"url": "nope", "windows": [{"start_ms": 0, "end_ms": 1}]},
    {"url": URL, "windows": []},
    {"url": URL, "windows": [{"start_ms": 0, "end_ms": 1}], "whisper": "maybe"},
])
def test_align_refuses_bad_bodies(srv, ready, body):
    assert _post(srv, body)[0] == 400


def test_whisper_off_is_refused_before_any_work(srv, ready):
    status, body = _post(srv, _body(whisper="off"))
    assert (status, body["reason"]) == (503, "whisper_off")
    assert ready["download"] == 0


def test_whisper_missing_includes_the_install_hint(srv, ready, monkeypatch):
    monkeypatch.setattr(wt, "is_available", lambda: False)
    status, body = _post(srv, _body())
    assert (status, body["reason"], body["install_hint"]) == (503, "whisper_missing", wt.INSTALL_HINT)


def test_unreadable_library_version_is_whisper_missing(srv, ready, monkeypatch):
    _versions(monkeypatch, fw=None)
    assert _post(srv, _body())[1]["reason"] == "whisper_missing"


def test_too_long_is_a_413_with_a_reason(srv, ready, monkeypatch):
    monkeypatch.setattr(server, "_max_transcribe_ms", lambda: 5000)
    status, body = _post(srv, _body())
    assert (status, body["reason"]) == (413, "too_long")


def test_missing_whisper_outranks_too_long(srv, ready, monkeypatch):
    monkeypatch.setattr(server, "_max_transcribe_ms", lambda: 5000)
    monkeypatch.setattr(wt, "is_available", lambda: False)
    assert _post(srv, _body())[0] == 503


def test_windows_are_timed_streamed_and_cached(srv, ready, capsys):
    status, events = _post(srv, _body())
    assert status == 200 and _kinds(events)[-1] == "result"
    win = next(e for e in events if e["type"] == "window")
    assert win["words"] == [{"s": 1500, "e": 1900, "w": "blorp"}] and win["cached"] is False
    assert events[-1] == {"type": "result", "timed": 1, "cached": 0, "failed": 0}
    assert ready["download_kw"]["max_duration_s"] == server._max_transcribe_ms() // 1000
    # second time: all cached, no download, no permit
    server._tx_permit.acquire()
    try:
        status, events = _post(srv, _body())
    finally:
        server._tx_permit.release()
    assert events[-1]["cached"] == 1 and ready["download"] == 1
    assert "blorp" not in capsys.readouterr().out


def test_duplicate_windows_are_timed_once(srv, ready):
    w = {"start_ms": 1000, "end_ms": 6000}
    _, events = _post(srv, _body(windows=[w, w]))
    assert sorted(e["i"] for e in events if e["type"] == "window") == [0, 1]
    assert ready["cut"] == 1


def test_all_cached_needs_no_ffmpeg(srv, ready, monkeypatch):
    _post(srv, _body())
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    server._tx_permit.acquire()                  # both held: the request needs neither
    server._align_lock.acquire()
    try:
        status, events = _post(srv, _body())
    finally:
        server._align_lock.release()
        server._tx_permit.release()
    assert status == 200 and events[-1]["type"] == "result"
    assert ready["download"] == 1


@pytest.mark.parametrize("setup, reason, status", [
    (lambda m: __import__("shutil") and m.setattr(__import__("shutil"), "which", lambda n: None),
     "ffmpeg_missing", 503),
    (lambda m: m.setattr(wt, "model_on_disk", lambda s: False), "model_missing", 503),
    (lambda m: m.setattr(server, "_align_backoff_until", server._align_clock() + 60), "rate_limited", 429),
])
def test_in_stream_refusals_download_nothing(srv, ready, monkeypatch, capsys, setup, reason, status):
    setup(monkeypatch)
    code, events = _post(srv, _body())
    assert code == 200
    assert (events[-1]["reason"], events[-1]["status"]) == (reason, status)
    assert ready["download"] == 0
    assert f"align: {reason} {VID}" in capsys.readouterr().out


def test_a_held_lock_is_busy_and_not_released(srv, ready):
    server._align_lock.acquire()
    try:
        _, events = _post(srv, _body())
        assert events[-1]["reason"] == "busy"
        assert server._align_lock.locked()
    finally:
        server._align_lock.release()


def test_a_running_transcription_is_busy_before_any_download(srv, ready):
    server._tx_jobs[("x", "small")] = {}
    try:
        _, events = _post(srv, _body())
    finally:
        server._tx_jobs.clear()
    assert events[-1]["reason"] == "busy" and ready["download"] == 0


def test_rate_limit_sets_the_cooldown(srv, ready, monkeypatch):
    def limited(*a, **k):
        raise TldrError("ERROR: HTTP Error 429: Too Many Requests")
    monkeypatch.setattr(wt, "download_audio", limited)
    _, events = _post(srv, _body())
    assert (events[-1]["reason"], events[-1]["error"]) == ("rate_limited", align.ALIGN_RATE_LIMITED_TEXT)
    assert server._align_backoff_until > server._align_clock()


@pytest.mark.parametrize("exc, reason, status", [
    (TldrTimeoutError("slow"), "timeout", 504),
    (TldrError("yt-dlp produced no audio for this video."), "download_failed", 502),
    (TldrError("something else"), "download_failed", 502),
])
def test_download_failures_map_to_reasons(srv, ready, monkeypatch, exc, reason, status):
    def boom(*a, **k):
        raise exc
    monkeypatch.setattr(wt, "download_audio", boom)
    _, events = _post(srv, _body())
    assert (events[-1]["reason"], events[-1]["status"]) == (reason, status)


def test_the_permit_wait_gives_up_fast_on_a_fake_clock(srv, ready, monkeypatch):
    clock = [0.0]

    class FakePermit:
        def acquire(self, timeout=None):
            clock[0] += 3.0
            return False

        def release(self):
            raise AssertionError("never acquired")
    monkeypatch.setattr(server, "_tx_permit", FakePermit())
    monkeypatch.setattr(server, "_align_clock", lambda: clock[0])
    started = time.monotonic()
    _, events = _post(srv, _body())
    assert events[-1]["reason"] == "busy"
    assert sum(1 for e in events if e["type"] == "progress" and "Waiting" in e["message"]) >= 3
    assert time.monotonic() - started < 2


def test_a_failed_window_is_counted_and_not_cached(srv, ready, monkeypatch):
    monkeypatch.setattr(wt, "_load_model", lambda s, local_only=False: _Model(boom=RuntimeError()))
    _, events = _post(srv, _body())
    assert events[-1] == {"type": "result", "timed": 0, "cached": 0, "failed": 1}
    assert {"type": "progress", "message": "One window couldn't be timed."} in events
    assert not list(align.ALIGN_DIR.glob("*.json"))


def test_model_load_import_error_is_whisper_missing(srv, ready, monkeypatch):
    def bad(size, local_only=False):
        raise ImportError("half installed")
    monkeypatch.setattr(wt, "_load_model", bad)
    _, events = _post(srv, _body())
    assert events[-1]["reason"] == "whisper_missing"


def test_the_workdir_is_removed(srv, ready, monkeypatch, tmp_path):
    made = []
    import tempfile
    real = tempfile.mkdtemp

    def track(**kw):
        made.append(real(**kw))
        return made[-1]
    monkeypatch.setattr(tempfile, "mkdtemp", track)
    _post(srv, _body())
    assert made and not Path(made[0]).exists()



# --- T-020 build review: the rest of the 6' register --------------------------------

def test_an_oversized_body_is_a_413_with_no_reason(srv, ready):
    huge = {"url": URL, "whisper": "ask", "pad": "x" * (server.MAX_BODY_BYTES + 10),
            "windows": [{"start_ms": 0, "end_ms": 1000}]}
    status, body = _post(srv, huge)
    assert status == 413 and "reason" not in (body or {})


def test_a_pinned_off_says_so(srv, ready, monkeypatch):
    monkeypatch.setattr(server.config, "get",
                        lambda key, default=None: "off" if key == "whisper" else None)
    status, body = _post(srv, _body())
    assert (status, body["reason"], body["error"]) == (
        503, "whisper_off", align.WHISPER_PINNED_OFF_TEXT)


def test_a_cut_timeout_fails_that_window_and_the_next_one_runs(srv, ready, monkeypatch):
    cuts = []

    def run(argv, **kw):
        cuts.append(argv)
        if len(cuts) == 1:
            raise TldrTimeoutError("cut timed out")
        Path(argv[-1]).write_bytes(b"wav")
        return ProcResult(0, "", "")
    monkeypatch.setattr(align, "run", run)
    _, events = _post(srv, _body(windows=[{"start_ms": 1000, "end_ms": 6000},
                                          {"start_ms": 7000, "end_ms": 9000}]))
    assert events[-1] == {"type": "result", "timed": 1, "cached": 0, "failed": 1}
    assert [e["i"] for e in events if e["type"] == "window"] == [1]


def test_no_window_starts_without_budget_for_one(srv, ready, monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(server, "_align_clock", lambda: clock[0])
    real_put = align.put_window

    def put(*a, **k):
        real_put(*a, **k)
        clock[0] += server.ALIGN_BUDGET_S - server.ALIGN_WINDOW_DEADLINE_S + 1
    monkeypatch.setattr(align, "put_window", put)
    _, events = _post(srv, _body(windows=[{"start_ms": 1000, "end_ms": 6000},
                                          {"start_ms": 7000, "end_ms": 9000}]))
    assert events[-1] == {"type": "result", "timed": 1, "cached": 0, "failed": 1}
    assert ready["cut"] == 1


def test_prune_runs_once_per_request(srv, ready, monkeypatch):
    calls = []
    monkeypatch.setattr(align, "prune", lambda: calls.append(1))
    _post(srv, _body(windows=[{"start_ms": 1000, "end_ms": 6000},
                              {"start_ms": 7000, "end_ms": 9000},
                              {"start_ms": 10000, "end_ms": 12000}]))
    assert calls == [1]


def test_the_over_ceiling_skip_names_both_causes(srv, ready, monkeypatch):
    # The server picks this text by the message whisper_transcribe.download_audio
    # raises when yt-dlp's filter skips the video; rewording that raise must
    # break this test.
    import inspect
    assert "produced no audio" in inspect.getsource(wt)    # download_audio's raise

    def skipped(*a, **k):
        raise TldrError("yt-dlp produced no audio for this video.")
    monkeypatch.setattr(wt, "download_audio", skipped)
    _, events = _post(srv, _body())
    assert events[-1]["error"] == align.AUDIO_SKIPPED_TEXT
    assert "length" in align.AUDIO_SKIPPED_TEXT and "Whisper limit" in align.AUDIO_SKIPPED_TEXT


class _Writer:
    """A response body that breaks on its Nth write, as a closed tab does."""

    def __init__(self, break_at):
        self.n, self.break_at, self.lines = 0, break_at, []

    def write(self, data):
        self.n += 1
        if self.n >= self.break_at:
            raise BrokenPipeError()
        self.lines.append(json.loads(data))

    def flush(self):
        pass


def _handler(writer):
    h = server._Handler.__new__(server._Handler)
    h.wfile = writer
    h.sent = []
    h.send_response = lambda code: h.sent.append(code)
    h.send_header = lambda *a: None
    h.end_headers = lambda: None
    h._cors_headers = lambda: None
    h._send_json = lambda status, payload: h.sent.append((status, payload))
    return h


def test_a_closed_client_after_a_failed_window_stops_there(ready, monkeypatch):
    cuts = []

    def run(argv, **kw):
        cuts.append(argv)
        raise TldrTimeoutError("cut timed out")
    monkeypatch.setattr(align, "run", run)
    wins = [{"start_ms": 1000, "end_ms": 6000}, {"start_ms": 7000, "end_ms": 9000}]
    # writes: "Fetching", "Got the audio", "One window couldn't be timed." (breaks)
    h = _handler(_Writer(break_at=3))
    h._run_align(_body(windows=wins))
    assert len(cuts) == 1 and not server._align_lock.locked()


def test_a_closed_client_stops_after_the_current_window(ready):
    wins = [{"start_ms": 1000, "end_ms": 6000}, {"start_ms": 7000, "end_ms": 9000}]
    # writes: "Fetching", "Got the audio", window 0 (breaks)
    w = _Writer(break_at=3)
    h = _handler(w)
    h._run_align(_body(windows=wins))
    assert [x.get("message") for x in w.lines] == [
        "Fetching the video's audio…", "Got the audio. Timing the words…"]
    assert align.get_window(VID, 1000, 6000, wt.resolve_model(), align.fingerprint())
    assert align.get_window(VID, 7000, 9000, wt.resolve_model(), align.fingerprint()) is None
    assert ready["cut"] == 1
    assert not server._align_lock.locked()
    assert server._tx_permit.acquire(blocking=False)
    server._tx_permit.release()


@pytest.mark.parametrize("reason, setup", [
    ("whisper_off", lambda m, b: b.update(whisper="off")),
    ("whisper_missing", lambda m, b: m.setattr(wt, "is_available", lambda: False)),
    ("too_long", lambda m, b: m.setattr(server, "_max_transcribe_ms", lambda: 5000)),
    ("ffmpeg_missing", lambda m, b: m.setattr(__import__("shutil"), "which", lambda n: None)),
    ("model_missing", lambda m, b: m.setattr(wt, "model_on_disk", lambda s: False)),
    ("busy", lambda m, b: server._tx_jobs.__setitem__(("x", "small"), {})),
    ("rate_limited", lambda m, b: m.setattr(server, "_align_backoff_until", server._align_clock() + 60)),
    ("timeout", lambda m, b: m.setattr(wt, "download_audio", lambda *a, **k: (_ for _ in ()).throw(
        TldrTimeoutError("slow")))),
    ("download_failed", lambda m, b: m.setattr(wt, "download_audio", lambda *a, **k: (_ for _ in ()).throw(
        TldrError("nope")))),
])
def test_every_reason_is_logged_and_no_word_ever_is(srv, ready, monkeypatch, capsys, reason, setup):
    body = _body()
    try:
        setup(monkeypatch, body)
        _post(srv, body)
    finally:
        server._tx_jobs.clear()
    out = capsys.readouterr().out
    assert f"align: {reason} {VID}" in out
    assert "blorp" not in out


def test_the_budget_covers_the_download_too(srv, ready, monkeypatch):
    """A slow download spends the same budget the windows do, so the server ends
    inside the extension's 660 s timer (build review, Backend B1)."""
    clock = [0.0]
    monkeypatch.setattr(server, "_align_clock", lambda: clock[0])
    real = wt.download_audio

    def slow(*a, **k):
        clock[0] += server.ALIGN_BUDGET_S - server.ALIGN_WINDOW_DEADLINE_S + 1
        return real(*a, **k)
    monkeypatch.setattr(wt, "download_audio", slow)
    _, events = _post(srv, _body())
    assert events[-1] == {"type": "result", "timed": 0, "cached": 0, "failed": 1}
    assert ready["cut"] == 0
    assert server.ALIGN_BUDGET_S + server.ALIGN_WINDOW_DEADLINE_S < 660


def test_an_unexpected_error_still_ends_with_a_reason(srv, ready, monkeypatch, capsys):
    import tempfile

    def broken(**kw):
        raise OSError("disk full")
    monkeypatch.setattr(tempfile, "mkdtemp", broken)
    _, events = _post(srv, _body())
    assert (events[-1]["reason"], events[-1]["status"], events[-1]["error"]) == (
        "other", 500, align.ALIGN_OTHER_TEXT)
    out = capsys.readouterr().out
    assert f"align: other {VID}" in out and "disk full" not in out
    assert not server._align_lock.locked()


def test_no_panel_text_carries_backticks():
    """The panel sets server detail as plain text, where backticks show literally."""
    import ast
    src = Path(align.__file__).read_text(encoding="utf-8")
    block = src[src.index("# T-020 align text: BEGIN"):src.index("# T-020 align text: END")]
    texts = [n.value.value for n in ast.parse(block).body
             if isinstance(n, ast.Assign) and isinstance(n.value, ast.Constant)]
    assert texts and not [t for t in texts if "`" in t]
