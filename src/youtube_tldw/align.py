"""Word timing for "Cover listed words" (T-020): Whisper over short windows.

The extension finds the caption lines that hold a listed word and sends only the
time windows around them -- never the word list. For each window this module cuts
the audio with ffmpeg, runs faster-whisper with word timestamps, and returns every
word it heard with absolute media times. Matching against the list happens back in
the browser, so the list never shapes anything cached here.

Store 13, the alignment cache: one JSON file per window under ALIGN_DIR, keyed by
everything that shapes the result (the video, the window, the model, `lang=auto`,
and a fingerprint of the settings and library versions). See docs/data-model.md.
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import time
from pathlib import Path

from . import TldrTimeoutError
from . import txcache
from . import whisper_transcribe
from .proc import run
from .timing import to_ffmpeg_ts

ALIGN_DIR = Path.home() / ".cache" / "youtube-tldw" / "align"

MAX_ALIGN_WINDOWS = 200
MAX_ALIGN_WINDOW_MS = 12_000
MAX_WORDS_PER_WINDOW = 200
MAX_WORD_CHARS = 64
ALIGN_MAX_ENTRIES = 5000
ALIGN_CUT_TIMEOUT_S = 15
# A word whose timestamp lands this far outside its window is a bad timestamp,
# and is dropped rather than clamped (clamping would hide it).
BAD_TIMESTAMP_SLACK_MS = 1000
SAMPLE_RATE = 16_000
CHANNELS = 1
# The payload shape's own version: bumping it makes every old entry a miss.
ALIGN_SHAPE = 1
TRANSCRIBE_KWARGS = {
    "beam_size": 5,
    "word_timestamps": True,
    "vad_filter": False,
    "condition_on_previous_text": False,
    # Auto-detect. The caption track may be a translation, not the audio's
    # language, so it is never sent; `lang=auto` is an explicit key term instead.
    "language": None,
}

# --- what the panel shows (each quoted verbatim in docs/contracts.md) ---------
# T-020 align text: BEGIN
ALIGN_RATE_LIMITED_TEXT = ("YouTube is rate-limiting tldw serve, so it couldn't get "
                           "the audio. Turn this off and on again in a few minutes; "
                           "the wait is at most ten.")
FFMPEG_MISSING_TEXT = ("Word timing needs ffmpeg, and tldw serve can't find it. "
                       "Install ffmpeg, restart tldw serve, then turn this off and "
                       "on again.")
# yt-dlp's match filter skips a video with no duration and one over the ceiling
# alike, and can't say which, so this names both (T-020 build review, rev 5.4).
# The panel sets these as plain text after "{verb} {n} by caption line: ", so they
# carry no backticks and don't repeat that the words stay covered.
AUDIO_SKIPPED_TEXT = ("YouTube didn't give this video's length, or it's longer than "
                      "tldw serve's Whisper limit, so its audio can't be fetched. If "
                      "it's the limit, run tldw config set whisper-max-minutes "
                      "<minutes>, then turn this off and on again.")
TOO_LONG_TEXT = ("This video is longer than tldw serve's Whisper limit. To time "
                 "words, run tldw config set whisper-max-minutes <minutes>, then "
                 "turn this off and on again.")
ALIGN_BUSY_TEXT = ("Already timing words for another video. Try again when it "
                   "finishes.")
TX_BUSY_TEXT = ("Whisper is busy transcribing another video. Try again when it "
                "finishes.")
WHISPER_OFF_TEXT = "Whisper is turned off."
WHISPER_PINNED_OFF_TEXT = ("Whisper is turned off for tldw serve on this machine "
                           "(tldw config unset whisper turns it back on).")
WHISPER_MISSING_TEXT = "Whisper isn't installed for tldw serve."
MODEL_MISSING_TEXT = "Whisper's model isn't downloaded yet."
WHISPER_BROKEN_TEXT = ("Whisper is installed but could not be loaded. Try "
                       "reinstalling it: {hint}, then restart tldw serve.")
DOWNLOAD_TIMEOUT_TEXT = ("Fetching the video's audio timed out. Turn this off and on "
                         "again to retry.")
DOWNLOAD_FAILED_TEXT = ("Couldn't fetch this video's audio. Turn this off and on again "
                        "to retry.")
ALIGN_OTHER_TEXT = ("Timing the words failed unexpectedly. Turn this off and on again "
                    "to retry.")
# T-020 align text: END


class WindowError(ValueError):
    """A bad `windows` value; the message names the field."""


def parse_windows(raw) -> list[tuple[int, int]]:
    """The request's windows as (start_ms, end_ms) ints, or WindowError.

    Bools are refused even though they are ints. Overlapping windows are allowed;
    exact duplicates are collapsed by the caller. The duration ceiling is the
    route's business (a 413), not this function's.
    """
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ALIGN_WINDOWS:
        raise WindowError(f"windows must be a list of 1 to {MAX_ALIGN_WINDOWS} windows")
    out = []
    for i, win in enumerate(raw):
        if not isinstance(win, dict):
            raise WindowError(f"windows[{i}] must be an object")
        start, end = win.get("start_ms"), win.get("end_ms")
        for name, value in (("start_ms", start), ("end_ms", end)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise WindowError(f"windows[{i}].{name} must be a whole number")
        if not 0 <= start < end:
            raise WindowError(f"windows[{i}] must have 0 <= start_ms < end_ms")
        if end - start > MAX_ALIGN_WINDOW_MS:
            raise WindowError(f"windows[{i}] is longer than {MAX_ALIGN_WINDOW_MS} ms")
        out.append((start, end))
    return out


def cut_argv(audio: Path, workdir: Path, wav: Path, start_ms: int, end_ms: int) -> list:
    """ffmpeg argv for one window. Every item is a server-made number or path."""
    audio, workdir = Path(audio), Path(workdir)
    # A video id may begin with "-"; an absolute path inside our own temp dir can
    # never be read as an option.
    if not audio.is_absolute():
        raise ValueError("audio path must be absolute")
    if workdir.resolve() not in audio.resolve().parents:
        raise ValueError("audio path must be inside the work directory")
    return ["ffmpeg", "-nostdin", "-loglevel", "error",
            "-ss", to_ffmpeg_ts(start_ms), "-t", to_ffmpeg_ts(end_ms - start_ms),
            "-i", str(audio), "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
            "-f", "wav", "-y", str(wav)]


# The cut's shape, as it goes into the fingerprint (no paths, no times).
_CUT_SHAPE = ["ffmpeg", "-nostdin", "-loglevel", "error", "-ss", "-t", "-i",
              "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE), "-f", "wav", "-y"]

_fingerprint_memo: str | None = None


def fingerprint() -> str | None:
    """12 hex digits over everything that shapes a cached window, or None when a
    library version can't be read (then nothing is looked up, and the route says
    Whisper is missing). Computed on first use, never at import."""
    global _fingerprint_memo
    if _fingerprint_memo is not None:
        return _fingerprint_memo
    try:
        versions = {name: importlib.metadata.version(name)
                    for name in ("faster-whisper", "ctranslate2")}
    except importlib.metadata.PackageNotFoundError:
        return None
    parts = {
        "transcribe": TRANSCRIBE_KWARGS,
        "compute_type": whisper_transcribe.MODEL_COMPUTE_TYPE,
        "device": whisper_transcribe.MODEL_DEVICE,
        # cpu_threads is left out on purpose: it changes speed, not output.
        "cut": _CUT_SHAPE,
        "max_words": MAX_WORDS_PER_WINDOW,
        "max_chars": MAX_WORD_CHARS,
        "bad_ts_slack_ms": BAD_TIMESTAMP_SLACK_MS,
        "versions": versions,
        "shape": ALIGN_SHAPE,
    }
    blob = json.dumps(parts, sort_keys=True).encode("utf-8")
    _fingerprint_memo = hashlib.sha256(blob).hexdigest()[:12]
    return _fingerprint_memo


def cache_key(video_id: str, start_ms: int, end_ms: int, model: str, fp: str) -> str:
    """Everything that shapes one cached window. The language is fixed (`auto`),
    and is in the key so a language hint can never share entries with it."""
    return f"{video_id}|{start_ms}|{end_ms}|{model}|lang=auto|{fp}"


def cache_name(video_id: str, start_ms: int, end_ms: int, model: str, fp: str) -> str:
    key = cache_key(video_id, start_ms, end_ms, model, fp)
    return f"{video_id}-{hashlib.sha256(key.encode('utf-8')).hexdigest()[:16]}"


def _valid_words(words, start_ms: int, end_ms: int) -> bool:
    if not isinstance(words, list) or len(words) > MAX_WORDS_PER_WINDOW:
        return False
    for item in words:
        if not (isinstance(item, list) and len(item) == 3):
            return False
        s, e, w = item
        if not all(isinstance(v, int) and not isinstance(v, bool) for v in (s, e)):
            return False
        if not start_ms <= s <= e <= end_ms:
            return False
        if not isinstance(w, str) or not 0 < len(w) <= MAX_WORD_CHARS:
            return False
    return True


def get_window(video_id: str, start_ms: int, end_ms: int, model: str, fp: str):
    """[[s, e, w], ...] for a fresh, valid entry, else None. Anything off is a miss."""
    path = ALIGN_DIR / f"{cache_name(video_id, start_ms, end_ms, model, fp)}.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if time.time() - float(raw["saved_at"]) > txcache.TTL_SECONDS:
            return None
        # A copied file or a 16-hex collision must not serve another window.
        if (raw["video_id"], raw["start_ms"], raw["end_ms"]) != (video_id, start_ms, end_ms):
            return None
        if any(isinstance(raw[k], bool) or not isinstance(raw[k], int)
               for k in ("start_ms", "end_ms")):
            return None
        words = raw["words"]
        return words if _valid_words(words, start_ms, end_ms) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def put_window(video_id: str, start_ms: int, end_ms: int, model: str, fp: str,
               words: list) -> None:
    """Best effort: a failed write is logged (its type only) and never fails a request."""
    name = cache_name(video_id, start_ms, end_ms, model, fp)
    try:
        ALIGN_DIR.mkdir(parents=True, exist_ok=True)
        tmp = ALIGN_DIR / f"{name}.tmp"
        tmp.write_text(json.dumps({"saved_at": time.time(), "video_id": video_id,
                                   "start_ms": start_ms, "end_ms": end_ms,
                                   "words": words}), encoding="utf-8")
        tmp.replace(ALIGN_DIR / f"{name}.json")
    except Exception as exc:
        print(f"  align: cache write failed ({type(exc).__name__})", flush=True)


def prune() -> None:
    """Enforce the store's own cap. Once per request, not once per window."""
    try:
        if ALIGN_DIR.is_dir():
            txcache._prune(ALIGN_DIR, cap=ALIGN_MAX_ENTRIES)
    except Exception as exc:
        print(f"  align: cache prune failed ({type(exc).__name__})", flush=True)


