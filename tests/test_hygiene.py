"""Disk cleanup: what it removes, and -- more importantly -- what it never does.

Every rule here deletes files on someone's machine, so each one is tested from
both sides. The "never" tests are the ones that matter.
"""

import json
import os
import time
from pathlib import Path

import pytest

from youtube_tldw import audio, cli, hygiene, txcache
from youtube_tldw import whisper_transcribe as wt

DAY = 86400
ALLOWED = [model for model, _label in audio.VOICES.values()]
DEFAULT = audio.VOICES[audio.DEFAULT_VOICE][0]


def _age(path: Path, days: float) -> None:
    t = time.time() - days * DAY
    os.utime(path, (t, t))


def _voice(name: str, days_since_spoken: float) -> Path:
    audio.VOICE_DIR.mkdir(parents=True, exist_ok=True)
    model = audio.VOICE_DIR / f"{name}.onnx"
    model.write_bytes(b"x" * 100)
    (audio.VOICE_DIR / f"{name}.onnx.json").write_text("{}")
    _age(model, days_since_spoken)
    return model


def _tracking_started(days_ago: float) -> None:
    hygiene.ensure_voice_tracking(audio.VOICE_DIR)
    _age(audio.VOICE_DIR / hygiene.VOICE_TRACKING_MARKER, days_ago)


# --- voices --------------------------------------------------------------------

def test_an_unlisted_voice_is_removable():
    """A model the allowlist can never select is pure clutter (63 MB today)."""
    _voice("en_US-ryan-medium", 1)
    found = {f.name for f in hygiene.unlisted_voice_files(audio.VOICE_DIR, ALLOWED)}
    assert found == {"en_US-ryan-medium.onnx", "en_US-ryan-medium.onnx.json"}


def test_unlisted_means_exact_voice_files_only():
    """Never the tracking marker, a partial download, or anything else there."""
    audio.VOICE_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("notes.txt", "en_US-x.onnx.part", hygiene.VOICE_TRACKING_MARKER):
        (audio.VOICE_DIR / name).write_text("keep me")
    assert hygiene.unlisted_voice_files(audio.VOICE_DIR, ALLOWED) == []


def test_no_voice_is_idle_until_tracking_has_run_thirty_days():
    """Until then a file's timestamp is its download date, not its last use.

    Two voices, so the old one isn't also protected by being the most recent --
    the grace period has to be the only thing keeping it.
    """
    _voice("en_US-joe-medium", 1)
    _voice("en_US-lessac-medium", 200)
    _tracking_started(days_ago=5)
    assert hygiene.idle_voice_files(audio.VOICE_DIR, ALLOWED, DEFAULT) == []


def test_no_voice_is_idle_before_tracking_exists_at_all():
    _voice("en_US-lessac-medium", 200)
    assert hygiene.idle_voice_files(audio.VOICE_DIR, ALLOWED, DEFAULT) == []


def test_a_voice_only_previewed_long_ago_is_idle():
    _voice("en_US-joe-medium", 1)                    # in use
    _voice("en_US-lessac-medium", 60)                # previewed once, months ago
    _tracking_started(days_ago=45)
    names = {f.name for f in hygiene.idle_voice_files(audio.VOICE_DIR, ALLOWED, DEFAULT)}
    assert names == {"en_US-lessac-medium.onnx", "en_US-lessac-medium.onnx.json"}


def test_the_default_voice_is_never_idle():
    _voice(DEFAULT, 400)
    _voice("en_US-joe-medium", 1)
    _tracking_started(days_ago=45)
    names = {f.name for f in hygiene.idle_voice_files(audio.VOICE_DIR, ALLOWED, DEFAULT)}
    assert not any(n.startswith(DEFAULT) for n in names)


def test_the_most_recently_spoken_voice_is_never_idle():
    """Even if nobody has listened to anything in months."""
    _voice("en_US-joe-medium", 90)
    _voice("en_US-lessac-medium", 120)
    _tracking_started(days_ago=150)
    names = {f.name for f in hygiene.idle_voice_files(audio.VOICE_DIR, ALLOWED, DEFAULT)}
    assert not any(n.startswith("en_US-joe-medium") for n in names)
    assert any(n.startswith("en_US-lessac-medium") for n in names)


def test_speaking_with_a_voice_marks_it_used_and_starts_tracking():
    model = _voice("en_US-joe-medium", 90)
    hygiene.mark_voice_spoken(model)
    assert time.time() - model.stat().st_mtime < 60
    assert (audio.VOICE_DIR / hygiene.VOICE_TRACKING_MARKER).exists()


def test_at_most_two_voices_stay_loaded(monkeypatch):
    """Each loaded voice holds its whole model in memory for the server's life."""
    import sys
    import types
    fake = types.SimpleNamespace(PiperVoice=types.SimpleNamespace(load=lambda k: f"v:{k}"))
    monkeypatch.setitem(sys.modules, "piper", fake)
    monkeypatch.setattr(audio, "_voice_cache", type(audio._voice_cache)())
    for name in ("a", "b", "c", "a"):
        audio._load_voice(Path(f"/v/{name}.onnx"))
    assert list(audio._voice_cache) == ["/v/c.onnx", "/v/a.onnx"]


