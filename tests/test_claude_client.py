import json

import pytest

from youtube_tldw import TldrError, claude_client
from youtube_tldw.proc import ProcResult


def _envelope(result_text: str, is_error=False):
    return ProcResult(
        0, json.dumps({"type": "result", "is_error": is_error, "result": result_text}), ""
    )


def _patch_run(monkeypatch, responses):
    calls = {"n": 0}

    def fake_run(argv, **kw):
        r = responses[min(calls["n"], len(responses) - 1)]
        calls["n"] += 1
        return r

    monkeypatch.setattr(claude_client, "run", fake_run)
    return calls


def test_parses_plain_json(monkeypatch):
    _patch_run(monkeypatch, [_envelope('{"ok": true}')])
    out = claude_client.ask_json("p", "data", validate=lambda d: d)
    assert out == {"ok": True}


def test_strips_markdown_fence(monkeypatch):
    _patch_run(monkeypatch, [_envelope('```json\n{"ok": 1}\n```')])
    out = claude_client.ask_json("p", "data", validate=lambda d: d)
    assert out == {"ok": 1}


def test_extracts_json_from_prose(monkeypatch):
    _patch_run(monkeypatch, [_envelope('Here you go:\n{"a": 2}\nThanks!')])
    out = claude_client.ask_json("p", "data", validate=lambda d: d)
    assert out == {"a": 2}


def test_repair_retry_succeeds(monkeypatch):
    calls = _patch_run(
        monkeypatch, [_envelope("not json at all"), _envelope('{"fixed": 1}')]
    )
    out = claude_client.ask_json("p", "data", validate=lambda d: d)
    assert out == {"fixed": 1}
    assert calls["n"] == 2


def test_gives_up_after_retry(monkeypatch):
    _patch_run(monkeypatch, [_envelope("nope"), _envelope("still nope")])
    with pytest.raises(TldrError):
        claude_client.ask_json("p", "data", validate=lambda d: d)


def test_empty_result_raises(monkeypatch):
    _patch_run(monkeypatch, [_envelope("   ")])
    with pytest.raises(TldrError):
        claude_client.ask_json("p", "data", validate=lambda d: d)


def test_validator_rejection_triggers_retry(monkeypatch):
    def validate(d):
        if "good" not in d:
            raise ValueError("bad")
        return d

    calls = _patch_run(
        monkeypatch, [_envelope('{"bad": 1}'), _envelope('{"good": 1}')]
    )
    out = claude_client.ask_json("p", "data", validate=validate)
    assert out == {"good": 1} and calls["n"] == 2
