import subprocess
import threading
import time

import pytest

from youtube_tldw import TldrError, TldrTimeoutError, proc


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


def test_stream_filter_yields_output_incrementally():
    """`cat` echoes stdin: what the feed writes comes back out, in order."""
    def feed(write):
        for i in range(5):
            write(f"line{i}\n".encode())
    out = b"".join(proc.stream_filter(["cat"], feed))
    assert out == b"".join(f"line{i}\n".encode() for i in range(5))


def test_stream_filter_starts_yielding_before_the_feed_finishes():
    """The point of the whole thing: output arrives while input is still coming."""
    gate = threading.Event()

    def feed(write):
        write(b"first\n")
        gate.wait(timeout=5)          # held until the consumer has seen that block
        write(b"second\n")

    gen = proc.stream_filter(["cat"], feed)
    first = next(gen)
    assert first == b"first\n"        # yielded while feed is still blocked
    gate.set()
    assert first + b"".join(gen) == b"first\nsecond\n"


def test_stream_filter_raises_on_nonzero_exit():
    with pytest.raises(TldrError):
        list(proc.stream_filter(["ls", "/definitely/not/here"], lambda w: None))


def test_stream_filter_propagates_feed_errors():
    def feed(write):
        write(b"partial")
        raise TldrError("synthesis blew up")

    with pytest.raises(TldrError, match="synthesis blew up"):
        list(proc.stream_filter(["cat"], feed))


def test_stream_filter_times_out_and_kills_the_process():
    def feed(write):
        write(b"hi\n")
        time.sleep(30)                # never finishes on its own

    with pytest.raises(TldrTimeoutError):
        list(proc.stream_filter(["cat"], feed, timeout=0.3))


def test_stream_filter_missing_binary():
    with pytest.raises(TldrError):
        list(proc.stream_filter(["definitely-not-a-real-binary-xyz"], lambda w: None))


def test_stream_filter_rejects_bad_argv():
    with pytest.raises(ValueError):
        list(proc.stream_filter([], lambda w: None))
