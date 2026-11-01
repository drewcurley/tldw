"""The suite must never touch the real home directory or launch real binaries.

Each of these guards exists because the thing it prevents already happened once.
"""

import importlib
import re
import subprocess
from pathlib import Path

import pytest

from conftest import HOME_PATHS

SRC = Path(__file__).resolve().parent.parent / "src" / "youtube_tldw"


def test_conftest_covers_every_home_path():
    """A new `X = Path.home() / ...` in src/ must be added to HOME_PATHS, or the
    suite starts writing into the real home directory again -- silently."""
    found = set()
    for path in SRC.glob("*.py"):
        for m in re.finditer(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*Path\.home\(\)",
                             path.read_text(encoding="utf-8"), re.M):
            found.add((f"youtube_tldw.{path.stem}", m.group(1)))
    covered = {(mod, attr) for mod, attr, _ in HOME_PATHS}
    assert found <= covered, f"not redirected by tests/conftest.py: {found - covered}"


def test_every_home_path_points_inside_the_test_dir(tmp_path):
    for module, attr, _ in HOME_PATHS:
        value = Path(getattr(importlib.import_module(module), attr))
        assert tmp_path in value.parents, f"{module}.{attr} = {value}"


def test_the_real_claude_cannot_run_from_a_test():
    """A test that forgets to stub claude must fail, not spend real quota."""
    r = subprocess.run(["claude", "-p"], input="hi", capture_output=True, text=True)
    assert r.returncode == 97
    assert "must not run the real claude" in r.stderr


def test_the_real_ytdlp_cannot_run_from_a_test():
    r = subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True)
    assert r.returncode == 97


def test_the_shell_environment_does_not_leak_in(monkeypatch):
    import os
    assert not any(k.startswith("TLDW_") for k in os.environ)
    assert "DISABLE_PROMPT_CACHING" not in os.environ


def test_the_real_config_is_not_read():
    """With `whisper: off` in the real config, 16 tests used to fail."""
    from youtube_tldw import config
    assert config.get("whisper") is None
    assert config.get("llm_cmd") is None
