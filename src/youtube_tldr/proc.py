"""Single subprocess chokepoint.

Every external command (claude, yt-dlp, ffmpeg, ffprobe) runs through here so the
shell=False / argv-list / stdin / timeout policy lives in exactly one place. We
NEVER use shell=True and NEVER build command strings from untrusted input.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass

from . import TldrError


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
            argv,
            input=stdin,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd,
        )
    except subprocess.TimeoutExpired as exc:
        raise TldrError(f"`{argv[0]}` timed out after {timeout}s.") from exc
    except FileNotFoundError as exc:
        raise TldrError(f"`{argv[0]}` is not installed or not on PATH.") from exc

    result = ProcResult(proc.returncode, proc.stdout or "", proc.stderr or "")
    if check and result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()[-3:]
        raise TldrError(
            f"`{argv[0]}` failed (exit {result.returncode}): " + " ".join(tail)
        )
    return result
