"""What tldw keeps on disk, and how it stays tidy.

Every deletion here follows the same rules, because the alternative is a cleanup
that removes something it shouldn't:

- Each function takes the directory it may delete from as an argument, and refuses
  anything that resolves outside it, and anything that is a symlink.
- Nothing is deleted on a guess. Each rule names exactly what it removes -- an
  expired cache entry, a voice file the allowlist can never select, a temp
  directory older than any transcription could run -- and leaves everything else.
- Some things are never deleted at all: the Whisper models (a 0.5-3 GB re-download)
  and anything tldw doesn't own, such as Hugging Face's own cache. Those are
  *reported*, with the command to remove them, and the decision stays with the
  person whose disk it is.
"""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

AUDIO_TMP_PREFIX = "youtube-tldw-audio-"
STRAY_TMP_AGE_S = 3600            # a .tmp left by a write that never finished
VOICE_IDLE_DAYS = 30
# Created the first time voice use is recorded. Until tracking has run for
# VOICE_IDLE_DAYS, a voice file's timestamp is its download date, not its last
# use -- so nothing is judged idle before then.
VOICE_TRACKING_MARKER = ".spoken-tracking-since"


@dataclass
class Removal:
    removed: list = field(default_factory=list)      # Paths
    freed: int = 0                                     # bytes


def _inside(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def _size(path: Path) -> int:
    try:
        if path.is_symlink():
            return 0
        if path.is_file():
            return path.stat().st_size
        return sum(p.stat().st_size for p in path.rglob("*")
                   if p.is_file() and not p.is_symlink())
    except OSError:
        return 0


def remove_under(path: Path, base: Path, out: Removal, *, dry_run: bool) -> None:
    """Delete one file or directory under `base`. Refuses anything else."""
    if path.is_symlink() or not _inside(path, base) or path.resolve() == base.resolve():
        return
    size = _size(path)
    if not dry_run:
        try:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink()
        except FileNotFoundError:
            return
        except OSError:
            return
    out.removed.append(path)
    out.freed += size


# --- leftover transcription audio ----------------------------------------------

def stale_audio_dirs(tmp_root: Path, max_age_s: float, *, now: float | None = None):
    """Audio temp directories older than any transcription could still be using.

    Age, not "no server running": a second `tldw serve` on another port binds fine
    and must not delete a live job's download. The caller passes an age longer than
    the longest transcription budget.
    """
    now = time.time() if now is None else now
    try:
        candidates = list(Path(tmp_root).glob(AUDIO_TMP_PREFIX + "*"))
    except OSError:
        return []
    out = []
    for d in candidates:
        try:
            if d.is_dir() and not d.is_symlink() and now - d.stat().st_mtime > max_age_s:
                out.append(d)
        except OSError:
            continue
    return out


def sweep_stale_audio(max_age_s: float, *, tmp_root: Path | None = None,
                      dry_run: bool = False) -> Removal:
    root = Path(tmp_root or tempfile.gettempdir())
    out = Removal()
    for d in stale_audio_dirs(root, max_age_s):
        remove_under(d, root, out, dry_run=dry_run)
    return out


# --- voices ----------------------------------------------------------------------

def _voice_stem(name: str) -> str | None:
    """The model stem of a voice file, or None for anything that isn't one."""
    for suffix in (".onnx.json", ".onnx"):
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return None


def unlisted_voice_files(voice_dir: Path, allowed_stems) -> list:
    """Voice files the allowlist can never select. Exact `<stem>.onnx` and
    `<stem>.onnx.json` names only -- never the tracking marker, never a partial
    download, never anything else that happens to be in the directory."""
    allowed = set(allowed_stems)
    try:
        entries = list(Path(voice_dir).iterdir())
    except OSError:
        return []
    return [f for f in entries
            if f.is_file() and not f.is_symlink()
            and (stem := _voice_stem(f.name)) is not None and stem not in allowed]


def ensure_voice_tracking(voice_dir: Path) -> None:
    try:
        Path(voice_dir).mkdir(parents=True, exist_ok=True)
        marker = Path(voice_dir) / VOICE_TRACKING_MARKER
        if not marker.exists():
            marker.touch()
    except OSError:
        pass


def mark_voice_spoken(model_file: Path) -> None:
    """Record that a voice was used to speak (not merely previewed)."""
    try:
        ensure_voice_tracking(Path(model_file).parent)
        os.utime(model_file, None)
    except OSError:
        pass


def idle_voice_files(voice_dir: Path, allowed_stems, keep_stem: str, *,
                     now: float | None = None,
                     idle_days: float = VOICE_IDLE_DAYS) -> list:
    """Voices nobody has spoken with in `idle_days` -- most often ones that were
    only ever previewed. Never `keep_stem` (the default voice), never the most
    recently spoken one, and nothing at all until tracking has run `idle_days`.

    Removing one costs, at most, a re-download if it's picked again.
    """
    now = time.time() if now is None else now
    voice_dir = Path(voice_dir)
    limit = idle_days * 86400
    try:
        since = (voice_dir / VOICE_TRACKING_MARKER).stat().st_mtime
    except OSError:
        return []                       # tracking hasn't started: nothing is known
    if now - since < limit:
        return []
    allowed = set(allowed_stems)
    models = []
    for f in voice_dir.glob("*.onnx"):
        stem = _voice_stem(f.name)
        if stem in allowed and f.is_file() and not f.is_symlink():
            try:
                models.append((stem, f.stat().st_mtime))
            except OSError:
                continue
    if not models:
        return []
    newest = max(models, key=lambda m: m[1])[0]
    victims = {stem for stem, mtime in models
               if stem not in (keep_stem, newest) and now - mtime > limit}
    return [f for f in voice_dir.iterdir()
            if f.is_file() and _voice_stem(f.name) in victims]


# --- expired cache entries -------------------------------------------------------

def expired_entries(cache_dir: Path, ttl_s: float, *, now: float | None = None) -> list:
    """Cache files past their TTL, plus stray .tmp files from a write that never
    finished. A file that can't be read is left alone rather than guessed at."""
    now = time.time() if now is None else now
    out = []
    try:
        entries = list(Path(cache_dir).iterdir())
    except OSError:
        return []
    for f in entries:
        if not f.is_file() or f.is_symlink():
            continue
        try:
            if f.suffix == ".tmp":
                if now - f.stat().st_mtime > STRAY_TMP_AGE_S:
                    out.append(f)
                continue
            if f.suffix != ".json":
                continue
            saved = float(json.loads(f.read_text(encoding="utf-8"))["saved_at"])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if now - saved > ttl_s:
            out.append(f)
    return out


# --- what isn't ours -------------------------------------------------------------

def huggingface_whisper_models() -> list:
    """faster-whisper models in Hugging Face's own cache. tldw keeps its models in
    its own directory, so these were downloaded by something else -- they are
    reported, never deleted."""
    hub = os.environ.get("HF_HUB_CACHE")
    if hub:
        root = Path(hub)
    else:
        root = Path(os.environ.get("HF_HOME") or Path.home() / ".cache" / "huggingface") / "hub"
    try:
        return sorted(p for p in root.glob("models--Systran--faster-whisper-*")
                      if p.is_dir() and not p.is_symlink())
    except OSError:
        return []


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} GB"
