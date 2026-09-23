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

import codecs
import hashlib
import json
import os
import re
import shlex
import subprocess
import threading
import time
from contextlib import contextmanager
from typing import Callable

from . import ClaudeError, TldrError, TldrTimeoutError
from . import config, usage
from .proc import _resolve, run, stream_filter

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
        detail = envelope.get("result") or envelope.get("subtype") or "unknown error"
        raise ClaudeError(f"Claude reported an error: {detail}")
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


def _record_envelope(stdout: str, step: str) -> None:
    """Log the token counts a claude CLI envelope carries. Custom backends report
    none, so there is simply nothing to record for them."""
    if not is_claude_cli():
        return
    try:
        envelope = json.loads(stdout.strip())
    except (ValueError, AttributeError):
        return
    if isinstance(envelope, dict) and envelope.get("usage"):
        usage.record(usage.parse_usage(envelope, step))


def _explain_failure(name: str, result) -> str:
    """Turn a failed backend invocation into something a person can act on.

    The claude CLI exits non-zero *and* prints its usual JSON envelope, whose
    `result` field carries the real message ("Not logged in - Please run /login").
    Without this the user gets two kilobytes of JSON, which is how a first run ends
    in a bug report instead of a login.
    """
    for blob in (result.stdout, result.stderr):
        try:
            envelope = json.loads((blob or "").strip())
        except (ValueError, AttributeError):
            continue
        if isinstance(envelope, dict):
            msg = envelope.get("result") or envelope.get("error")
            if isinstance(msg, str) and msg.strip():
                return f"`{name}` failed: {msg.strip()}"
    tail = ((result.stderr or result.stdout) or "").strip().splitlines()[-3:]
    return f"`{name}` failed (exit {result.returncode}): " + " ".join(tail)


# The claude CLI is a coding agent. Invoked plainly, every call carries its agent
# system prompt, its tool definitions, the MCP servers from every installed plugin,
# and any CLAUDE.md files above the working directory — none of which has anything
# to do with summarizing a video, and the last of which means each user's personal
# instructions were being sent along with every transcript. Measured on an
# 18-minute video: 57,890 input tokens down to 6,376, about 5x cheaper, and the
# first token 1-2s sooner. Keep this free of & | < > ^ — on Windows an npm-installed
# CLI is a .cmd shim run through cmd.exe, and proc._resolve refuses those.
_SYSTEM_PROMPT = ("You are the text engine for tldw, a YouTube video summarizer. "
                  "Follow the task instructions in the message exactly.")
_LEAN = ["--tools", "", "--strict-mcp-config", "--setting-sources", "",
         "--system-prompt", _SYSTEM_PROMPT]

# Request value -> CLI model alias. An allowlist, because the choice arrives from the
# extension and ends up in argv.
MODELS = {"opus": "opus", "sonnet": "sonnet"}
_local = threading.local()


@contextmanager
def use_model(model: str | None):
    """Pin the model for claude calls made on this thread.

    Thread-scoped rather than threaded through every signature, the same way usage
    interactions are: the server handles each request on its own thread. None means
    the CLI's own default. Unknown names are ignored here — callers validate against
    MODELS first so a bad value is a 400, not a silent default.
    """
    prev = getattr(_local, "model", None)
    _local.model = model if model in MODELS else None
    try:
        yield
    finally:
        _local.model = prev


def current_model() -> str | None:
    return getattr(_local, "model", None)


def _claude_argv(*mode: str, persist: bool = False) -> list[str]:
    """The claude invocation every call shares.

    persist=False for one-shot calls: nothing ever resumes a summary or a segment
    selection, and without it each call leaves a transcript-sized session file in
    ~/.claude/projects. Q&A persists, because follow-ups resume that session.
    """
    argv = ["claude", "-p", *mode, *_LEAN]
    model = current_model()
    if model:
        argv += ["--model", MODELS[model]]
    if not persist:
        argv.append("--no-session-persistence")
    return argv


def _backend() -> tuple[list[str], Callable[[str], str]]:
    """(argv, extractor) for the configured backend. argv reads the prompt on stdin.

    Backend resolution: TLDW_LLM_CMD env > `tldw config` llm_cmd > the `claude` CLI.
    """
    cmd = (os.environ.get("TLDW_LLM_CMD") or config.get("llm_cmd") or "").strip()
    if cmd:
        return shlex.split(cmd), _raw_extract
    return _claude_argv("--output-format", "json"), _extract_result_text