# --- expired entries and leftover audio -----------------------------------------

def _entry(d: Path, name: str, saved_days_ago: float) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{name}.json"
    f.write_text(json.dumps({"saved_at": time.time() - saved_days_ago * DAY}))
    return f


def test_only_entries_past_their_ttl_are_expired():
    old = _entry(txcache.CACHE_DIR, "old", 20)
    fresh = _entry(txcache.CACHE_DIR, "fresh", 2)
    found = hygiene.expired_entries(txcache.CACHE_DIR, txcache.TTL_SECONDS)
    assert found == [old] and fresh.exists()


def test_an_unreadable_entry_is_left_alone_not_guessed_at():
    txcache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (txcache.CACHE_DIR / "broken.json").write_text("{not json")
    assert hygiene.expired_entries(txcache.CACHE_DIR, txcache.TTL_SECONDS) == []


def test_only_stale_temp_files_are_collected():
    txcache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    stale = txcache.CACHE_DIR / "a.tmp"
    stale.write_text("x")
    _age(stale, 1)
    live = txcache.CACHE_DIR / "b.tmp"                # a write in progress
    live.write_text("x")
    assert hygiene.expired_entries(txcache.CACHE_DIR, txcache.TTL_SECONDS) == [stale]


def test_a_fresh_audio_temp_dir_is_never_swept(tmp_path):
    """A second `tldw serve` on another port must not delete a live download."""
    import tempfile
    root = Path(tempfile.gettempdir())
    live = root / (hygiene.AUDIO_TMP_PREFIX + "live")
    live.mkdir()
    old = root / (hygiene.AUDIO_TMP_PREFIX + "old")
    old.mkdir()
    _age(old, 2)
    out = hygiene.sweep_stale_audio(6 * 3600)
    assert out.removed == [old] and live.exists() and not old.exists()


# --- the guard every deletion goes through ---------------------------------------

def test_nothing_outside_the_base_is_ever_removed(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "precious.txt"
    outside.write_text("keep")
    out = hygiene.Removal()
    hygiene.remove_under(outside, base, out, dry_run=False)
    hygiene.remove_under(base / ".." / "precious.txt", base, out, dry_run=False)
    assert outside.exists() and out.removed == []


def test_a_symlink_is_never_followed_or_removed(tmp_path):
    base = tmp_path / "base"
    base.mkdir()
    # One pointing out of the base (the containment check would catch that too)...
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "data").write_text("keep")
    out_link = base / "out-link"
    out_link.symlink_to(target)
    # ...and one pointing *inside* it, which only the symlink rule protects: it's
    # somebody's deliberate link, and cleanup has no business removing it.
    inner = base / "inner.json"
    inner.write_text("keep")
    in_link = base / "in-link.json"
    in_link.symlink_to(inner)
    out = hygiene.Removal()
    for link in (out_link, in_link):
        hygiene.remove_under(link, base, out, dry_run=False)
    assert (target / "data").exists() and out_link.is_symlink()
    assert in_link.is_symlink() and inner.exists()
    assert out.removed == []


def test_the_base_itself_is_never_removed(tmp_path):
    out = hygiene.Removal()
    hygiene.remove_under(tmp_path, tmp_path, out, dry_run=False)
    assert tmp_path.exists()


# --- the command -------------------------------------------------------------------

def test_clean_removes_what_it_names_and_nothing_else(capsys):
    expired = _entry(txcache.SUMMARY_DIR, "old", 30)
    fresh = _entry(txcache.SUMMARY_DIR, "fresh", 1)
    unlisted = _voice("en_US-ryan-medium", 1)
    default = _voice(DEFAULT, 400)
    wt._CACHE_DIR.mkdir(parents=True, exist_ok=True)
    whisper_model = wt._CACHE_DIR / "model.bin"
    whisper_model.write_bytes(b"w" * 10)
    _age(whisper_model, 400)
    assert cli._run_cache(["clean"]) == 0
    assert not expired.exists() and not unlisted.exists()
    assert fresh.exists() and default.exists()
    assert whisper_model.exists(), "Whisper models are never removed"


def test_huggingface_models_are_reported_never_removed(monkeypatch, tmp_path, capsys):
    hub = tmp_path / "hf" / "hub"
    m = hub / "models--Systran--faster-whisper-large-v3"
    m.mkdir(parents=True)
    (m / "model.bin").write_bytes(b"x" * 1000)
    monkeypatch.setenv("HF_HUB_CACHE", str(hub))
    assert cli._run_cache(["clean"]) == 0
    assert (m / "model.bin").exists()
    out = capsys.readouterr().out
    assert "Not managed by tldw" in out and "faster-whisper-large-v3" in out


def test_a_dry_run_removes_nothing(capsys):
    expired = _entry(txcache.CACHE_DIR, "old", 30)
    unlisted = _voice("en_US-ryan-medium", 1)
    assert cli._run_cache(["clean", "--dry-run"]) == 0
    assert expired.exists() and unlisted.exists()
    assert "would remove" in capsys.readouterr().out


def test_the_report_names_every_area(capsys):
    assert cli._run_cache([]) == 0
    out = capsys.readouterr().out
    for area in ("transcripts", "summaries", "key moments", "voices", "whisper models"):
        assert area in out
