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
import hashlib
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
# video_id -> {"found": n, "pos_ms": how far into the video the model has read,
# "duration_ms": total}. Published by a running selection so a waiter can show real
# progress instead of a clock.
_seg_progress: dict = {}


# --- transcribing a captionless video -----------------------------------------
#
# This is the one job in the server measured in minutes rather than seconds, and it
# saturates the CPU while it runs. That makes it unlike everything else here:
#
# - It cannot live inside the request. _run_stream's `emit` has no OSError guard,
#   so a closed socket would throw away the whole run; and a finished transcript is
#   far too expensive to discard because someone shut a laptop lid.
# - It gets its own single permit rather than a slot from MAX_CONCURRENCY, which
#   was sized for 30-120s model calls. Two transcriptions holding both slots would
#   429 every other request -- including the one asking to stop them.
# - Two requests for the same video must produce one job, not two.
_tx_jobs: dict = {}             # video_id -> job dict, while a transcription runs
_tx_permit = threading.Semaphore(1)
_AUDIO_TMP_PREFIX = "youtube-tldw-audio-"


def sweep_stale_audio() -> int:
    """Remove audio temp directories left behind by an earlier run.

    The job cleans up after itself, but its thread is a daemon: Ctrl-C at the wrong
    moment kills it at interpreter exit without unwinding, stranding up to 300MB per
    interrupted run. Nothing else ever collects those, so `tldw serve` does it on
    the way up.
    """
    import shutil
    import tempfile
    removed = 0
    try:
        root = Path(tempfile.gettempdir())
        for stale in root.glob(_AUDIO_TMP_PREFIX + "*"):
            if stale.is_dir():
                shutil.rmtree(stale, ignore_errors=True)
                removed += 1
    except OSError:
        pass
    return removed

WHISPER_POLICIES = ("off", "ask", "always")
DEFAULT_WHISPER_POLICY = "ask"
# A ceiling on what may be transcribed at all, checked before any audio is fetched.
# At the measured 5.5x realtime, two hours of video is already ~22 minutes of
# full-core CPU; beyond that the honest answer is no.
MAX_TRANSCRIBE_MS = 2 * 3600 * 1000
# Wall-clock budget for one run: generous against the estimate, because being slow
# is normal and being infinite is not.
TRANSCRIBE_SLACK = 3.0
TRANSCRIBE_FLOOR_S = 300.0


def _wait_budget(job) -> float:
    """How long a request will narrate a job before giving up on it.

    Deliberately longer than the job's own budget: a job may be queued behind
    another before it starts, and giving up here says nothing about the job, which
    carries on and caches its result either way.
    """
    return (job.get("budget_s") or TRANSCRIBE_FLOOR_S) * 2 + 300.0


def _max_transcribe_ms() -> int:
    configured = config.get("whisper_max_minutes")
    if isinstance(configured, bool):      # int(True) == 1: a one-minute ceiling
        return MAX_TRANSCRIBE_MS
    try:
        minutes = int(configured)
    except (TypeError, ValueError):
        return MAX_TRANSCRIBE_MS
    return max(1, minutes) * 60 * 1000


def _whisper_policy(body):
    """How this request wants captionless videos handled, or False after a 400.

    Absent means "off". An extension that knows about this always sends the field,
    defaulting it client-side, so an absent one is a client that has no way to
    render the offer -- it would meet `needs_transcription` with "the summary
    stream ended unexpectedly" instead of the clear 422 it used to get. Silence is
    therefore the old behaviour, exactly.

    An unrecognized value is refused rather than quietly defaulted, the way _model
    does -- it decides whether we spend minutes of someone's CPU.

    A server-side `whisper` config key pins the answer regardless of what any
    client sends, so "off" can be a property of the machine and not a preference
    the browser happens to hold.
    """
    pinned = config.get("whisper")
    if isinstance(pinned, str) and pinned in WHISPER_POLICIES:
        return pinned
    value = body.get("whisper") if isinstance(body, dict) else None
    if value is None:
        return "off"
    if isinstance(value, str) and value in WHISPER_POLICIES:
        return value
    return None


def _tx_progress(video_id: str):
    """What a running transcription has published, or None if none is running."""
    with _cache_lock:
        for (vid, _model), job in _tx_jobs.items():
            if vid == video_id:
                return dict(job["progress"])
        return None


def _stop_transcription(video_id: str) -> bool:
    """Ask this video's transcription to stop. True if there was one.

    Keyed on the video rather than the model: whoever presses stop means "the one
    running for this video", not one of several it could have been.
    """
    stopped = False
    with _cache_lock:
        for (vid, _model), job in _tx_jobs.items():
            if vid == video_id:
                job["cancel"].set()
                stopped = True
    return stopped


