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
TTL_SECONDS = 14 * 24 * 3600      # transcripts rarely change; a fortnight is ample
MAX_ENTRIES = 500


def _path(video_id: str) -> Path:
    # video_id is validated as [A-Za-z0-9_-]{11} long before this, so it can't
    # escape the directory.
    return CACHE_DIR / f"{video_id}.json"


def get(video_id: str):
    """(meta, cues) if a fresh copy is on disk, else None."""
    try:
        raw = json.loads(_path(video_id).read_text(encoding="utf-8"))
        if time.time() - float(raw["saved_at"]) > TTL_SECONDS:
            return None
        m = raw["meta"]
        meta = VideoMeta(m["video_id"], m["title"], m["channel"],
                         int(m["duration_ms"]), m.get("subtitles") or {},
                         m.get("auto_captions") or {})
        return meta, [Cue(int(c[0]), int(c[1]), c[2]) for c in raw["cues"]]
    except (OSError, ValueError, KeyError, TypeError, IndexError):
        return None                       # missing, truncated, or from an older shape


def put(video_id: str, meta, cues: list) -> None:
    """Best effort: a cache that can't be written must not fail the request."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "saved_at": time.time(),
            "meta": {"video_id": meta.video_id, "title": meta.title,
                     "channel": meta.channel, "duration_ms": meta.duration_ms,
                     "subtitles": meta.subtitles, "auto_captions": meta.auto_captions},
            "cues": [[c.start_ms, c.end_ms, c.text] for c in cues],
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