def words_from_raw(raw_words, start_ms: int, end_ms: int) -> list:
    """Whisper's (start_s, end_s, text) relative to the window → [[s, e, w]] in
    absolute media ms. Bad timestamps are dropped; the rest are clamped."""
    out = []
    for start_s, end_s, text in raw_words:
        try:
            s = start_ms + round(float(start_s) * 1000)
            e = start_ms + round(float(end_s) * 1000)
        except (TypeError, ValueError, OverflowError):
            continue
        if e < s or s < start_ms - BAD_TIMESTAMP_SLACK_MS or e > end_ms + BAD_TIMESTAMP_SLACK_MS:
            continue
        s, e = max(start_ms, min(s, end_ms)), max(start_ms, min(e, end_ms))
        w = " ".join(str(text or "").split())[:MAX_WORD_CHARS]
        if not w:
            continue
        out.append([s, e, w])
        if len(out) >= MAX_WORDS_PER_WINDOW:
            break
    return out


def time_window(model, audio: Path, workdir: Path, index: int, start_ms: int,
                end_ms: int, *, deadline: float, clock=time.monotonic) -> list:
    """Cut one window and return the words Whisper heard in it.

    The deadline is checked between segments: a segment already decoding can't be
    interrupted, which is the honest limit of this loop.
    """
    wav = Path(workdir) / f"w{index}.wav"
    run(cut_argv(audio, workdir, wav, start_ms, end_ms),
        timeout=ALIGN_CUT_TIMEOUT_S, cwd=str(workdir))
    try:
        segments, _info = model.transcribe(str(wav), **TRANSCRIBE_KWARGS)
        raw = []
        for seg in segments:
            if clock() > deadline:
                raise TldrTimeoutError("Timing this window took too long.")
            for word in getattr(seg, "words", None) or []:
                raw.append((word.start, word.end, word.word))
        return words_from_raw(raw, start_ms, end_ms)
    finally:
        wav.unlink(missing_ok=True)