def _start_transcription(meta, model: str):
    """Start (or join) the transcription of this video. Returns its job.

    The second caller for a video gets the first caller's job, so two tabs cost one
    transcription. The job owns its own temp directory and removes it however it
    ends, and writes to the transcript cache the moment it succeeds -- whether or
    not anyone is still listening.
    """
    vid = meta.video_id
    key = (vid, model)
    estimate = whisper_transcribe.estimate_seconds(meta.duration_ms, model)
    # A video of unknown length still needs a finite budget, and the floor would
    # kill it after five minutes of saturated CPU. Size it as the longest video we
    # would have accepted.
    budget_basis = estimate or whisper_transcribe.estimate_seconds(
        _max_transcribe_ms(), model)
    with _cache_lock:
        existing = _tx_jobs.get(key)
        # Joining a job that has already given up would hand this caller the
        # previous caller's failure -- a second click failing for the first
        # click's reason.
        if existing and not existing["cancel"].is_set() and not existing["error"]:
            return existing
        job = {
            "event": threading.Event(),
            "cancel": threading.Event(),
            "progress": {"message": "waiting for a free transcription slot...",
                         "percent": 0.0,
                         "estimate_s": estimate},
            "error": None,
            "budget_s": max(TRANSCRIBE_FLOOR_S, budget_basis * TRANSCRIBE_SLACK),
            "status": 500,          # overwritten from the exception's own type
            "cancelled": False,
            "cues": None,
            "meta": meta,
            "model": model,
            "started": time.monotonic(),
        }
        _tx_jobs[key] = job

    def _publish(message, percent=None):
        with _cache_lock:
            job["progress"]["message"] = message
            if percent is not None:
                job["progress"]["percent"] = percent

    def _run():
        import shutil
        import tempfile
        workdir = Path(tempfile.mkdtemp(prefix=_AUDIO_TMP_PREFIX))
        try:
            # One transcription at a time, machine-wide. A second video waits here
            # rather than fighting the first one for cores.
            # Wait for the permit *and* watch for a stop: acquiring blind means a
            # cancel is accepted and then ignored for as long as the job ahead
            # runs, which can be the better part of an hour.
            while not _tx_permit.acquire(timeout=0.5):
                if job["cancel"].is_set():
                    raise _Cancelled("Transcription cancelled.")
            try:
                if job["cancel"].is_set():
                    raise _Cancelled("Transcription cancelled.")
                budget = job["budget_s"]
                cues = whisper_transcribe.transcribe_video(
                    vid, workdir,
                    on_progress=_publish,
                    should_cancel=job["cancel"].is_set,
                    deadline=time.monotonic() + budget,
                    model_size=model,
                )
            finally:
                _tx_permit.release()
            # Cache before anything else: this cost minutes, and whether a client
            # is still connected has no bearing on whether it was worth keeping.
            _cache_put(vid, meta, cues, f"audio:{model}")
            job["cues"] = cues
            print(f"  transcribed {vid} from audio: {len(cues)} cues "
                  f"in {time.monotonic() - job['started']:.0f}s", flush=True)
        except _Cancelled as exc:
            job["error"], job["cancelled"], job["status"] = str(exc), True, 499
        except ImportError as exc:
            # A half-installed faster-whisper passes find_spec and fails here. That
            # is the likeliest real-world failure, so it says what to do about it.
            job["error"] = ("Whisper is installed but could not be loaded. Try "
                            f"reinstalling it: {whisper_transcribe.INSTALL_HINT}")
            job["status"] = 500
            print(f"  whisper import failed: {exc!r}", flush=True)
        except TldrError as exc:
            job["error"] = str(exc)
            job["status"] = next(
                (st for cls, st in _STATUS.items() if isinstance(exc, cls)), 500)
        except Exception as exc:
            job["error"] = "Transcribing this video failed unexpectedly."
            print(f"  transcription error: {exc!r}", flush=True)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
            job["event"].set()
            with _cache_lock:
                _tx_jobs.pop(key, None)

    try:
        threading.Thread(target=_run, daemon=True,
                         name=f"tldw-transcribe-{vid}").start()
    except Exception:
        with _cache_lock:
            _tx_jobs.pop(key, None)
        job["error"] = "Could not start transcribing this video."
        job["event"].set()
    return job


