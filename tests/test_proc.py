import subprocess
import sys
import threading
import time

import pytest

from youtube_tldw import TldrError, TldrTimeoutError, proc

# These tests need real child processes, but `cat`/`printf`/`sh` don't exist on
# Windows. The interpreter running the tests does, on every platform — so stand the
# helpers up with it instead of depending on a POSIX userland.
ECHO = [sys.executable, "-c", "import sys; sys.stdout.write('hello')"]
# read1 + flush, not copyfileobj: the streaming test asserts output appears while
# input is still arriving, and copyfileobj's internal buffering defeats exactly that.
CAT = [sys.executable, "-u", "-c",
       "import sys\n"
       "while True:\n"
       "    b = sys.stdin.buffer.read1(65536)\n"
       "    if not b: break\n"
       "    sys.stdout.buffer.write(b); sys.stdout.buffer.flush()"]
FAIL = [sys.executable, "-c", "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"]
SLEEP = [sys.executable, "-c", "import time; time.sleep(10)"]


def test_success_returns_stdout():
    r = proc.run(ECHO)
    assert r.returncode == 0 and r.stdout == "hello"


def test_stdin_is_passed():
    r = proc.run(CAT, stdin="piped-data")
    assert r.stdout == "piped-data"


def test_nonzero_exit_raises_with_tail():
    with pytest.raises(TldrError):
        proc.run(FAIL)


def test_nonzero_exit_no_check_returns():
    r = proc.run(FAIL, check=False)
    assert r.returncode == 3


def test_timeout_raises(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="x", timeout=0.01)

    monkeypatch.setattr(subprocess, "run", boom)
    with pytest.raises(TldrError):
        proc.run(SLEEP, timeout=0.01)


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
    proc.require("python3" if sys.platform != "win32" else "python")


def test_stream_filter_yields_output_incrementally():
    """`cat` echoes stdin: what the feed writes comes back out, in order."""
    def feed(write):
        for i in range(5):
            write(f"line{i}\n".encode())
    out = b"".join(proc.stream_filter(CAT, feed))
    assert out == b"".join(f"line{i}\n".encode() for i in range(5))


def test_stream_filter_starts_yielding_before_the_feed_finishes():
    """The point of the whole thing: output arrives while input is still coming."""
    gate = threading.Event()

    def feed(write):
        write(b"first\n")
        gate.wait(timeout=5)          # held until the consumer has seen that block
        write(b"second\n")

    gen = proc.stream_filter(CAT, feed)
    first = next(gen)
    assert first == b"first\n"        # yielded while feed is still blocked
    gate.set()
    assert first + b"".join(gen) == b"first\nsecond\n"


def test_stream_filter_raises_on_nonzero_exit():
    with pytest.raises(TldrError):
        list(proc.stream_filter(FAIL, lambda w: None))


def test_stream_filter_propagates_feed_errors():
    def feed(write):
        write(b"partial")
        raise TldrError("synthesis blew up")

    with pytest.raises(TldrError, match="synthesis blew up"):
        list(proc.stream_filter(CAT, feed))


def test_stream_filter_times_out_and_kills_the_process():
    def feed(write):
        write(b"hi\n")
        time.sleep(30)                # never finishes on its own

    with pytest.raises(TldrTimeoutError):
        list(proc.stream_filter(CAT, feed, timeout=0.3))


def test_stream_filter_missing_binary():
    with pytest.raises(TldrError):
        list(proc.stream_filter(["definitely-not-a-real-binary-xyz"], lambda w: None))


def test_stream_filter_rejects_bad_argv():
    with pytest.raises(ValueError):
        list(proc.stream_filter([], lambda w: None))


def test_resolve_returns_an_absolute_path():
    """Resolving via which() is what makes PATHEXT work on Windows."""
    resolved = proc._resolve([sys.executable, "-c", "pass"])
    assert resolved[0] and resolved[1:] == ["-c", "pass"]


def test_resolve_leaves_a_missing_binary_alone():
    """The caller's own 'not installed' error is better than one from here."""
    assert proc._resolve(["definitely-not-real-xyz", "-x"]) == \
        ["definitely-not-real-xyz", "-x"]


def test_resolve_wraps_a_windows_cmd_shim(monkeypatch):
    """npm installs the claude CLI as claude.cmd, which CreateProcess can't run."""
    monkeypatch.setattr(proc.os, "name", "nt")
    monkeypatch.setattr(proc.shutil, "which", lambda n: r"C:\npm\claude.CMD")
    assert proc._resolve(["claude", "-p"]) == \
        ["cmd", "/d", "/s", "/c", r"C:\npm\claude.CMD", "-p"]


def test_resolve_refuses_cmd_metacharacters(monkeypatch):
    """cmd.exe re-parses its command line, so data must not reach it as syntax."""
    monkeypatch.setattr(proc.os, "name", "nt")
    monkeypatch.setattr(proc.shutil, "which", lambda n: r"C:\npm\tool.cmd")
    with pytest.raises(TldrError, match="metacharacters"):
        proc._resolve(["tool", "a & calc.exe"])


def test_resolve_does_not_wrap_a_real_exe(monkeypatch):
    monkeypatch.setattr(proc.os, "name", "nt")
    monkeypatch.setattr(proc.shutil, "which", lambda n: r"C:\ffmpeg\ffmpeg.exe")
    assert proc._resolve(["ffmpeg", "-y"]) == [r"C:\ffmpeg\ffmpeg.exe", "-y"]
