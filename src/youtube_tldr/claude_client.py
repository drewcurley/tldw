"""Call the Claude Max subscription via headless `claude -p`.

The static prompt template is passed on argv; the (untrusted, possibly huge)
transcript is piped on stdin. We parse the JSON envelope, pull `.result`, strip
any markdown fences, parse the inner JSON, and validate it. Exactly one repair
retry on bad/invalid JSON, then abort.
"""

from __future__ import annotations

import json
import re
from typing import Callable

from . import TldrError
from .proc import run

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
_DEFAULT_TIMEOUT = 300.0


def _extract_result_text(stdout: str) -> str:
    """Pull the model's text out of the `claude --output-format json` envelope."""
    stdout = stdout.strip()
    if not stdout:
        raise TldrError(
            "Claude returned no output. Are you logged in? Try `claude` once "
            "interactively to confirm your Max session."
        )
    try:
        envelope = json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise TldrError("Could not parse Claude's response envelope.") from exc
    if envelope.get("is_error"):
        raise TldrError(f"Claude reported an error: {envelope.get('subtype')}")
    result = envelope.get("result")
    if not isinstance(result, str) or not result.strip():
        raise TldrError("Claude returned an empty result.")
    return result


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
    argv = ["claude", "-p", prompt, "--output-format", "json"]
    last_err: Exception | None = None
    for attempt in range(2):
        payload = stdin_payload
        if attempt == 1:
            payload = (
                stdin_payload
                + "\n\nIMPORTANT: Your previous reply was not valid JSON matching "
                "the requested schema. Reply with ONLY the raw JSON object, no "
                "prose, no markdown fences."
            )
        result = run(argv, stdin=payload, timeout=timeout)
        try:
            data = _parse_inner_json(_extract_result_text(result.stdout))
            return validate(data)
        except (ValueError, json.JSONDecodeError, TldrError) as exc:
            last_err = exc
    raise TldrError(
        f"Claude did not return valid structured output after a retry: {last_err}"
    )