def _prune(cache: dict) -> None:
    """Drop expired entries, then the soonest-to-expire until under the cap."""
    now = time.monotonic()
    for k in [k for k, v in cache.items() if v[0] < now]:
        del cache[k]
    if len(cache) > _CACHE_MAX_ENTRIES:
        for k, _ in sorted(cache.items(), key=lambda kv: kv[1][0])[
                :len(cache) - _CACHE_MAX_ENTRIES]:
            del cache[k]


def _cache_put(video_id: str, meta, cues: list, source: str | None = None) -> None:
    # Resolved here rather than as a default: these helpers are defined above the
    # imports, so txcache doesn't exist yet when this signature is evaluated.
    source = source or txcache.CAPTIONS
    with _cache_lock:
        _transcript_cache[video_id] = (time.monotonic() + _CACHE_TTL,
                                       meta, cues, source)
        _prune(_transcript_cache)
    txcache.put(video_id, meta, cues, source)  # survives a restart; yt-dlp is rate-limited


def _cache_get(video_id: str, *, touch: bool = False):
    """Return (meta, cues) if a fresh entry exists, else None.

    touch=True renews the TTL — an active Q&A conversation keeps its own transcript
    alive rather than expiring out from under the next question.

    Deliberately still a 2-tuple: where a transcript came from matters to two
    callers and to none of the rest, so provenance rides in the entry and is read
    by _cache_source rather than widening what every call site has to unpack.
    """
    with _cache_lock:
        entry = _transcript_cache.get(video_id)
        if entry and entry[0] > time.monotonic():
            if touch:
                _transcript_cache[video_id] = (time.monotonic() + _CACHE_TTL,
                                               *entry[1:])
            return entry[1], entry[2]
    # Not in memory: a previous run may still have it, which saves two yt-dlp calls.
    on_disk = txcache.get(video_id)
    if on_disk:
        with _cache_lock:
            _transcript_cache[video_id] = (time.monotonic() + _CACHE_TTL, *on_disk)
        return on_disk[0], on_disk[1]
    return None


def _cache_source(video_id: str) -> str:
    """Where this video's cached transcript came from: "captions" or "audio:<model>".

    Anything we have no record of is captions — that is what every entry written
    before provenance existed holds.
    """
    with _cache_lock:
        entry = _transcript_cache.get(video_id)
        if entry and entry[0] > time.monotonic() and len(entry) > 3:
            return entry[3]
    on_disk = txcache.get(video_id)
    return on_disk[2] if on_disk else txcache.CAPTIONS


def _segment_ratio(body):
    """How aggressively the page wants key moments trimmed, if it said.

    Separate from `ratio`, which shapes the written summary — the two are different
    questions and the page sets them independently.
    """
    if not isinstance(body, dict):
        return None
    value = body.get("segment_ratio")
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if 0 < value <= 1 else None


def _seg_key(video_id: str, ratio):
    """Selections differ by how aggressively they trim, so the trim strength is part
    of the cache identity — otherwise asking for a tighter cut returns the looser
    selection that was prefetched earlier."""
    return (video_id, round(ratio, 3) if isinstance(ratio, (int, float)) else None)


def _seg_cache_put(video_id: str, meta, segments: list, ratio=None) -> None:
    with _cache_lock:
        _seg_cache[_seg_key(video_id, ratio)] = (
            time.monotonic() + _CACHE_TTL, meta, segments)
        _prune(_seg_cache)


def _seg_cache_get(video_id: str, ratio=None):
    """Return (meta, segments) if a fresh entry exists, else None."""
    with _cache_lock:
        entry = _seg_cache.get(_seg_key(video_id, ratio))
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


def _start_seg_prefetch(meta, cues: list, model: str | None = None, ratio=None) -> None:
    """Start background segment selection. No-op if one is already running for this video.
    Stores a threading.Event in _seg_prefetch_events so _run_segments can wait instead
    of spawning a second concurrent Claude call."""
    vid = meta.video_id
    key = _seg_key(vid, ratio)
    with _cache_lock:
        if key in _seg_prefetch_events:
            return  # already running — don't spawn a second one
        evt = threading.Event()
        _seg_prefetch_events[key] = evt

    def _run():
        from . import metadata as _md
        try:
            def note(seg):
                # Claude works through the transcript in order, so the end of the
                # newest clip is how far it has read.
                with _cache_lock:
                    p = _seg_progress.setdefault(
                        key, {"found": 0, "pos_ms": 0,
                              "duration_ms": meta.duration_ms})
                    p["found"] += 1
                    p["pos_ms"] = max(p["pos_ms"], int(seg.get("end", 0) * 1000))

            # A new thread: the request's model scope doesn't follow it here.
            with claude_client.use_model(model), usage.interaction("segments", vid):
                _meta, segs = core.select_segments(
                    _md.watch_url(vid), ratio, "en",
                    timeout=SEGMENTS_TIMEOUT,
                    _prefetched=(meta, cues),
                    # Both are needed to take the streaming path, which is the only
                    # one that can report progress while it runs.
                    on_progress=lambda *a, **k: None,
                    on_segment_ready=note,
                )
            _seg_cache_put(vid, _meta, segs, ratio)
            print(f"  prefetched {len(segs)} segments for {vid}", flush=True)
        except Exception as exc:
            print(f"  segment prefetch failed for {vid}: {exc}", flush=True)
        finally:
            with _cache_lock:
                _seg_prefetch_events.pop(key, None)
                _seg_progress.pop(key, None)
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
from . import transcript
from . import ask, audio, claude_client, config, core, textmode, txcache, usage
from . import whisper_transcribe
from . import summarize
from .summarize import SINGLE_PASS_CHARS
from .urls import canonical_video_id
from .timing import format_length, parse_duration

