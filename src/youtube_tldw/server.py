"""`tldw serve` — a tiny localhost HTTP API for the browser extension (text only).

Stdlib only. Security posture (see docs/reviews/PLAN-extension.md):
- loopback bind only; bearer token (hmac.compare_digest); CORS fails closed
  (chrome-extension origins or a pinned origin, never web/null/`*`);
- url goes through the youtube allowlist before any subprocess;
- bounded concurrency, per kind of work (claude calls / speech; busy -> 429);
  small body cap;
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
_seg_cache: dict = {}           # _seg_key(...) -> (expires_at, meta, segments)
# _seg_key(...) -> threading.Event while a selection for that key is being made --
# by the prefetch or by a live request -- so nothing starts a second one.
_seg_prefetch_events: dict = {}
# video_id -> (expires_at, session_id): the model-side conversation for this video.
# Resuming it means a follow-up question doesn't re-send the transcript.
_ask_sessions: dict = {}
# _seg_key(...) -> {"found": n, "pos_ms": how far into the video the model has read,
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
# - It gets its own single permit rather than a claude slot, which is sized for
#   30-120s model calls. Two transcriptions holding both slots would
#   429 every other request -- including the one asking to stop them.
# - Two requests for the same video must produce one job, not two.
_tx_jobs: dict = {}             # video_id -> job dict, while a transcription runs
_tx_permit = threading.Semaphore(1)
WHISPER_POLICIES = ("off", "ask", "always")
# Word timing (T-020, /align/stream). One alignment at a time, process-wide, so two
# tabs can't each start a YouTube download; it shares _tx_permit with whole-video
# transcription, waiting at most ALIGN_PERMIT_WAIT_S for it. After a YouTube 429 it
# does no fetch for ALIGN_COOLDOWN_S (in memory: a restart clears it).
# _align_backoff_until is read and written only while holding _align_lock.
_align_lock = threading.Lock()
_align_backoff_until = 0.0
_align_clock = time.monotonic       # a module attribute so tests can drive it
ALIGN_DOWNLOAD_TIMEOUT = 300.0
ALIGN_BUDGET_S = 600.0
ALIGN_WINDOW_DEADLINE_S = 30.0
ALIGN_PERMIT_WAIT_S = 20.0
ALIGN_COOLDOWN_S = 600.0
# A ceiling on what may be transcribed at all, checked before any audio is fetched.
# At the measured 5.5x realtime, two hours of video is already ~22 minutes of
# full-core CPU; beyond that the honest answer is no.
MAX_TRANSCRIBE_MS = 2 * 3600 * 1000
# Wall-clock budget for one run: generous against the estimate, because being slow
# is normal and being infinite is not.
TRANSCRIBE_SLACK = 3.0
TRANSCRIBE_FLOOR_S = 300.0


def _stale_audio_age() -> float:
    """How old an audio temp directory must be before it can't be in use.

    Generous on purpose: a directory's timestamp stops moving once its download
    finishes, while transcription of that audio can run for its whole budget. Twice
    the longest budget the configured ceiling allows, and never under six hours.
    """
    longest = whisper_transcribe.estimate_seconds(_max_transcribe_ms(),
                                                  whisper_transcribe.resolve_model())
    return max(6 * 3600.0, 2 * max(TRANSCRIBE_FLOOR_S, longest * TRANSCRIBE_SLACK))


def startup_hygiene() -> list:
    """The cleanup that's safe to run every time the server starts. Returns lines
    worth printing.

    Runs after the port is bound, and every rule is one that can't touch work in
    progress: only audio directories older than any transcription could still be
    using, and only voice files the allowlist can never select. Idle voices and
    expired cache entries are left to `tldw cache clean`, which says what it does.
    """
    notes = []
    swept = hygiene.sweep_stale_audio(_stale_audio_age())
    if swept.removed:
        notes.append(f"cleared {len(swept.removed)} leftover audio temp "
                     f"director{'y' if len(swept.removed) == 1 else 'ies'} "
                     f"({hygiene.human(swept.freed)})")
    unlisted = hygiene.Removal()
    allowed = [model for model, _label in audio.VOICES.values()]
    for f in hygiene.unlisted_voice_files(audio.VOICE_DIR, allowed):
        hygiene.remove_under(f, audio.VOICE_DIR, unlisted, dry_run=False)
    if unlisted.removed:
        notes.append(f"removed {len(unlisted.removed)} voice file(s) no longer "
                     f"offered ({hygiene.human(unlisted.freed)})")
    hygiene.ensure_voice_tracking(audio.VOICE_DIR)
    return notes


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
        workdir = Path(tempfile.mkdtemp(prefix=hygiene.AUDIO_TMP_PREFIX))
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


def _seg_key(video_id: str, ratio, source: str | None = None) -> tuple:
    """Everything that shapes a segment selection.

    The trim strength, so asking for a tighter cut doesn't return a looser one
    prefetched earlier. The selection prompt, listing and span constants, so a
    change to any of them misses rather than serving what today's code wouldn't
    produce. The backend -- command, flags *and model* -- so a selection made by
    Sonnet is never handed to an Opus request. And the transcript it was made from.

    backend_fingerprint() reads the calling thread's model scope, so this must be
    built in the request thread, under that request's model, and passed along --
    never rebuilt later on another thread, where it would quietly describe the
    default model and miss every time.
    """
    return (video_id,
            round(ratio, 3) if isinstance(ratio, (int, float)) else None,
            core.SEGMENT_FINGERPRINT,
            claude_client.backend_fingerprint(),
            source or txcache.CAPTIONS)


def _seg_file(key: tuple) -> str:
    return f"{key[0]}-{hashlib.sha256(repr(key).encode()).hexdigest()[:16]}"


def _seg_cache_put(key: tuple, meta, segments: list) -> None:
    with _cache_lock:
        _seg_cache[key] = (time.monotonic() + _CACHE_TTL, meta, segments)
        _prune(_seg_cache)
    txcache.put_segments(_seg_file(key), meta, segments)   # survives a restart


def _seg_cache_get(key: tuple):
    """(meta, segments) from memory, or from disk, else None."""
    with _cache_lock:
        entry = _seg_cache.get(key)
        if entry and entry[0] > time.monotonic():
            return entry[1], entry[2]
    on_disk = txcache.get_segments(_seg_file(key))         # outside the lock: I/O
    if on_disk:
        with _cache_lock:
            _seg_cache[key] = (time.monotonic() + _CACHE_TTL, *on_disk)
            _prune(_seg_cache)
    return on_disk


def _session_get(video_id: str):
    with _cache_lock:
        entry = _ask_sessions.get(video_id)
        return entry[1] if entry and entry[0] > time.monotonic() else None


def _session_put(video_id: str, session_id: str) -> None:
    with _cache_lock:
        _ask_sessions[video_id] = (time.monotonic() + _CACHE_TTL, session_id)
        _prune(_ask_sessions)


def _start_seg_prefetch(meta, cues: list, model: str | None = None, ratio=None,
                        source: str | None = None, *,
                        caller_holds_slot: bool = False) -> None:
    """Start background segment selection, unless it's already cached or running.

    Both checks live here, under one lock, rather than at each call site: a
    check-then-start split across callers is what let a selection be paid for
    twice. The key is built here, in the caller's thread, under the model this
    selection will run with -- see _seg_key. `source` is that of the cues being
    handed over, not whatever the transcript cache happens to say.

    It takes a claude slot like any other call, but never one of the last
    PREFETCH_RESERVE: background work must not be why a click gets "busy". When
    there's no room it is skipped, which costs nothing -- the key-moments request
    makes the same call live. `caller_holds_slot`: the caller is a request that has
    finished its own call and is about to give its slot back, so this is in effect
    taking that slot over, and the caller's isn't counted against the reserve.
    """
    vid = meta.video_id
    with claude_client.use_model(model):
        key = _seg_key(vid, ratio, source)
    if _seg_cache_get(key):
        return                  # already made: on disk from a previous run, or here
    slots = _claude_slots
    with _cache_lock:
        entry = _seg_cache.get(key)
        if key in _seg_prefetch_events or (entry and entry[0] > time.monotonic()):
            return              # running, or finished between the check and here
        # After the dedupe, before the event: a slot taken for a selection that is
        # already running would leak, and an event registered for one that never
        # starts would keep a key-moments request waiting for it.
        keep_free = PREFETCH_RESERVE - (1 if caller_holds_slot else 0)
        took = slots.try_take(keep_free=keep_free)
        if took:
            evt = threading.Event()
            _seg_prefetch_events[key] = evt
    if not took:
        print(f"  prefetch skipped for {vid}: {slots.status()} busy", flush=True)
        return

    def _finish():
        # The slot first, so whoever the event wakes finds it free -- but the event
        # whatever happens: a key left registered would keep every key-moments
        # request for this video waiting on a selection that will never come.
        try:
            slots.give_back()
        finally:
            with _cache_lock:
                _seg_prefetch_events.pop(key, None)
                _seg_progress.pop(key, None)
            evt.set()  # wake any waiter (even on failure, so it doesn't block forever)

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
            with claude_client.use_model(model), \
                    usage.interaction("segments", vid, trigger="prefetch"):
                _meta, segs = core.select_segments(
                    _md.watch_url(vid), ratio, "en",
                    timeout=SEGMENTS_TIMEOUT,
                    _prefetched=(meta, cues),
                    # Both are needed to take the streaming path, which is the only
                    # one that can report progress while it runs.
                    on_progress=lambda *a, **k: None,
                    on_segment_ready=note,
                )
            _seg_cache_put(key, _meta, segs)
            print(f"  prefetched {len(segs)} segments for {vid}", flush=True)
        except Exception as exc:
            print(f"  segment prefetch failed for {vid}: {exc}", flush=True)
        finally:
            _finish()

    try:
        threading.Thread(target=_run, daemon=True).start()
    except RuntimeError as exc:          # can't start a thread: undo, don't leak
        print(f"  segment prefetch failed for {vid}: {exc}", flush=True)
        _finish()

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
from . import align, hygiene, whisper_transcribe
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


# What each kind of work costs, not one number for all of it. A `claude` call is a
# ~200 MB process that spends its time waiting on the network; reading aloud is
# Piper, and Piper is the CPU. Transcription has its own single permit (above).
CLAUDE_LIMIT = 6          # summaries, key moments, questions -- and prefetch
SPEECH_LIMIT = 2          # reading aloud, and synthesizing a voice preview
PREFETCH_RESERVE = 2      # claude slots background work may never take
RETAKE_BUDGET = 30.0      # how long a request that let its slot go waits to get one back


class _Slots:
    """A counted pool of permits that can say how full it is.

    Not a Semaphore: prefetch has to ask "is there room, *and* would taking it
    still leave some for a person?", which a semaphore can't answer without reading
    its private counter. And a semaphore released one time too many says nothing
    and raises its limit for good -- here that raises, and is counted, because a
    raise inside a handler's `finally` is printed by socketserver and otherwise lost.
    """

    def __init__(self, name: str, limit: int, busy_message: str):
        self.name = name
        self.limit = limit
        self.busy_message = busy_message
        self.in_use = 0
        self.over_released = 0
        self._cond = threading.Condition()

    def try_take(self, *, keep_free: int = 0) -> bool:
        with self._cond:
            if self.in_use + 1 + keep_free > self.limit:
                return False
            self.in_use += 1
            return True

    def take(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        with self._cond:
            while self.in_use >= self.limit:
                left = deadline - time.monotonic()
                if left <= 0:
                    return False
                self._cond.wait(left)
            self.in_use += 1
            return True

    def give_back(self) -> None:
        with self._cond:
            if self.in_use <= 0:
                self.over_released += 1
                raise RuntimeError(f"a {self.name} slot was given back without being taken")
            self.in_use -= 1
            self._cond.notify()

    def status(self) -> str:
        return f"{self.name} {self.in_use}/{self.limit}"


class _Hold:
    """One request's slot. Closing it gives back only what is actually held, so a
    request that let its slot go while it waited can return from anywhere -- a
    cache hit, a timeout, a client that hung up -- without giving back twice.

    It keeps the pool it was taken from rather than re-reading the module's: a
    handler outliving its test must not touch the next test's pool.
    """

    def __init__(self, slots: _Slots):
        self.slots = slots
        self.held = True

    def release(self) -> None:
        if self.held:
            self.held = False
            self.slots.give_back()

    def retake(self, timeout: float) -> bool:
        if not self.held and self.slots.take(timeout):
            self.held = True
        return self.held

    close = release


def _make_pools() -> tuple[_Slots, _Slots]:
    return (
        _Slots("claude", CLAUDE_LIMIT,
               f"Already working on {CLAUDE_LIMIT} summaries, key moments or "
               "questions. Try again in a moment."),
        _Slots("speech", SPEECH_LIMIT,
               f"Already reading {SPEECH_LIMIT} summaries aloud. Try again when "
               "one finishes."),
    )


_claude_slots, _speech_slots = _make_pools()


def _limits_line() -> str:
    return (f"limits: {CLAUDE_LIMIT} claude calls (prefetch never takes the last "
            f"{PREFETCH_RESERVE}), {SPEECH_LIMIT} reading aloud, 1 transcription")


def _login_lines(env=None) -> list[str]:
    """Which credentials the environment hands `claude`. Names only, never values.

    Worded as what is in the environment, not as which login the CLI will use:
    there are ways to log in that two variables can't see.
    """
    env = os.environ if env is None else env
    token = bool(env.get("CLAUDE_CODE_OAUTH_TOKEN"))
    api_key = bool(env.get("ANTHROPIC_API_KEY"))
    lines = []
    if token:
        lines.append("claude login: CLAUDE_CODE_OAUTH_TOKEN is set in the environment")
    if api_key:
        lines.append("ANTHROPIC_API_KEY is set in the environment: calls may be "
                     "billed to the API rather than your Claude plan")
    if not token:
        lines.append("no CLAUDE_CODE_OAUTH_TOKEN in the environment: several "
                     "claude calls at once on a stored login can log you out "
                     "(anthropics/claude-code#76905); `claude setup-token` makes a "
                     "token that avoids it")
    return lines


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


# video_id -> (expires_at, NoTranscriptError with its .meta): what a captionless
# video looked like the last time YouTube was asked. Short-lived on purpose --
# long enough to cover an offer and its acceptance, short enough that a video
# which gains captions is noticed. Only an answer is kept, never a failure.
_captionless_meta: dict = {}
_CAPTIONLESS_TTL = 30 * 60


def _captionless_put(video_id: str, exc) -> None:
    with _cache_lock:
        _captionless_meta[video_id] = (time.monotonic() + _CAPTIONLESS_TTL, exc)
        _prune(_captionless_meta)


def _captionless_get(video_id: str):
    """A fresh copy of the remembered error (meta and message intact), or None."""
    with _cache_lock:
        entry = _captionless_meta.get(video_id)
        if not entry or entry[0] <= time.monotonic():
            return None
        original = entry[1]
    again = NoTranscriptError(str(original))
    again.meta = original.meta
    return again


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
            # No slot for a preview that's already made; _run_preview takes a
            # speech slot itself, around the synthesis.
            self._guarded(self._run_preview, slots=None)
            return
        if path == "/summarize/cached":
            self._guarded(self._run_cached, slots=None)      # one disk read
            return
        if path == "/ask/stream":
            self._guarded(self._run_ask, slots=_claude_slots, max_bytes=MAX_ASK_BYTES)
            return
        if path == "/align/stream":
            # No claude slot: it takes the transcription permit itself, around the
            # Whisper work only (T-020).
            self._guarded(self._run_align, slots=None)
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
        hold = self._take(_speech_slots if speak else _claude_slots)
        if hold is None:
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
            hold.close()

    def _take(self, slots: _Slots) -> "_Hold | None":
        """A slot from `slots` for this request, or None after a 429 saying which
        limit it hit. The hold is kept on the handler so a wait deep inside a route
        can let it go and get it back."""
        if not slots.try_take():
            self._refuse_busy(slots)
            return None
        self._hold = _Hold(slots)
        return self._hold

    def _refuse_busy(self, slots: _Slots) -> None:
        print(f"  busy: {slots.status()}", flush=True)
        self._send_json(429, {"error": slots.busy_message})

    def _retake(self, progress, emit) -> bool:
        """Get this request's slot back after waiting on something else.

        Says so while it waits rather than going quiet, and gives up after
        RETAKE_BUDGET with a busy error, never a stall with no end. A client that
        has hung up is simply let go: there is nobody left to tell.
        """
        hold = self._hold
        if hold.retake(timeout=0):
            return True                 # the usual case: one is free right now
        deadline = time.monotonic() + RETAKE_BUDGET
        try:
            progress(f"Waiting for a free slot ({hold.slots.status()})…")
            while True:
                left = deadline - time.monotonic()
                if hold.retake(timeout=max(0.0, min(2.0, left))):
                    return True
                if time.monotonic() >= deadline:
                    print(f"  busy: {hold.slots.status()}", flush=True)
                    emit({"type": "error", "status": 429,
                          "error": hold.slots.busy_message})
                    return False
        except OSError:
            return False

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

    def _guarded(self, handler, *, slots: "_Slots | None",
                 max_bytes: int = MAX_BODY_BYTES) -> None:
        """auth -> bounded body -> a slot from `slots`, then run `handler(body)`.
        None takes no pool slot: either the work costs nothing, or (/align/stream)
        it brings its own lock and the transcription permit."""
        if not self._authorized():
            self._send_json(401, {"error": "missing or invalid token"})
            return
        body = self._read_body(max_bytes)
        if body is None:
            return  # _read_body already responded
        model = self._model(body)
        if model is False:
            return
        if slots is None:
            self._hold = None
            with claude_client.use_model(model):
                handler(body)
            return
        hold = self._take(slots)
        if hold is None:
            return
        try:
            with claude_client.use_model(model):
                handler(body)
        finally:
            hold.close()

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
                if tx:
                    # Still worth having key moments ready by the time they look.
                    # It checks its own cache, under the *segment* ratio -- this
                    # used to check under the summary ratio, which the extension
                    # never sends, and so re-ran the selection on every re-open.
                    _start_seg_prefetch(tx[0], tx[1], claude_client.current_model(),
                                        _segment_ratio(body), _cache_source(vid),
                                        caller_holds_slot=True)
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
                prefetched = _cached_transcript(url) or _page_transcript(body, url)
                known = _captionless_get(vid) if vid and prefetched is None else None
                remembered = known is not None
                if remembered:
                    # Asked YouTube about this very video minutes ago -- typically
                    # the offer whose acceptance this request is. Fetching its
                    # metadata again is ~4s and a request to a rate-limited host
                    # for an answer we already have.
                    raise known
                summary = _summarize(prefetched)
            except NoTranscriptError as exc:
                # No caption track anywhere. The audio is still there, and it comes
                # from an endpoint YouTube does serve.
                meta = getattr(exc, "meta", None)
                # Only a fresh answer starts the clock. Re-storing a remembered one
                # would keep renewing it, and a video reopened every few minutes
                # would never be checked for captions again.
                if meta is not None and vid and not remembered:
                    _captionless_put(vid, exc)
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
        source = tx_source or (_cache_source(summary.meta.video_id)
                               if summary.cues else txcache.CAPTIONS)
        if summary.cues:
            # Without the source here, caching the summarize result would relabel a
            # transcript we just made from audio as a caption track.
            _cache_put(summary.meta.video_id, summary.meta, summary.cues, source)
            # Kick off segment selection in the background so "play key moments" is
            # ready by the time the user reads the summary.
            # The prefetch has to produce the selection the page will ask for: a
            # looser one cached under a different key would never be used.
            _start_seg_prefetch(summary.meta, summary.cues,
                                claude_client.current_model(),
                                _segment_ratio(body), source,
                                caller_holds_slot=True)
        payload = _to_payload(summary, source)
        if vid:
            txcache.put_summary(_summary_key(vid, ratio, source), payload)
        emit({"type": "result", **payload, "stats": usage.stats()})

    def _run_cached(self, body) -> None:
        """A summary we've already written for this video, or {"type": "miss"}.

        The panel asks this *before* reading the page's transcript, which costs
        0.5-13s and a request to YouTube -- and which it used to do even for a
        summary already sitting in the cache. Read-only: on a miss it fetches
        nothing and starts nothing, so the panel's normal flow runs unchanged.

        Same key as the real request (_summary_key with _effective_source), and run
        under the request's model by _guarded: the key includes the backend
        fingerprint, so outside that scope every lookup would quietly miss.
        A separate endpoint rather than a flag on /summarize/stream, so an older
        server answers 404 -- which the panel treats as a miss -- instead of
        running a summarize without the page's transcript.
        """
        if not isinstance(body, dict):
            self._send_json(400, {"error": "expected a JSON object"})
            return
        # The same validation the real request runs, so a ratio means the same
        # thing to both -- otherwise "0.5" could be served the "auto" summary.
        parsed = self._validate(body)
        if parsed is None:
            return                                   # 400 already sent
        url, ratio, _lang = parsed
        try:
            vid = canonical_video_id(str(url))
        except TldrError:
            self._send_json(400, {"error": "bad video url"})
            return
        hit = txcache.get_summary(_summary_key(vid, ratio, _effective_source(vid)))
        if not hit:
            self._send_json(200, {"type": "miss"})
            return
        # What today's cached-summary path does: have key moments ready.
        tx = _cache_get(vid, touch=True)
        if tx:
            _start_seg_prefetch(tx[0], tx[1], claude_client.current_model(),
                                _segment_ratio(body), _cache_source(vid))
        self._send_json(200, {"type": "result", **hit, "cached": True,
                              "stats": usage.stats()})

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
        self._hold.release()
        gone = False
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
        # A summary is still to be made, and that is work: it needs its slot back.
        # Not before now -- a client that hung up, or a job that failed, has
        # nothing left to do with one.
        if not self._retake(progress, emit):
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
                    pos = dict(_seg_progress.get(key) or {})
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

        # The transcript this selection would be made from, and so its key -- built
        # here, in the request thread, under this request's model (see _seg_key).
        # A request with max_length gets no key at all: a capped selection must
        # neither be served from, nor written over, the uncapped one.
        tx_hit = _cache_get(vid) if vid else None
        source = _cache_source(vid) if tx_hit else txcache.CAPTIONS
        key = _seg_key(vid, ratio, source) if vid and max_ms is None else None
        cue_label = f"{len(tx_hit[1])} cues" if tx_hit else "transcript"

        # 1. Instant cache hit (memory, or disk from an earlier run)
        if key:
            seg_hit = _seg_cache_get(key)
            if seg_hit:
                meta, segments = seg_hit
                print(f"  cached segments for {vid} ({len(segments)} moments, "
                      f"{time.monotonic()-start:.1f}s)", flush=True)
                usage.note_served(vid, "cache")
                _emit_segments(meta, segments)
                return

        # 2. Wait for in-progress prefetch (heartbeat while blocked). Same key, so a
        #    prefetch running under another model is not waited on.
        if key:
            with _cache_lock:
                prefetch_evt = _seg_prefetch_events.get(key)
            if prefetch_evt:
                print(f"  waiting for prefetch for {vid}", flush=True)
                # Waiting is not work: the selection being waited on holds its own
                # slot, and holding a second one for the same call would halve how
                # many videos can have key moments made at once.
                self._hold.release()
                _wait_with_heartbeat(prefetch_evt, cue_label)
                seg_hit = _seg_cache_get(key)
                if seg_hit:
                    usage.note_served(vid, "prefetch")
                    _emit_segments(*seg_hit)
                    return
                # It failed or ran past its budget. Falling through to a live call,
                # which is work again, and needs the slot back.
                if not self._retake(progress, emit):
                    return

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

        # Register as in flight, so a prefetch that starts while this runs -- a
        # summary finishing first -- waits for this selection instead of paying for
        # a second one.
        live_evt = None
        if key:
            with _cache_lock:
                if key not in _seg_prefetch_events:
                    live_evt = threading.Event()
                    _seg_prefetch_events[key] = live_evt
        try:
            with usage.interaction("segments", vid or "", trigger="request"):
                meta, segments = core.select_segments(
                    url, ratio, lang, max_length_ms=max_ms,
                    timeout=SEGMENTS_TIMEOUT, on_progress=progress,
                    on_segment_ready=_on_segment_ready,
                    _prefetched=prefetched)
            # Cached before the event is released, so anything woken by it finds
            # the selection instead of starting another.
            if key:
                _seg_cache_put(key, meta, segments)
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
        finally:
            if live_evt is not None:
                with _cache_lock:
                    if _seg_prefetch_events.get(key) is live_evt:
                        _seg_prefetch_events.pop(key, None)
                live_evt.set()
        if vid:
            usage.note_served(vid, "live")
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

    def _run_align(self, body) -> None:
        """POST /align/stream (T-020): word timing for "Cover listed words".

        Checks that can fail before any work answer as plain JSON; everything after
        the 200 is an in-stream `error`. Every refusal after the body is validated
        carries `reason` and logs `align: <reason> <video_id>` -- never words or
        windows. The order is
        contracted (docs/contracts.md): cached windows are served before any
        dependency is required, and nothing is downloaded until the model, the
        lock, the cooldown and any running transcription all allow it.

        Long synchronous work in a request is accepted here: it streams, follows
        /summarize/stream's precedent, and stops when the client goes.
        """
        global _align_backoff_until
        vid = "-"

        def refuse(status, reason, error, **extra):
            print(f"  align: {reason} {vid}", flush=True)
            self._send_json(status, {"error": error, "reason": reason, **extra})

        if not isinstance(body, dict):
            self._send_json(400, {"error": "expected a JSON object"})
            return
        try:
            vid = canonical_video_id(str(body.get("url")))
        except TldrError:
            self._send_json(400, {"error": "bad video url"})
            return
        try:
            windows = align.parse_windows(body.get("windows"))
        except align.WindowError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        policy = _whisper_policy(body)
        if policy is None:
            self._send_json(400, {"error": "whisper must be one of: off, ask, always"})
            return
        if policy == "off":
            pinned = config.get("whisper") == "off"
            refuse(503, "whisper_off",
                   align.WHISPER_PINNED_OFF_TEXT if pinned else align.WHISPER_OFF_TEXT)
            return
        fp = align.fingerprint() if whisper_transcribe.is_available() else None
        if fp is None:
            refuse(503, "whisper_missing", align.WHISPER_MISSING_TEXT,
                   install_hint=whisper_transcribe.INSTALL_HINT)
            return
        ceiling = _max_transcribe_ms()
        if any(end > ceiling for _start, end in windows):
            refuse(413, "too_long", align.TOO_LONG_TEXT)
            return
        model_size = whisper_transcribe.resolve_model()

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self._cors_headers()
        self.end_headers()
        gone = [False]

        def emit(obj):
            # Only the write is guarded: a model-load OSError must not read as
            # "the client went away".
            if gone[0]:
                return
            try:
                self.wfile.write((json.dumps(obj) + "\n").encode("utf-8"))
                self.wfile.flush()
            except OSError:
                gone[0] = True

        def fail(status, reason, error, **extra):
            print(f"  align: {reason} {vid}", flush=True)
            emit({"type": "error", "status": status, "reason": reason,
                  "error": error, **extra})

        # Exact duplicates are timed once; each original index still gets its event.
        indices: dict = {}
        for i, key in enumerate(windows):
            indices.setdefault(key, []).append(i)
        counts = {"timed": 0, "cached": 0, "failed": 0}

        def send_window(key, words, cached):
            for i in indices[key]:
                emit({"type": "window", "i": i, "start_ms": key[0], "end_ms": key[1],
                      "words": [{"s": s, "e": e, "w": w} for s, e, w in words],
                      "cached": cached})

        missing = []
        for key in indices:
            words = align.get_window(vid, key[0], key[1], model_size, fp)
            if words is None:
                missing.append(key)
                continue
            send_window(key, words, True)
            counts["timed"] += len(indices[key])
            counts["cached"] += len(indices[key])
        if not missing:
            emit({"type": "result", **counts})
            return

        import shutil
        import tempfile
        if shutil.which("ffmpeg") is None:
            fail(503, "ffmpeg_missing", align.FFMPEG_MISSING_TEXT)
            return
        try:
            on_disk = whisper_transcribe.model_on_disk(model_size)
        except ImportError:
            fail(503, "whisper_missing", align.WHISPER_BROKEN_TEXT.format(
                hint=whisper_transcribe.INSTALL_HINT),
                 install_hint=whisper_transcribe.INSTALL_HINT)
            return
        if not on_disk:
            fail(503, "model_missing", align.MODEL_MISSING_TEXT)
            return
        if not _align_lock.acquire(blocking=False):
            fail(429, "busy", align.ALIGN_BUSY_TEXT)
            return
        have_permit = False
        workdir = None
        # The budget covers the whole request, download and permit wait included,
        # so the server always finishes inside the extension's 660 s timer.
        started = _align_clock()
        try:
            with _cache_lock:
                transcribing = bool(_tx_jobs)
            if transcribing:
                fail(429, "busy", align.TX_BUSY_TEXT)
                return
            if _align_clock() < _align_backoff_until:
                fail(429, "rate_limited", align.ALIGN_RATE_LIMITED_TEXT)
                return
            workdir = Path(tempfile.mkdtemp(prefix=hygiene.AUDIO_TMP_PREFIX))
            emit({"type": "progress", "message": "Fetching the video's audio…"})
            if gone[0]:
                return
            try:
                audio_path = whisper_transcribe.download_audio(
                    vid, workdir, timeout=ALIGN_DOWNLOAD_TIMEOUT,
                    max_duration_s=ceiling // 1000)
            except TldrTimeoutError:
                fail(504, "timeout", align.DOWNLOAD_TIMEOUT_TEXT)
                return
            except TldrError as exc:
                # Checked on yt-dlp's own text, before anything rewrites it.
                if md.rate_limited(str(exc)):
                    _align_backoff_until = _align_clock() + ALIGN_COOLDOWN_S
                    fail(429, "rate_limited", align.ALIGN_RATE_LIMITED_TEXT)
                elif "produced no audio" in str(exc):
                    # The match filter skipped it: no duration, or over the ceiling.
                    fail(502, "download_failed", align.AUDIO_SKIPPED_TEXT)
                else:
                    fail(502, "download_failed", align.DOWNLOAD_FAILED_TEXT)
                return
            # A progress line after every long silent step, so a client that gave
            # up is noticed at the next write instead of after the whole budget.
            emit({"type": "progress", "message": "Got the audio. Timing the words…"})
            if gone[0]:
                return
            waited_from = last_note = _align_clock()
            while not _tx_permit.acquire(timeout=0.5):
                now = _align_clock()
                if now - waited_from >= ALIGN_PERMIT_WAIT_S:
                    fail(429, "busy", align.TX_BUSY_TEXT)
                    return
                if now - last_note >= 5.0:
                    last_note = now
                    emit({"type": "progress",
                          "message": "Waiting for Whisper: a transcription is running…"})
                    if gone[0]:
                        return
            have_permit = True
            try:
                model = whisper_transcribe._load_model(model_size, local_only=True)
            except ImportError:
                fail(503, "whisper_missing", align.WHISPER_BROKEN_TEXT.format(
                    hint=whisper_transcribe.INSTALL_HINT),
                     install_hint=whisper_transcribe.INSTALL_HINT)
                return
            except Exception as exc:
                print(f"  align: model load failed {vid}: {type(exc).__name__}", flush=True)
                fail(503, "model_missing", align.MODEL_MISSING_TEXT)
                return
            for n, key in enumerate(missing):
                if gone[0]:
                    break
                if ALIGN_BUDGET_S - (_align_clock() - started) < ALIGN_WINDOW_DEADLINE_S:
                    # Not enough budget left for one more window's worst case.
                    left = sum(len(indices[k]) for k in missing[n:])
                    counts["failed"] += left
                    print(f"  align: budget spent {vid}: {left} windows left", flush=True)
                    break
                try:
                    words = align.time_window(
                        model, audio_path, workdir, n, key[0], key[1],
                        deadline=_align_clock() + ALIGN_WINDOW_DEADLINE_S,
                        clock=_align_clock)
                except Exception as exc:
                    counts["failed"] += len(indices[key])
                    print(f"  align: window {n} failed {vid}: {type(exc).__name__}",
                          flush=True)
                    emit({"type": "progress", "message": "One window couldn't be timed."})
                    continue
                align.put_window(vid, key[0], key[1], model_size, fp, words)
                send_window(key, words, False)
                counts["timed"] += len(indices[key])
            emit({"type": "result", **counts})
        except Exception as exc:
            # Anything unforeseen still ends the stream with a reason; its detail
            # stays in the log.
            print(f"  align: unexpected {vid}: {type(exc).__name__}", flush=True)
            fail(500, "other", align.ALIGN_OTHER_TEXT)
        finally:
            if have_permit:
                _tx_permit.release()
            _align_lock.release()
            if workdir is not None:
                shutil.rmtree(workdir, ignore_errors=True)
            align.prune()

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
        # Making one is Piper (and, the first time, a voice download), so it counts
        # against reading aloud. The whole join: the download happens inside it.
        slots = _speech_slots
        if not slots.try_take():
            self._refuse_busy(slots)
            return
        start = time.monotonic()
        try:
            data = b"".join(audio.stream_speech(PREVIEW_TEXT, voice,
                                                timeout=SPEAK_TIMEOUT,
                                                record_use=False))
        except (TldrTimeoutError, TldrError) as exc:
            status = 504 if isinstance(exc, TldrTimeoutError) else 502
            print(f"  preview failed ({status}): {exc}", flush=True)
            self._send_json(status, {"error": str(exc)}); return
        except Exception as exc:
            print(f"  unexpected preview error: {exc!r}", flush=True)
            self._send_json(500, {"error": "internal error"}); return
        finally:
            slots.give_back()
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
    print(f"  {_limits_line()}", flush=True)
    # Contained like the cleanup below: a malformed custom model command is a
    # reason for its requests to fail, not for the server not to start.
    try:
        if claude_client.is_claude_cli():
            for line in _login_lines():
                print(f"  {line}", flush=True)
    except Exception as exc:
        print(f"  (couldn't check the model command: {exc})", flush=True)
    # After the bind, so a second server that can't start never cleans up at all.
    # And contained: tidying up is never a reason not to serve.
    try:
        for note in startup_hygiene():
            print(f"  ({note})")
    except Exception as exc:
        print(f"  (skipped startup cleanup: {exc!r})")
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
