"""Token and cost accounting for every model call.

The `claude` CLI already reports token counts and a cost per invocation; this
captures them instead of discarding them, so "what does a video actually cost" is
a question with an answer. One JSONL row per model call, appended to
~/.config/youtube-tldw/usage.jsonl, plus a line on the server log as it happens.

Calls are grouped into an *interaction* (one summarize, one segment selection, one
question) so a row can be attributed both to a step and to the thing the user
asked for. Grouping is thread-local, which is what the threaded server needs.

Nothing here is on the critical path: an accounting failure must never break the
request it was measuring.
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

USAGE_FILE = Path.home() / ".config" / "youtube-tldw" / "usage.jsonl"
_lock = threading.Lock()
_local = threading.local()


@dataclass
class Usage:
    """One model call, as the backend reported it."""
    step: str = ""
    model: str = ""
    input_tokens: int = 0
    cache_creation_tokens: int = 0
    cache_read_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    session_id: str = ""
    resumed: bool = False

    @property
    def billed_input(self) -> int:
        """Everything the model had to be handed, cached or not."""
        return self.input_tokens + self.cache_creation_tokens + self.cache_read_tokens

    @property
    def total_tokens(self) -> int:
        return self.billed_input + self.output_tokens


@dataclass
class Interaction:
    kind: str                      # summarize | segments | ask | video
    video_id: str = ""
    started: float = field(default_factory=time.monotonic)
    calls: list = field(default_factory=list)

    def totals(self) -> Usage:
        t = Usage(step="total")
        for c in self.calls:
            t.input_tokens += c.input_tokens
            t.cache_creation_tokens += c.cache_creation_tokens
            t.cache_read_tokens += c.cache_read_tokens
            t.output_tokens += c.output_tokens
            t.cost_usd += c.cost_usd
            t.duration_ms += c.duration_ms
        return t


def parse_usage(envelope: dict, step: str = "") -> Usage:
    """Pull the usage numbers out of a claude CLI result envelope/event."""
    u = envelope.get("usage") or {}
    model = ""
    msg = envelope.get("message")
    if isinstance(msg, dict):
        model = msg.get("model") or ""
    return Usage(
        step=step,
        model=model or envelope.get("model") or "",
        input_tokens=int(u.get("input_tokens") or 0),
        cache_creation_tokens=int(u.get("cache_creation_input_tokens") or 0),
        cache_read_tokens=int(u.get("cache_read_input_tokens") or 0),
        output_tokens=int(u.get("output_tokens") or 0),
        cost_usd=float(envelope.get("total_cost_usd") or 0.0),
        duration_ms=int(envelope.get("duration_ms") or 0),
        session_id=envelope.get("session_id") or "",
    )


class _Scope:
    """Context-manager form; safe to nest (an inner scope is a no-op)."""

    def __init__(self, kind: str, video_id: str):
        self.kind, self.video_id, self.owner = kind, video_id, False

    def __enter__(self) -> "_Scope":
        if getattr(_local, "interaction", None) is None:
            _local.interaction = Interaction(self.kind, self.video_id)
            self.owner = True
        return self

    def __exit__(self, *exc) -> bool:
        if self.owner:
            finish()
        return False


def interaction(kind: str, video_id: str = "") -> _Scope:
    return _Scope(kind, video_id)


def current():
    return getattr(_local, "interaction", None)


def record(usage: Usage, *, on_log=None) -> None:
    """Attribute one model call to the interaction in progress."""
    try:
        inter = current()
        if inter is not None:
            inter.calls.append(usage)
        line = (f"{usage.step}: {usage.billed_input:,} in "
                f"({usage.cache_read_tokens:,} cached) + {usage.output_tokens:,} out"
                + (f" = ${usage.cost_usd:.4f}" if usage.cost_usd else "")
                + (" [resumed]" if usage.resumed else ""))
        if on_log:
            on_log(line)
        else:
            print(f"  {line}", flush=True)
        row = {"ts": time.time(),
               "kind": inter.kind if inter else "",
               "video_id": inter.video_id if inter else ""}
        row.update(asdict(usage))
        _append(row)
    except Exception as exc:                      # never break the real request
        print(f"  (usage accounting failed: {exc!r})", flush=True)


def finish():
    inter = current()
    _local.interaction = None
    if inter is None or not inter.calls:
        return inter
    t = inter.totals()
    print(f"  usage [{inter.kind}] {len(inter.calls)} call(s): "
          f"{t.billed_input:,} in + {t.output_tokens:,} out"
          + (f" = ${t.cost_usd:.4f}" if t.cost_usd else ""), flush=True)
    return inter


def _append(row: dict) -> None:
    USAGE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        with USAGE_FILE.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        try:
            os.chmod(USAGE_FILE, 0o600)
        except OSError:
            pass


def read_rows(path=None) -> list:
    """Every recorded call, oldest first. Bad lines are skipped, not fatal."""
    target = path or USAGE_FILE
    rows = []
    try:
        with target.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except FileNotFoundError:
        return []
    return rows