MAX_BODY_BYTES = 16 * 1024
MAX_SPEAK_BYTES = 64 * 1024   # /speak carries the summary text
MAX_ASK_BYTES = 64 * 1024     # /ask carries the question + conversation so far
# A summarize request may carry the whole caption track the page already had, which
# is the point: fetched by the browser, in its own session, so the server never
# asks YouTube for anything.
MAX_SUMMARIZE_BYTES = 8 * 1024 * 1024
ASK_TIMEOUT = 300.0           # one answer; the transcript is already in hand
PREVIEW_TEXT = "Hi — this is how your T L D W summaries will sound."
_preview_cache: dict[str, bytes] = {}   # voice model -> mp3 (13 voices, ~40KB each)
_preview_lock = threading.Lock()
class _Cancelled(TldrError):
    """A stop the user asked for, as distinct from something going wrong."""


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


_SEG_CRAWL_CAP = 30       # where the "no clips yet" crawl stops and real progress begins


def _seg_progress_line(pos: dict, label: str, elapsed: float):
    """(message, percent) for a selection in flight.

    Before the first clip there is nothing to measure, so it falls back to a slow
    crawl — but capped well below the real range, so arriving progress moves the bar
    forwards rather than backwards.
    """
    duration = pos.get("duration_ms") or 0
    if duration and pos.get("pos_ms"):
        read = min(1.0, pos["pos_ms"] / duration)
        # Starts exactly where the pre-clip crawl tops out, so the bar never
        # jumps backwards when real progress takes over.
        pct = round(min(_SEG_CRAWL_CAP + (95 - _SEG_CRAWL_CAP) * read, 95), 1)
        found = pos.get("found", 0)
        return (f"Analyzing {label}… {int(pct)}% ({found} clip"
                f"{'' if found == 1 else 's'} so far)", pct)
    return (f"Reading the transcript… ({int(elapsed)}s)",
            round(min(20 + elapsed * 0.15, _SEG_CRAWL_CAP), 1))


def _summary_key(video_id: str, ratio, source: str | None = None) -> str:
    """Identity of a summary: the video, and everything that shapes the answer.

    The prompt fingerprint matters most — editing a prompt used to be invisible to a
    cache, so it would keep serving text the current code would never produce. The
    model comes from the request's own scope.

    `source` is the transcript this summary was written from. Without it, swapping
    whisper_model to get a better transcript would hand back the old summary — the
    lookup happens before any transcript work, so the better transcript would never
    be consulted. That is the same bug the backend fingerprint exists to prevent, on
    a different axis. An unknown source is "captions", which is what every summary
    written before this was.
    """
    shape = "|".join((
        video_id,
        # The backend's own identity: which command, which flags, which model.
        claude_client.backend_fingerprint(),
        str(round(ratio, 3)) if isinstance(ratio, (int, float)) else "auto",
        summarize.PROMPT_FINGERPRINT,
        source or txcache.CAPTIONS,
    ))
    return f"{video_id}-{hashlib.sha256(shape.encode()).hexdigest()[:16]}"


def _body_limit(path: str) -> int:
    """How much body a route may carry. Summarize is the outlier: it can bring the
    caption track the page already fetched."""
    if path in ("/speak", "/speak/stream"):
        return MAX_SPEAK_BYTES
    if path in ("/summarize", "/summarize/stream"):
        return MAX_SUMMARIZE_BYTES
    if path == "/ask/stream":
        return MAX_ASK_BYTES
    return MAX_BODY_BYTES


