"""Call an LLM backend headlessly and get back validated JSON.

Default backend: the `claude` CLI (`claude -p --output-format json`), which uses
whatever the CLI is logged into — Claude Pro, Max, Team, or an Anthropic API key.
Override with the `TLDW_LLM_CMD` env var (or `--llm-cmd`): any command that reads the
prompt on stdin and prints the model's text on stdout (e.g. the `llm` CLI or
`ollama run <model>`), letting you use OpenAI/Gemini/local models instead.

The prompt + (untrusted) transcript are piped on stdin — never argv, never a shell.
The backend command itself is OPERATOR config (env/flag), never request-controlled.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import threading
import time
from typing import Callable

from . import ClaudeError, TldrError, TldrTimeoutError
from . import config
from .proc import run

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_DEFAULT_TIMEOUT = 300.0


def _extract_result_text(stdout: str) -> str:
    """Pull the model's text out of the `claude --output-format json` envelope."""
    stdout = stdout.strip()
    if not stdout:
        raise ClaudeError(
            "Claude returned no output. Are you logged in? Try `claude` once "
            "interactively to confirm your session."
        )
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise ClaudeError("Could not parse Claude's response envelope.") from exc
    if envelope.get("is_error"):
        raise ClaudeError(f"Claude reported an error: {envelope.get('subtype')}")
    result = envelope.get("result")
    if not isinstance(result, str) or not result.strip():
        raise ClaudeError("Claude returned an empty result.")
    return result


def _raw_extract(stdout: str) -> str:
    """A generic backend just prints the model's text; use it verbatim."""
    text = stdout.strip()
    if not text:
        raise ClaudeError("The LLM command returned no output.")
    return text


def _backend() -> tuple[list[str], Callable[[str], str]]:
    """(argv, extractor) for the configured backend. argv reads the prompt on stdin.

    Backend resolution: TLDW_LLM_CMD env > `tldw config` llm_cmd > the `claude` CLI.
    """
    cmd = (os.environ.get("TLDW_LLM_CMD") or config.get("llm_cmd") or "").strip()
    if cmd:
        return shlex.split(cmd), _raw_extract
    return ["claude", "-p", "--output-format", "json"], _extract_result_text


def _parse_inner_json(text: str) -> dict:
    cleaned = _FENCE.sub("", text.strip()).strip()
    # If the model wrapped prose around the JSON, grab the outermost object.
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("no JSON object found")
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def is_claude_cli() -> bool:
    """True when the default claude CLI backend is active (not a custom llm_cmd)."""
    argv, _ = _backend()
    return argv[0] == "claude"


def _delta_text(event: dict) -> str:
    """Text out of one stream-json line, across the shapes the CLI has used.

    Current CLIs wrap Anthropic stream events as {"type":"stream_event","event":{...}};
    older ones emitted the content_block_delta at the top level.
    """
    if event.get("type") == "stream_event":
        event = event.get("event", {})
    if event.get("type") == "content_block_delta":
        delta = event.get("delta", {})
        if delta.get("type") == "text_delta":
            return delta.get("text", "")
    return ""


