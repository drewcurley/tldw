"""Single subprocess chokepoint.

Every external command (claude, yt-dlp, ffmpeg, ffprobe) runs through here so the
shell=False / argv-list / stdin / timeout policy lives in exactly one place. We
NEVER use shell=True and NEVER build command strings from untrusted input.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass

from . import TldrError, TldrTimeoutError


@dataclass
class ProcResult:
    returncode: int
    stdout: str
    stderr: str


def require(*binaries: str) -> None:
    """Fail fast if a required binary is not on PATH."""
    missing = [b for b in binaries if shutil.which(b) is None]
    if missing:
        raise TldrError(
            "Missing required program(s): "
            + ", ".join(missing)
            + ". Please install them and try again."
        )


# cmd.exe re-parses its command line, so these would stop being inert data.
_CMD_META = re.compile(r"[&|<>^]")


def _resolve(argv: list[str]) -> list[str]:
    """Resolve argv[0] to a concrete executable path.

    On Windows, npm-installed tools — the `claude` CLI among them — are `.cmd`
    shims, and CreateProcess cannot execute those: Popen fails with WinError 193.
    They have to go through cmd.exe, which re-parses the command line, so this
    refuses any argument cmd could read as a metacharacter rather than passing it
    along. Everything we send down this path is a server-side constant, so the
    refusal should never fire; if it ever does, a loud error is the right answer.
    """
    exe = shutil.which(argv[0])
    if exe is None:
        return argv            # let the caller raise its normal "not installed"
    if os.name == "nt" and exe.lower().endswith((".cmd", ".bat")):
        bad = next((a for a in argv[1:] if _CMD_META.search(a)), None)
        if bad is not None:
            raise TldrError(
                f"Refusing to run `{argv[0]}` through cmd.exe with an argument "
                f"containing shell metacharacters: {bad!r}"
            )
        return ["cmd", "/d", "/s", "/c", exe, *argv[1:]]
    return [exe, *argv[1:]]


def run(
    argv: list[str],
    *,
    stdin: str | None = None,
    timeout: float | None = None,
    cwd: "str | None" = None,
    check: bool = True,
) -> ProcResult:
    """Run argv with no shell. Untrusted data must be discrete argv items or `stdin`.

    Raises TldrError on timeout, or on non-zero exit when check=True.
    """
    if not argv or not isinstance(argv, list):
        raise ValueError("argv must be a non-empty list")
    try:
        proc = subprocess.run(  # noqa: S603 - argv list, shell=False by default
            _resolve(argv),
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise TldrTimeoutError(f"`{argv[0]}` timed out after {timeout}s.") from exc
    except FileNotFoundError as exc:
        raise TldrError(f"`{argv[0]}` is not installed or not on PATH.") from exc

    result = ProcResult(proc.returncode, proc.stdout or "", proc.stderr or "")
    if check and result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
        raise TldrError(
            f"`{argv[0]}` failed (exit {result.returncode}): " + " ".join(tail)
        )
    return result


def stream_filter(
    argv: list[str],
    feed,
    *,
    timeout: float | None = None,
    block: int = 65536,
):
    """Run argv as a long-lived byte filter and yield its stdout as it appears.

    `feed(write)` runs on a worker thread and produces stdin (write is called with
    bytes; stdin is closed when it returns). Same policy as run(): argv list,
    shell=False, no untrusted data in argv. Used where waiting for the whole output
    would add latency the caller can't afford — mp3 encoding while TTS is still
    running. `timeout` kills the process, which unblocks the read loop.
    """
    if not argv or not isinstance(argv, list):
        raise ValueError("argv must be a non-empty list")
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv list, shell=False by default
            _resolve(argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE
        )
    except FileNotFoundError as exc:
        raise TldrError(f"`{argv[0]}` is not installed or not on PATH.") from exc

    errbuf: list[bytes] = []
    feed_exc: list[BaseException] = []
    killed = threading.Event()

    def _write(data: bytes) -> None:
        # Flush every write: a buffered write would sit here until stdin closed,
        # which is exactly the latency this function exists to remove.
        proc.stdin.write(data)
        proc.stdin.flush()

    def _feed() -> None:
        try:
            feed(_write)
        except BaseException as exc:  # surfaced after the read loop drains
            feed_exc.append(exc)
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass  # already gone (killed, or the filter exited early)

    def _drain_err() -> None:
        errbuf.append(proc.stderr.read() or b"")

    def _kill() -> None:
        killed.set()
        proc.kill()

    threads = [threading.Thread(target=_feed, daemon=True),
               threading.Thread(target=_drain_err, daemon=True)]
    for t in threads:
        t.start()
    watchdog = threading.Timer(timeout, _kill) if timeout else None
    if watchdog:
        watchdog.daemon = True
        watchdog.start()
    try:
        while True:
            chunk = proc.stdout.read1(block)
            if not chunk:
                break
            yield chunk
    finally:
        if watchdog:
            watchdog.cancel()
        if proc.poll() is None:
            proc.kill()  # consumer abandoned us, or we're unwinding on an error
        threads[0].join(timeout=5)
        threads[1].join(timeout=5)
        proc.wait()
    if killed.is_set():
        raise TldrTimeoutError(f"`{argv[0]}` timed out after {timeout}s.")
    if feed_exc:
        raise feed_exc[0]
    if proc.returncode != 0:
        tail = (b"".join(errbuf)).decode("utf-8", "replace").strip().splitlines()[-3:]
        raise TldrError(f"`{argv[0]}` failed (exit {proc.returncode}): " + " ".join(tail))