def _page_transcript(body, url: str):
    """(meta, cues) built from what the page already had, or None.

    The extension runs on the watch page, where the title, channel, duration and
    caption track are all present and the browser can fetch captions in its own
    session. Taking them from there means the server makes no request to YouTube at
    all — which is the whole difference between "works" and "429" for a tool that
    isn't a browser.

    Everything here is untrusted input. So is yt-dlp's output, and it flows to the
    same places (a prompt, a sanitized filename), but this arrives over HTTP and is
    checked before it's believed.
    """
    page = body.get("page") if isinstance(body, dict) else None
    if not isinstance(page, dict):
        return None
    try:
        vid = canonical_video_id(url)
    except TldrError:
        return None
    raw_duration = page.get("duration_ms")
    duration_ms = int(raw_duration) if isinstance(raw_duration, (int, float)) \
        and not isinstance(raw_duration, bool) \
        and 0 < raw_duration < 24 * 3600 * 1000 else None
    rows = page.get("cues")
    try:
        if isinstance(rows, list):
            # What the page's own transcript panel rendered -- the only copy of the
            # captions YouTube still hands out (see the extension for why).
            cues = transcript.parse_panel_rows(rows, duration_ms)
        else:
            # An extension older than the panel change still sends a caption track.
            vtt = page.get("captions")
            if not isinstance(vtt, str) or not vtt.strip():
                return None
            fmt = page.get("captions_format")
            cues = transcript.parse_captions(vtt, fmt if isinstance(fmt, str) else None)
    except TldrError:
        return None                      # unparseable: fall back to fetching it
    if not cues:
        return None

    def text(value, limit):
        return value.strip()[:limit] if isinstance(value, str) and value.strip() else ""

    duration = duration_ms or cues[-1].end_ms
    meta = md.VideoMeta(vid, text(page.get("title"), 300) or "(untitled)",
                        text(page.get("channel"), 200) or "(unknown)",
                        duration, {}, {})
    return meta, cues


def _preferred_transcript(body, url: str):
    """The best transcript already in hand: real captions ahead of audio."""
    cached = _cached_transcript(url)
    vid = _vid_of(url)
    if cached and vid and _cache_source(vid).startswith("audio:"):
        from_page = _page_transcript(body, url)
        if from_page:
            return from_page
    return cached or _page_transcript(body, url)


def _audio_source(model: str) -> str:
    return f"audio:{model}"


def _effective_source(video_id: str) -> str:
    """The transcript source this request would actually use.

    A cached `audio:small` entry is not what a server now configured for `medium`
    would produce. Saying so here is what makes both the transcript lookup and the
    summary lookup miss, so changing whisper_model gets you the better transcript
    instead of a fortnight of the old one.
    """
    source = _cache_source(video_id)
    if not source.startswith("audio:"):
        return source
    wanted = _audio_source(whisper_transcribe.resolve_model())
    return source if source == wanted else wanted