def _stream_deltas(prompt: str, stdin_payload: str, *, timeout: float):
    """Yield the model's text deltas as the claude CLI produces them.

    Shared plumbing for every streaming caller: argv is a constant, the prompt and
    (untrusted) payload go on stdin, stdin is written from a thread so a large
    payload can't deadlock against unread stdout, and a deadline kills the process.
    Raises NotImplementedError for custom backends, which can't stream.
    """
    if not is_claude_cli():
        raise NotImplementedError  # caller must fall back to a buffered call

    # --verbose is REQUIRED alongside -p --output-format stream-json (the CLI refuses
    # otherwise), and --include-partial-messages is what actually yields token-level
    # deltas; without it the CLI only emits one whole `assistant` message at the end.
    argv = ["claude", "-p", "--output-format", "stream-json", "--verbose",
            "--include-partial-messages"]
    full = prompt + "\n\n" + stdin_payload

    proc = subprocess.Popen(  # noqa: S603
        argv,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    # Write stdin in a thread — large payloads can block if not drained concurrently
    def _write():
        try:
            proc.stdin.write(full)
        except OSError:
            pass          # process already gone (killed, or exited early)
        finally:
            try:
                proc.stdin.close()
            except OSError:
                pass
    threading.Thread(target=_write, daemon=True).start()

    deadline = time.monotonic() + timeout
    saw_delta = False
    try:
        for raw in proc.stdout:
            if time.monotonic() > deadline:
                proc.kill()
                raise TldrTimeoutError(f"`claude` timed out after {timeout}s.")
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            text = _delta_text(event)
            if text:
                saw_delta = True
                yield text
                continue
            # Fallback for a CLI that doesn't do partials: the complete message
            # arrives as one `assistant` event. Only use it if nothing streamed,
            # otherwise it would duplicate every delta we already yielded.
            if not saw_delta and event.get("type") == "assistant":
                whole = "".join(
                    block.get("text", "")
                    for block in event.get("message", {}).get("content", [])
                    if isinstance(block, dict) and block.get("type") == "text")
                if whole:
                    yield whole
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()   # consumer abandoned us (client hung up) or we're unwinding
        ret = proc.wait(timeout=5)

    if ret and ret not in (0, -9):
        tail = proc.stderr.read().strip().splitlines()[-3:]
        raise ClaudeError(f"`claude` failed (exit {ret}): " + " | ".join(tail))


def stream_ndjson_segments(
    prompt: str,
    stdin_payload: str,
    *,
    on_segment: Callable[[dict], None],
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict:
    """Stream segment selection from the claude CLI using --output-format stream-json.

    The prompt must ask Claude to emit one segment JSON per line as it identifies
    each one, then a final {chosen_ratio, rationale} line.  on_segment is called
    for every {first_cue, last_cue, reason} line received in real time.

    Returns the final summary dict {chosen_ratio, rationale}.
    Raises TldrTimeoutError / ClaudeError on failure.
    Falls back to ask_json (buffered, no streaming) for custom LLM backends.
    """
    text_buf = ""
    done_data: dict = {}
    for text in _stream_deltas(prompt, stdin_payload, timeout=timeout):
        text_buf += text
        # Flush complete NDJSON lines from the accumulated buffer
        while "\n" in text_buf:
            line, text_buf = text_buf.split("\n", 1)
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "first_cue" in obj:
                on_segment(obj)
            elif "chosen_ratio" in obj or "rationale" in obj:
                done_data = obj
    return done_data


def stream_text(
    prompt: str,
    stdin_payload: str,
    *,
    on_delta: Callable[[str], None],
    timeout: float = _DEFAULT_TIMEOUT,
) -> str:
    """Stream a plain-text answer, calling on_delta for each piece as it arrives.

    Returns the full text. Raises NotImplementedError for custom backends.
    """
    parts: list[str] = []
    gen = _stream_deltas(prompt, stdin_payload, timeout=timeout)
    try:
        for text in gen:
            parts.append(text)
            on_delta(text)      # may raise to abandon the answer (client hung up)
    finally:
        gen.close()             # unwinds into _stream_deltas, killing the process
    return "".join(parts)


def ask_text(
    prompt: str, stdin_payload: str, *, timeout: float = _DEFAULT_TIMEOUT
) -> str:
    """Buffered plain-text answer — the fallback when the backend can't stream."""
    argv, extract = _backend()
    try:
        result = run(argv, stdin=prompt + "\n\n" + stdin_payload, timeout=timeout)
    except TldrTimeoutError:
        raise                                    # surfaces as 504
    except TldrError as exc:
        raise ClaudeError(str(exc)) from exc     # non-zero exit etc. -> 502
    return extract(result.stdout)


def ask_json(
    prompt: str,
    stdin_payload: str,
    *,
    validate: Callable[[dict], object],
    timeout: float = _DEFAULT_TIMEOUT,
) -> object:
    """Run claude, parse+validate JSON. One repair retry, then TldrError.

    `validate` receives the parsed dict and returns the caller's object (or raises
    ValueError/TldrError to trigger the single retry).
    """
    argv, extract = _backend()
    last_err: Exception | None = None
    for attempt in range(2):
        full = prompt + "\n\n" + stdin_payload
        if attempt == 1:
            full += (
                "\n\nIMPORTANT: Your previous reply was not valid JSON matching the "
                "requested schema. Reply with ONLY the raw JSON object, no prose, no "
                "markdown fences."
            )
        try:
            result = run(argv, stdin=full, timeout=timeout)
        except TldrTimeoutError:
            raise  # surfaces as 504, not swallowed by the retry
        except TldrError as exc:
            raise ClaudeError(str(exc)) from exc  # non-zero exit etc. -> 502
        try:
            data = _parse_inner_json(extract(result.stdout))
            return validate(data)
        except (ValueError, json.JSONDecodeError, TldrError) as exc:
            last_err = exc
    raise ClaudeError(
        f"The model did not return valid structured output after a retry: {last_err}"
    )