def _parse_inner_json(text: str) -> dict:
    cleaned = _FENCE.sub("", text.strip()).strip()
    # If the model wrapped prose around the JSON, grab the outermost object.
    if not cleaned.startswith("{"):
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end == -1:
            raise ValueError("no JSON object found")
        cleaned = cleaned[start : end + 1]
    return json.loads(cleaned)


def backend_fingerprint() -> str:
    """Identity of whatever will answer: the resolved command and its flags.

    Hashed, so it can key a cache without exposing operator config. Covers the
    model too, since that rides in the same argv — a summary written by Sonnet, by
    Opus, or by somebody's local llama must never be served for the others.
    """
    argv, _ = _backend()
    return hashlib.sha256("|".join(argv).encode("utf-8")).hexdigest()[:12]


def backend_info() -> dict:
    """What the server is actually talking to, for the UI to adapt to.

    Deliberately does NOT expose the configured command: it's operator config, and
    /health is unauthenticated. Only the shape of what's available.
    """
    if is_claude_cli():
        return {"kind": "claude", "label": "Claude",
                "models": sorted(MODELS), "streaming": True}
    return {"kind": "custom", "label": "your model",
            "models": [], "streaming": True}


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


def _stream_custom(prompt: str, stdin_payload: str, *, timeout: float):
    """Yield a custom backend's stdout as it is produced.

    Decoded incrementally: a chunk boundary can land mid-character, and splitting a
    multi-byte character would corrupt the text.
    """
    argv, _ = _backend()
    payload = (prompt + "\n\n" + stdin_payload).encode("utf-8")

    def feed(write) -> None:
        write(payload)

    decoder = codecs.getincrementaldecoder("utf-8")("replace")
    for chunk in stream_filter(argv, feed, timeout=timeout):
        text = decoder.decode(chunk)
        if text:
            yield text
    tail = decoder.decode(b"", True)
    if tail:
        yield tail


