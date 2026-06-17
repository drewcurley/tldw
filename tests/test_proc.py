import subprocess

import pytest

from youtube_tldw import TldrError, proc


def test_success_returns_stdout():
    r = proc.run(["printf", "hello"])
    assert r.returncode == 0 and r.stdout == "hello"


def test_stdin_is_passed():
    r = proc.run(["cat"], stdin="piped-data")
    assert r.stdout == "piped-data"


def test_nonzero_exit_raises_with_tail():
    with pytest.raises(TldrError):
        proc.run(["sh", "-c", "echo boom >&2; exit 3"])


def test_nonzero_exit_no_check_returns():
    r = proc.run(["sh", "-c", "exit 3"], check=False)
    assert r.returncode == 3


def test_timeout_raises(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=0.01)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(TldrError):
        proc.run(["sleep", "10"], timeout=0.01)


def test_missing_binary_raises():
    with pytest.raises(TldrError):
        proc.run(["this-binary-does-not-exist-xyz"])


def test_empty_argv_rejected():
    with pytest.raises(ValueError):
        proc.run([])


def test_require_missing():
    with pytest.raises(TldrError):
        proc.require("definitely-not-a-real-binary-xyz")


def test_require_present():
    proc.require("sh")  # should not raise
