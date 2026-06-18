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