def _stream_deltas(prompt: str, stdin_payload: str, *, timeout: float,
                   step: str = "", resume: str | None = None, on_meta=None,
                   persist: bool = False):
    """Yield the model's text deltas as the claude CLI produces them.

    Shared plumbing for every streaming caller: argv is a constant, the prompt and
    (untrusted) payload go on stdin, stdin is written from a thread so a large
    payload can't deadlock against unread stdout, and a deadline kills the process.
    Raises NotImplementedError for custom backends, which can't stream.
    """
    if not is_claude_cli():
        # A custom backend just prints text. Stream its stdout as it appears, so
        # everything built on streaming — the summary filling in, segments arriving
        # one by one — works for any model, not only Claude's CLI.
        yield from _stream_custom(prompt, stdin_payload, timeout=timeout)
        return

    # --verbose is REQUIRED alongside -p --output-format stream-json (the CLI refuses
    # otherwise), and --include-partial-messages is what actually yields token-level
    # deltas; without it the CLI only emits one whole `assistant` message at the end.
    argv = _claude_argv("--output-format", "stream-json", "--verbose",
                        "--include-partial-messages", persist=persist)
    if resume:
        # Continue an existing conversation: the transcript and the rules are
        # already in that session, so only the new question rides on stdin. The id
        # comes from a previous run of this same CLI, never from a request.
        argv += ["--resume", str(resume)]
    full = prompt + "\n\n" + stdin_payload

    proc = subprocess.Popen(  # noqa: S603 - resolved argv, shell=False
        _resolve(argv),
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
    killed = False
    drained = False
    try:
        for raw in proc.stdout:
            if time.monotonic() > deadline:
                proc.kill()
                raise TldrTimeoutError(f"`claude` timed out after {timeout}s.")
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "result":
                u = usage.parse_usage(event, step)
                u.resumed = bool(resume)
                usage.record(u)
                if on_meta:
                    on_meta({"session_id": u.session_id, "usage": u})
            elif event.get("type") == "system" and event.get("session_id") and on_meta:
                on_meta({"session_id": event["session_id"]})   # available early
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
        drained = True          # stdout reached EOF: the process is on its way out
    finally:
        proc.stdout.close()
        if drained:
            # Let it exit on its own. poll() can still read None while a finished
            # process is being reaped, and killing it then turned a clean exit into
            # a reported "failure".
            try:
                ret = proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                killed = True
                ret = proc.wait(timeout=5)
        else:
            # Unwinding early — a timeout, or the consumer hung up (Stop). Kill now:
            # waiting on a process that is still streaming would stall the abort.
            proc.kill()
            killed = True
            ret = proc.wait(timeout=5)

    # A process we killed on purpose isn't a failure. Don't test for a specific
    # code: POSIX reports -9 for SIGKILL, Windows reports 1 from TerminateProcess.
    if ret and not killed:
        tail = proc.stderr.read().strip().splitlines()[-3:]
        raise ClaudeError(f"`claude` failed (exit {ret}): " + " | ".join(tail))


def stream_ndjson(
    prompt: str,
    stdin_payload: str,
    *,
    on_obj: Callable[[dict], None],
    timeout: float = _DEFAULT_TIMEOUT,
    step: str = "",
) -> None:
    """Stream a reply the prompt asked for as one JSON object per line, calling
    on_obj for each object as soon as its line is complete.

    Lines that aren't a JSON object are skipped rather than fatal — the caller
    decides whether what arrived adds up to a usable answer. Raises
    NotImplementedError for custom backends, which can't stream.
    """
    def emit(line: str) -> None:
        line = line.strip()
        if not line:
            return
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            return
        if isinstance(obj, dict):
            on_obj(obj)

    buf = ""
    for text in _stream_deltas(prompt, stdin_payload, timeout=timeout, step=step):
        buf += text
        while "\n" in buf:
            line, buf = buf.split("\n", 1)
            emit(line)
    # The model doesn't always end with a newline, and the last line is usually
    # the one carrying the final fields — dropping it lost them silently.
    emit(buf)


def stream_ndjson_segments(
    prompt: str,
    stdin_payload: str,
    *,
    on_segment: Callable[[dict], None],
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict:
    """Stream segment selection: on_segment gets each {first_cue, last_cue, reason}
    as it's identified; returns the closing {chosen_ratio, rationale}.
    Raises NotImplementedError for custom backends (callers fall back to ask_json).
    """
    done: dict = {}

    def on_obj(obj: dict) -> None:
        if "first_cue" in obj:
            on_segment(obj)
        elif "chosen_ratio" in obj or "rationale" in obj:
            done.update(obj)

    stream_ndjson(prompt, stdin_payload, on_obj=on_obj, timeout=timeout,
                  step="segments")
    return done


def stream_text(
    prompt: str,
    stdin_payload: str,
    *,
    on_delta: Callable[[str], None],
    timeout: float = _DEFAULT_TIMEOUT,
    step: str = "ask",
    resume: str | None = None,
    on_meta=None,
) -> str:
    """Stream a plain-text answer, calling on_delta for each piece as it arrives.

    resume continues a prior session instead of re-sending its context; on_meta
    receives {session_id, usage} so the caller can resume this one next time.
    Returns the full text. Raises NotImplementedError for custom backends.
    """
    parts: list[str] = []
    gen = _stream_deltas(prompt, stdin_payload, timeout=timeout, step=step,
                         resume=resume, on_meta=on_meta, persist=True)
    try:
        for text in gen:
            parts.append(text)
            on_delta(text)      # may raise to abandon the answer (client hung up)
    finally:
        gen.close()             # unwinds into _stream_deltas, killing the process
    return "".join(parts)


def ask_text(
    prompt: str, stdin_payload: str, *, timeout: float = _DEFAULT_TIMEOUT,
    step: str = "ask",
) -> str:
    """Buffered plain-text answer — the fallback when the backend can't stream."""
    argv, extract = _backend()
    try:
        result = run(argv, stdin=prompt + "\n\n" + stdin_payload, timeout=timeout,
                     check=False)
    except TldrTimeoutError:
        raise                                    # surfaces as 504
    except TldrError as exc:
        raise ClaudeError(str(exc)) from exc     # not installed etc. -> 502
    if result.returncode != 0:
        raise ClaudeError(_explain_failure(argv[0], result))
    _record_envelope(result.stdout, step)
    return extract(result.stdout)


def ask_json(
    prompt: str,
    stdin_payload: str,
    *,
    validate: Callable[[dict], object],
    timeout: float = _DEFAULT_TIMEOUT,
    step: str = "summarize",
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
            result = run(argv, stdin=full, timeout=timeout, check=False)
        except TldrTimeoutError:
            raise  # surfaces as 504, not swallowed by the retry
        except TldrError as exc:
            raise ClaudeError(str(exc)) from exc  # not installed etc. -> 502
        if result.returncode != 0:
            raise ClaudeError(_explain_failure(argv[0], result))
        _record_envelope(result.stdout, step if attempt == 0 else f"{step}-retry")
        try:
            data = _parse_inner_json(extract(result.stdout))
            return validate(data)
        except (ValueError, json.JSONDecodeError, TldrError) as exc:
            last_err = exc
    raise ClaudeError(
        f"The model did not return valid structured output after a retry: {last_err}"
    )