def _cached_transcript(url: str):
    """(meta, cues) for this video if we already have them, else None.

    Summarizing was the one path that never looked: it wrote to the cache when it
    finished and read from it never, so every restart re-fetched from YouTube — the
    thing most likely to get the machine rate-limited.
    """
    vid = _vid_of(url)
    if not vid:
        return None
    source = _cache_source(vid)
    if source.startswith("audio:") and source != _effective_source(vid):
        return None        # made by a different Whisper model; make it again
    return _cache_get(vid, touch=True)


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
            self._send_json(200, {"ok": True, "name": "tldw", "version": __version__,
                                  "backend": claude_client.backend_info()})
        elif path == "/voices":
            self._send_json(200, {"voices": audio.voice_list()})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:
        path = self.path.split("?")[0]
        speak = path in ("/speak", "/speak/stream")
        if path == "/transcribe/stop":
            # Deliberately outside the concurrency semaphore. Transcriptions are the
            # longest-running thing here, and if stopping one needed a slot then the
            # request that could free the server would be the one getting a 429.
            # It does no work of its own: it sets a flag.
            if not self._authorized():
                self._send_json(401, {"error": "missing or invalid token"})
                return
            body = self._read_body()
            if body is None:
                return
            if not isinstance(body, dict):
                self._send_json(400, {"error": "expected a JSON object"})
                return
            try:
                vid = canonical_video_id(str(body.get("url") or body.get("video_id")))
            except TldrError:
                self._send_json(400, {"error": "bad video url"})
                return
            self._send_json(200, {"stopped": _stop_transcription(vid)})
            return
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
        body = self._read_body(_body_limit(path))
        if body is None:
            return  # _read_body already responded
        if not speak:
            parsed = self._validate(body)
            if parsed is None:
                return
        model = self._model(body)
        if model is False:
            return  # 400 already sent
        if not self.server.sem.acquire(blocking=False):
            self._send_json(429, {"error": "busy, try again shortly"})
            return
        try:
            with claude_client.use_model(model):
                if path == "/speak":
                    self._run_speak(body, stream=False)
                elif path == "/speak/stream":
                    self._run_speak(body, stream=True)
                elif path == "/summarize/stream":
                    self._run_stream(*parsed, body)
                elif path == "/segments/stream":
                    self._run_segments(*parsed, body)
                else:
                    self._run_buffered(*parsed)
        finally:
            self.server.sem.release()

    def _model(self, body):
        """The requested model, None for the CLI default, or False after a 400.

        Validated here rather than at use because it ends up in argv: an unknown
        name must be refused, not quietly swapped for the default.
        """
        model = body.get("model") if isinstance(body, dict) else None
        if model is None or model in claude_client.MODELS:
            return model
        self._send_json(400, {"error": "unknown model"})
        return False

    def _guarded(self, handler, *, max_bytes: int = MAX_BODY_BYTES) -> None:
        """auth -> bounded body -> concurrency slot, then run `handler(body)`."""
        if not self._authorized():
            self._send_json(401, {"error": "missing or invalid token"})
            return
        body = self._read_body(max_bytes)
        if body is None:
            return  # _read_body already responded
        model = self._model(body)
        if model is False:
            return
        if not self.server.sem.acquire(blocking=False):
            self._send_json(429, {"error": "busy, try again shortly"})
            return
        try:
            with claude_client.use_model(model):
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
                    max_chars=SINGLE_PASS_CHARS, on_progress=self._logger(start),
                    _prefetched=_cached_transcript(url))
                usage.note_video(summary.meta.video_id, summary.meta.duration_ms,
                                 textmode.summary_word_count(summary.result))
        except TldrError as exc:
            status = next((s for cls, s in _STATUS.items() if isinstance(exc, cls)), 500)
            print(f"  failed ({status}) in {time.monotonic()-start:.1f}s: {exc}", flush=True)
            self._send_json(status, {"error": str(exc)})
            return
        print(f"  summarized '{summary.meta.title}' in {time.monotonic()-start:.1f}s", flush=True)
        self._send_json(200, _to_payload(summary))

    def _run_stream(self, url, ratio, lang, body=None) -> None:
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

        # A summary we've already produced for this video, model, trim ratio and
        # prompt: serve it instead of paying for the model call again. The page's
        # own cache dies with the service worker or an extension reload, which is
        # exactly when this matters.
        vid = _vid_of(url)
        # A forced refresh re-runs the model but deliberately keeps the cached
        # transcript: the point is to compare summaries, and re-fetching the video
        # would risk a rate limit for nothing.
        refresh = isinstance(body, dict) and body.get("refresh") is True
        tx_source = None          # set when this request transcribed the audio
        policy = _whisper_policy(body)
        if policy is None:
            emit({"type": "error", "status": 400,
                  "error": "unknown whisper setting"})
            return
        if vid and not refresh:
            hit = txcache.get_summary(_summary_key(vid, ratio,
                                                   _effective_source(vid)))
            if hit:
                tlog(f"cached summary for {vid} (no model call)")
                emit({"type": "meta", "video_id": hit.get("video_id", vid),
                      "title": hit.get("title", ""), "channel": hit.get("channel", ""),
                      "source_url": hit.get("source_url", md.watch_url(vid)),
                      "original_length": hit.get("original_length", "")})
                emit({"type": "result", **hit, "cached": True,
                      "stats": usage.stats()})
                tx = _cache_get(vid, touch=True)
                if tx and not _seg_cache_get(vid, ratio):
                    # Still worth having key moments ready by the time they look.
                    _start_seg_prefetch(tx[0], tx[1], claude_client.current_model(),
                                        _segment_ratio(body))
                return

        page = body.get("page") if isinstance(body, dict) else None
        if isinstance(page, dict) and page.get("status"):
            tlog(f"page transcript: {str(page['status'])[:200]}")
        elif page is None:
            tlog("page transcript: not offered (old extension, or non-page request)")

        def partial(ev):
            if ev["kind"] == "meta":
                m = ev["meta"]
                # Enough to draw the real layout before the summary exists.
                emit({"type": "meta", "video_id": m.video_id, "title": m.title,
                      "channel": m.channel, "source_url": md.watch_url(m.video_id),
                      "original_length": format_length(m.duration_ms)})
            else:
                emit({"type": "partial", "kind": ev["kind"], "text": ev["text"]})

        def _summarize(prefetched):
            with usage.interaction("summarize", _vid_of(url)):
                result = core.summarize_url(
                    url, ratio, lang, timeout=REQUEST_TIMEOUT,
                    max_chars=SINGLE_PASS_CHARS, on_progress=progress,
                    on_partial=partial, _prefetched=prefetched)
                usage.note_video(result.meta.video_id, result.meta.duration_ms,
                                 textmode.summary_word_count(result.result))
                return result

        try:
            try:
                summary = _summarize(_cached_transcript(url)
                                     or _page_transcript(body, url))
            except NoTranscriptError as exc:
                # No caption track anywhere. The audio is still there, and it comes
                # from an endpoint YouTube does serve.
                meta = getattr(exc, "meta", None)
                if policy == "off" or meta is None:
                    raise
                produced = self._transcribe_fallback(meta, body, policy,
                                                     emit, progress)
                if produced is None:
                    return
                prefetched, tx_source = produced
                summary = _summarize(prefetched)
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
            # Without the source here, caching the summarize result would relabel a
            # transcript we just made from audio as a caption track.
            _cache_put(summary.meta.video_id, summary.meta, summary.cues,
                       tx_source or _cache_source(summary.meta.video_id))
            # Kick off segment selection in the background so "play key moments" is
            # ready by the time the user reads the summary.
            # The prefetch has to produce the selection the page will ask for: a
            # looser one cached under a different key would never be used.
            _start_seg_prefetch(summary.meta, summary.cues,
                                claude_client.current_model(),
                                _segment_ratio(body))
        source = tx_source or (_cache_source(summary.meta.video_id)
                               if summary.cues else txcache.CAPTIONS)
        payload = _to_payload(summary, source)
        if vid:
            txcache.put_summary(_summary_key(vid, ratio, source), payload)
        emit({"type": "result", **payload, "stats": usage.stats()})

    def _transcribe_fallback(self, meta, body, policy, emit, progress):
        """Offer, or carry out, transcription of a video that has no captions.

        Returns ((meta, cues), source) to summarize from, or None when the answer
        has already been sent -- an offer awaiting a click, a refusal, or a failure.
        """
        model = whisper_transcribe.resolve_model()
        estimate = whisper_transcribe.estimate_seconds(meta.duration_ms, model)
        installed = whisper_transcribe.is_available()
        accepted = isinstance(body, dict) and body.get("transcribe") is True

        # Checked against the duration yt-dlp reported, never one the request
        # supplied: a client claiming a three-hour video is sixty seconds long must
        # not be able to talk us into transcribing it.
        ceiling = _max_transcribe_ms()
        # yt-dlp reports 0 when it doesn't know. Quoting that as an estimate would
        # promise "under a minute" for a video of any length, and comparing it
        # against the ceiling would wave through the very videos we can't size.
        if not meta.duration_ms:
            # yt-dlp reports 0 when it doesn't know. We can neither quote a figure
            # nor let it slip the cap, so it is budgeted as though it were a
            # maximum-length video and offered without a number.
            estimate = 0
        elif meta.duration_ms > ceiling:
            emit({"type": "error", "status": 413, "error":
                  f"This video is {format_length(meta.duration_ms)} long and has no "
                  f"captions. Transcribing its audio is limited to "
                  f"{ceiling // 60000} minutes of video "
                  f"(`tldw config set whisper-max-minutes N` to change that)."})
            return None

        # The dependency is checked before anything is downloaded or any estimate is
        # agreed to -- it is the state most people will meet first.
        if not installed or (policy == "ask" and not accepted):
            emit({"type": "needs_transcription",
                  "video_id": meta.video_id, "title": meta.title,
                  "channel": meta.channel, "duration_ms": meta.duration_ms,
                  "estimate_s": estimate, "model": model, "installed": installed,
                  "install_hint": "" if installed else whisper_transcribe.INSTALL_HINT})
            return None

        job = _start_transcription(meta, model)
        model = job["model"]        # we may have joined someone else's run
        # Say so explicitly. The panel cannot infer this from its own request: on
        # "always" it never asked to transcribe, and after the panel is closed and
        # re-opened it has no memory of having done so. Both cases need a stop
        # button and a timeout measured in minutes.
        emit({"type": "transcribing", "video_id": meta.video_id,
              "estimate_s": job["progress"]["estimate_s"]})
        # Waiting is not work, and holding a general request slot for the length of
        # a transcription would 429 every captioned summarize, every question and
        # every stop -- which is the starvation that putting the job outside the
        # request was meant to avoid in the first place.
        self.server.sem.release()
        gone = False
        try:
            deadline = time.monotonic() + _wait_budget(job)
            while not job["event"].wait(0.5):
                if time.monotonic() > deadline:
                    emit({"type": "error", "status": 504, "error":
                          "Transcribing this video is taking longer than expected. "
                          "It may still finish; try again in a few minutes."})
                    return None
                if gone:
                    continue        # the job runs on; we just stop narrating it
                published = _tx_progress(meta.video_id)
                if not published:
                    continue
                try:
                    progress(published["message"],
                             round(10 + published["percent"] * 0.6, 1))
                except OSError:
                    # The client hung up. That is not a failure of the job, which
                    # keeps running and still caches what it produces -- and it is
                    # not something to spray a traceback over the server log for.
                    gone = True
        finally:
            # Re-taken before anything else happens: do_POST's finally releases it.
            self.server.sem.acquire()
        if gone:
            return None
        if job["error"]:
            emit({"type": "error", "status": job.get("status") or 500,
                  "error": job["error"]})
            return None
        if not job["cues"]:
            emit({"type": "error", "status": 500,
                  "error": "Transcription produced nothing usable."})
            return None
        return (meta, job["cues"]), f"audio:{model}"

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
            """Wait for a running selection, reporting how far through the video it
            has read rather than how long we've been waiting.

            Returns True if the event fired, False if SEGMENTS_TIMEOUT exceeded.
            """
            progress(f"Analyzing {cue_label}…", 20)
            t0 = time.monotonic()
            last_msg = None
            while not wait_evt.wait(timeout=2.0):
                elapsed = time.monotonic() - t0
                if elapsed >= SEGMENTS_TIMEOUT:
                    return False
                with _cache_lock:
                    pos = dict(_seg_progress.get(_seg_key(vid, ratio)) or {})
                msg, pct = _seg_progress_line(pos, cue_label, elapsed)
                if msg != last_msg:        # only when something actually changed
                    progress(msg, pct)
                    last_msg = msg
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
        if vid and max_ms is None:
            seg_hit = _seg_cache_get(vid, ratio)
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
        if vid and max_ms is None:
            with _cache_lock:
                prefetch_evt = _seg_prefetch_events.get(_seg_key(vid, ratio))
            if prefetch_evt:
                print(f"  waiting for prefetch for {vid}", flush=True)
                _wait_with_heartbeat(prefetch_evt, cue_label)
                seg_hit = _seg_cache_get(vid, ratio)
                if seg_hit:
                    _emit_segments(*seg_hit)
                    return
                # prefetch failed — fall through to direct call

        # 3. Direct call — emit segment_added for each span as Claude finds it so the
        #    client can start playback immediately without waiting for the full list.
        prefetched = tx_hit
        streamed_segs: list = []

        direct_ms = tx_hit[0].duration_ms if tx_hit else 0

        def _on_segment_ready(seg: dict) -> None:
            streamed_segs.append(seg)
            emit({"type": "segment_added", "segment": seg})
            msg, pct = _seg_progress_line(
                {"found": len(streamed_segs), "pos_ms": int(seg.get("end", 0) * 1000),
                 "duration_ms": direct_ms}, cue_label, 0)
            progress(msg, pct)

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
            _seg_cache_put(vid, meta, segments, ratio)
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
                try:
                    meta, cues = core.fetch_transcript(vid, lang,
                                                       on_progress=progress)
                except NoTranscriptError:
                    # Questions never start a transcription -- an answer must not
                    # quietly cost eight minutes of CPU. But "this video has no
                    # subtitles" is the wrong story for a video that was summarized
                    # from audio and has since aged out of the cache.
                    raise NoTranscriptError(
                        "This video has no subtitles, so there's no transcript to "
                        "answer from. If it was summarized from its audio, that "
                        "transcript has since expired -- summarize it again to "
                        "rebuild it, then ask."
                    ) from None
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


def _to_payload(summary: core.Summary, source: str | None = None) -> dict:
    meta, result = summary.meta, summary.result
    return {
        # Where the words came from. This rides in the payload, not just in the
        # transcript cache, because a summary served from the summary cache never
        # touches a transcript -- and that is exactly when the panel and the saved
        # .md still have to say the transcript was machine-transcribed from audio.
        "transcript_source": source or txcache.CAPTIONS,
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
    stale = sweep_stale_audio()
    httpd = _Server((host, port), token, allow_origin)
    print(f"tldw serve listening on http://{host}:{port}", flush=True)
    if stale:
        print(f"  (cleared {stale} leftover audio temp director"
              f"{'y' if stale == 1 else 'ies'} from an interrupted run)")
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
