"""Transcripts and summaries on disk, so a restart doesn't throw away work.

The server has always cached transcripts in memory, which meant every `tldw serve`
restart re-fetched every video — and YouTube answers bursts from one address with
HTTP 429. A transcript is small, immutable for a given video, and expensive to get,
which makes it exactly the thing worth keeping on disk.

Entries carry their own expiry so a video whose captions change is eventually
refetched; a corrupt or unreadable file is a cache miss, never an error.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .metadata import VideoMeta
from .transcript import Cue

CACHE_DIR = Path.home() / ".cache" / "youtube-tldw" / "transcripts"
SUMMARY_DIR = Path.home() / ".cache" / "youtube-tldw" / "summaries"
# Their own directory: summaries and selections are pruned separately, so a
# burst of selections can never push summaries out of the cap.
SEGMENT_DIR = Path.home() / ".cache" / "youtube-tldw" / "segments"
TTL_SECONDS = 14 * 24 * 3600      # transcripts rarely change; a fortnight is ample
MAX_ENTRIES = 500


def _path(video_id: str) -> Path:
    # video_id is validated as [A-Za-z0-9_-]{11} long before this, so it can't
    # escape the directory.
    return CACHE_DIR / f"{video_id}.json"


CAPTIONS = "captions"             # the source of every entry written before this


# Bumped when entries written by older code can no longer be trusted. Generation 2:
# after an in-page navigation, extensions before the sync check sent the *previous*
# video's title, channel and duration (and sometimes its transcript), and the server
# stored them. Caption entries below this generation are misses; audio entries are
# not, because their metadata came from yt-dlp, never from the page, and they cost
# minutes of CPU to remake. The summary and segment keys hash it too.
META_GENERATION = 2


def _generation_ok(gen) -> bool:
    # bool is an int in Python; True must not pass for generation 1.
    return isinstance(gen, int) and not isinstance(gen, bool) and gen >= META_GENERATION


def _read(video_id: str):
    """(status, entry): one parser behind both get() and get_status(), so the two
    can't drift. status is "hit", "missing", "expired", "stale-gen" or "unreadable";
    entry is (meta, cues, source) for a hit and None otherwise."""
    try:
        text = _path(video_id).read_text(encoding="utf-8")
    except FileNotFoundError:
        return "missing", None
    except (OSError, ValueError):         # ValueError: bytes that aren't UTF-8
        return "unreadable", None
    try:
        raw = json.loads(text)
        if time.time() - float(raw["saved_at"]) > TTL_SECONDS:
            return "expired", None
        source = raw.get("source") or CAPTIONS
        if not isinstance(source, str):
            source = CAPTIONS
        # The generation is read for caption entries only, so a corrupt field on an
        # audio entry can never cost a re-transcription.
        if not source.startswith("audio:") and not _generation_ok(raw.get("gen")):
            return "stale-gen", None
        meta = _meta_from(raw["meta"])
        return "hit", (meta, [Cue(int(c[0]), int(c[1]), c[2]) for c in raw["cues"]],
                       source)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError):
        return "unreadable", None         # truncated, or from an older shape


def get(video_id: str):
    """(meta, cues, source) if a fresh copy is on disk, else None.

    `source` is "captions" or "audio:<model>". It is read with a default rather
    than as a required key: treating it as required would turn every cache file
    written by an older version into a miss, and on a captionless video a miss
    now costs minutes of transcription rather than one yt-dlp call.
    """
    status, entry = _read(video_id)
    return entry if status == "hit" else None


def get_status(video_id: str) -> str:
    """Why get() would return what it does -- for the log, not for decisions."""
    return _read(video_id)[0]


def _meta_dict(meta) -> dict:
    return {"video_id": meta.video_id, "title": meta.title,
            "channel": meta.channel, "duration_ms": meta.duration_ms}


def _meta_from(m: dict) -> VideoMeta:
    return VideoMeta(m["video_id"], m["title"], m["channel"], int(m["duration_ms"]),
                     m.get("subtitles") or {}, m.get("auto_captions") or {})


def put(video_id: str, meta, cues: list, source: str = CAPTIONS) -> None:
    """Best effort: a cache that can't be written must not fail the request."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": time.time(),
            # No caption-track maps: they're only used to pick a track on freshly
            # fetched metadata, never on a cached one, and they were most of the
            # file -- 579KB for the largest entry against a 32KB median.
            "meta": _meta_dict(meta),
            "cues": [[c.start_ms, c.end_ms, c.text] for c in cues],
            "source": source,
            "gen": META_GENERATION,
        }
        tmp = _path(video_id).with_suffix(".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(_path(video_id))      # atomic: never a half-written cache file
        _prune()
    except Exception:
        # Deliberately broad. This is a speed-up for a request that has already
        # succeeded; nothing here is worth failing that request over — not a full
        # disk, not a read-only home, not an unexpected cue shape.
        pass


def _prune(directory: Path | None = None) -> None:
    target = directory or CACHE_DIR
    files = sorted(target.glob("*.json"), key=lambda f: f.stat().st_mtime)
    for stale in files[:max(0, len(files) - MAX_ENTRIES)]:
        stale.unlink(missing_ok=True)


# --- summaries ----------------------------------------------------------------
#
# Keyed by video *and* by everything that shapes the answer — the model, the trim
# ratio, and a fingerprint of the prompts themselves — so changing any of them
# produces a miss rather than serving something the current code wouldn't produce.

def get_summary(key: str):
    """The stored payload for this key, or None."""
    try:
        raw = json.loads((SUMMARY_DIR / f"{key}.json").read_text(encoding="utf-8"))
        if time.time() - float(raw["saved_at"]) > TTL_SECONDS:
            return None
        payload = raw["payload"]
        return payload if isinstance(payload, dict) else None
    except (OSError, ValueError, KeyError, TypeError):
        return None


def put_summary(key: str, payload: dict) -> None:
    """Best effort, like the transcript side: never fail a finished request."""
    try:
        SUMMARY_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SUMMARY_DIR / f"{key}.tmp"
        tmp.write_text(json.dumps({"saved_at": time.time(), "payload": payload}),
                       encoding="utf-8")
        tmp.replace(SUMMARY_DIR / f"{key}.json")
        _prune(SUMMARY_DIR)
    except Exception:
        pass


# --- segment selections -------------------------------------------------------
#
# A selection costs a model call, and it used to live only in memory -- so every
# restart, and every re-open of a summary served from the summary cache, paid for
# it again. The key arrives already built (see server._seg_key): it covers the
# video, the trim ratio, the selection prompt, the backend and the transcript.

def get_segments(name: str):
    """(meta, segments) stored under this name, or None."""
    try:
        raw = json.loads((SEGMENT_DIR / f"{name}.json").read_text(encoding="utf-8"))
        if time.time() - float(raw["saved_at"]) > TTL_SECONDS:
            return None
        segments = raw["segments"]
        if not isinstance(segments, list):
            return None
        return _meta_from(raw["meta"]), segments
    except (OSError, ValueError, KeyError, TypeError):
        return None


def put_segments(name: str, meta, segments: list) -> None:
    """Best effort, like everything here: never fail a finished request."""
    try:
        SEGMENT_DIR.mkdir(parents=True, exist_ok=True)
        tmp = SEGMENT_DIR / f"{name}.tmp"
        tmp.write_text(json.dumps({"saved_at": time.time(), "meta": _meta_dict(meta),
                                   "segments": segments}), encoding="utf-8")
        tmp.replace(SEGMENT_DIR / f"{name}.json")
        _prune(SEGMENT_DIR)
    except Exception:
        pass
