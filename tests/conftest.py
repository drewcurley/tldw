"""Keep every test out of the real home directory.

Before this existed, the suite appended rows to the real usage log on every run
(1,570 of them, by the time anyone noticed), read the real config file (so setting
a documented option like `whisper: off` turned 16 tests red), and could in
principle have launched the real `claude` and spent real quota.

Every path tldw derives from the home directory is a module constant evaluated
at import, so setting HOME afterwards moves none of them. They are redirected
one by one instead, and `test_conftest_covers_every_home_path` fails the build if
`src/` gains one this list doesn't know about.
"""

from __future__ import annotations

import importlib
import os
import stat
import tempfile

import pytest

# (module, attribute, path relative to the fake home). Keep in step with src/:
# the guard test below scans for `Path.home()` and compares.
HOME_PATHS = [
    ("youtube_tldw.config", "CONFIG_FILE", ".config/youtube-tldw/config.json"),
    ("youtube_tldw.server", "TOKEN_FILE", ".config/youtube-tldw/token"),
    ("youtube_tldw.usage", "USAGE_FILE", ".config/youtube-tldw/usage.jsonl"),
    ("youtube_tldw.txcache", "CACHE_DIR", ".cache/youtube-tldw/transcripts"),
    ("youtube_tldw.txcache", "SUMMARY_DIR", ".cache/youtube-tldw/summaries"),
    ("youtube_tldw.whisper_transcribe", "_CACHE_DIR", ".cache/youtube-tldw/whisper"),
    ("youtube_tldw.audio", "VOICE_DIR", ".cache/youtube-tldw/voices"),
    ("youtube_tldw.cli", "DEFAULT_OUTPUT", "Downloads/youtube-tldw/tldws"),
]

_REFUSE = """#!/bin/sh
echo "tests must not run the real $(basename "$0"): stub it" >&2
exit 97
"""


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    for module, attr, rel in HOME_PATHS:
        monkeypatch.setattr(importlib.import_module(module), attr, home / rel)

    # Subprocesses that resolve ~ themselves, and the audio-temp sweep, which uses
    # gettempdir() -- without this a sweep test would reach the real /tmp.
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    scratch = tmp_path / "tmp"
    scratch.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(scratch))

    # Configuration that changes behaviour, from the shell the suite runs in.
    for key in [k for k in os.environ if k.startswith("TLDW_")]:
        monkeypatch.delenv(key)
    monkeypatch.delenv("DISABLE_PROMPT_CACHING", raising=False)

    # A test that forgets to stub these must fail loudly, not spend real quota or
    # make a real request to YouTube. A test that installs its own fake prepends
    # it to PATH afterwards, and that one wins.
    refuse = tmp_path / "refuse-bin"
    refuse.mkdir()
    for name in ("claude", "yt-dlp"):
        p = refuse / name
        p.write_text(_REFUSE)
        p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", str(refuse) + os.pathsep + os.environ.get("PATH", ""))
    yield home


@pytest.fixture(autouse=True)
def _fresh_server_state():
    """Every in-process registry the server keeps, emptied around each test.

    One wedged entry here (a prefetch event never set, a transcription job never
    popped) would otherwise poison every later test that touches the same video.
    """
    from youtube_tldw import server
    registries = [getattr(server, name) for name in (
        "_transcript_cache", "_seg_cache", "_ask_sessions", "_tx_jobs",
        "_seg_prefetch_events", "_seg_progress", "_captionless_meta",
    ) if hasattr(server, name)]
    for reg in registries:
        reg.clear()
    yield
    for reg in registries:
        reg.clear()
