"""Output path construction with sanitization and path-containment guarantees.

Filename template: '{channel} - {video} - tl;dw - {length}.{ext}'
The ' - ' joiner and 'tl;dw' literal are structural, so we strip ' - ' runs and
';' from the channel/title fields to keep the template parseable.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from . import TldrError

# Strip path separators, control chars, and characters illegal on common FS.
_ILLEGAL = re.compile(r'[\x00-\x1f<>:"/\\|?*]')
_WS = re.compile(r"\s+")
# Names reserved on Windows (defensive; harmless on macOS/Linux).
_RESERVED = {
    "con", "prn", "aux", "nul",
    *(f"com{i}" for i in range(1, 10)),
    *(f"lpt{i}" for i in range(1, 10)),
}


def sanitize_field(value: str, *, max_bytes: int = 80) -> str:
    """Make an arbitrary channel/title safe to embed in the filename template."""
    if not value:
        return "untitled"
    # Drop emoji / non-printable symbol categories; keep letters/numbers/punct.
    cleaned = "".join(
        ch for ch in value if not unicodedata.category(ch).startswith(("C", "So"))
    )
    cleaned = _ILLEGAL.sub(" ", cleaned)
    cleaned = cleaned.replace(";", " ")          # protect the 'tl;dw' literal
    cleaned = cleaned.replace(" - ", " ")        # protect the ' - ' joiner
    cleaned = _WS.sub(" ", cleaned).strip(" .")  # no leading/trailing dot/space
    cleaned = _truncate_bytes(cleaned, max_bytes).strip(" .")
    if not cleaned or cleaned.lower() in _RESERVED:
        return "untitled"
    return cleaned


def _truncate_bytes(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", "ignore")


def build_filename(channel: str, title: str, length_label: str, ext: str) -> str:
    channel_s = sanitize_field(channel, max_bytes=60)
    title_s = sanitize_field(title, max_bytes=120)
    return f"{channel_s} - {title_s} - tl;dw - {length_label}.{ext}"


def resolve_output_path(
    base_dir: Path, mode: str, filename: str
) -> Path:
    """Place `filename` under base_dir/<text|video|audio>/ and assert it stays inside.

    Guards against any traversal that survived sanitization.
    """
    subdir = {"video": "video", "audio": "audio"}.get(mode, "text")
    out_dir = (base_dir / subdir).resolve()
    candidate = (out_dir / filename).resolve()
    if not candidate.is_relative_to(out_dir):
        raise TldrError("Refusing to write outside the output directory.")
    out_dir.mkdir(parents=True, exist_ok=True)
    return candidate


def avoid_overwrite(path: Path) -> Path:
    """If path exists, append ' (2)', ' (3)', ... before the extension."""
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    n = 2
    while True:
        candidate = path.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1
