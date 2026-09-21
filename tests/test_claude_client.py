import json
import re
import threading

import pytest

from youtube_tldw import ClaudeError, TldrError, claude_client
from youtube_tldw import claude_client as cc
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


def test_default_backend_is_claude_envelope(monkeypatch):
    monkeypatch.delenv("TLDW_LLM_CMD", raising=False)
    monkeypatch.setattr(claude_client.config, "get", lambda *a, **k: None)
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        captured["stdin"] = kw.get("stdin")
        return _envelope('{"ok": 2}')

    monkeypatch.setattr(claude_client, "run", fake_run)
    out = claude_client.ask_json("PROMPT", "DATA", validate=lambda d: d)
    assert out == {"ok": 2}
    assert captured["argv"][0] == "claude" and "--output-format" in captured["argv"]
    assert "PROMPT" in captured["stdin"] and "DATA" in captured["stdin"]  # both on stdin


def test_config_backend_used_when_no_env(monkeypatch):
    monkeypatch.delenv("TLDW_LLM_CMD", raising=False)
    monkeypatch.setattr(claude_client.config, "get",
                        lambda k, d=None: "mycli --json" if k == "llm_cmd" else d)
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        return ProcResult(0, '{"ok": 9}', "")

    monkeypatch.setattr(claude_client, "run", fake_run)
    out = claude_client.ask_json("P", "D", validate=lambda d: d)
    assert out == {"ok": 9}
    assert captured["argv"] == ["mycli", "--json"]   # config backend, raw stdout


def test_custom_backend_raw_stdout(monkeypatch):
    monkeypatch.setenv("TLDW_LLM_CMD", "mymodel --json")
    captured = {}

    def fake_run(argv, **kw):
        captured["argv"] = argv
        captured["stdin"] = kw.get("stdin")
        return ProcResult(0, '{"ok": 5}', "")  # raw model text, no envelope

    monkeypatch.setattr(claude_client, "run", fake_run)
    out = claude_client.ask_json("P", "D", validate=lambda d: d)
    assert out == {"ok": 5}
    assert captured["argv"] == ["mymodel", "--json"]   # shlex-split operator command
    assert "P" in captured["stdin"] and "D" in captured["stdin"]


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


class _Res:
    def __init__(self, code, out="", err=""):
        self.returncode, self.stdout, self.stderr = code, out, err


def test_failure_surfaces_the_envelope_message():
    """A non-zero exit still prints the usual JSON envelope; the `result` field is
    the actionable part. Without this the user sees 2KB of JSON."""
    envelope = json.dumps({
        "is_error": True, "subtype": "success", "type": "result",
        "result": "Not logged in · Please run /login",
        "usage": {"input_tokens": 0}, "session_id": "abc",
    })
    msg = claude_client._explain_failure("claude", _Res(1, envelope))
    assert "Not logged in" in msg and "Please run /login" in msg
    assert "session_id" not in msg           # the blob itself stays out of it


def test_failure_falls_back_to_the_stderr_tail():
    msg = claude_client._explain_failure("claude", _Res(127, "", "boom\nsplat"))
    assert "exit 127" in msg and "splat" in msg


def test_is_error_envelope_reports_the_message_not_the_subtype():
    """subtype is often "success" even when is_error is true — useless on its own."""
    envelope = json.dumps({"is_error": True, "subtype": "success",
                           "result": "Credit balance too low"})
    with pytest.raises(ClaudeError, match="Credit balance too low"):
        claude_client._extract_result_text(envelope)


# --- lean invocation -----------------------------------------------------------

def _claude_backend(monkeypatch):
    monkeypatch.delenv("TLDW_LLM_CMD", raising=False)
    monkeypatch.setattr(cc.config, "get", lambda *a, **k: None)


def test_every_claude_call_strips_the_coding_agent_context(monkeypatch):
    """No tools, no plugin MCP servers, no settings/CLAUDE.md, our own system
    prompt — the default context was 89% of every request's input tokens, and it
    carried the user's personal instructions along with each transcript."""
    _claude_backend(monkeypatch)
    argv, _ = cc._backend()
    assert argv[:2] == ["claude", "-p"]
    i = argv.index("--tools")
    assert argv[i + 1] == ""
    assert "--strict-mcp-config" in argv
    j = argv.index("--setting-sources")
    assert argv[j + 1] == ""
    k = argv.index("--system-prompt")
    assert argv[k + 1] == cc._SYSTEM_PROMPT


def test_one_shot_calls_leave_no_session_file(monkeypatch):
    _claude_backend(monkeypatch)
    argv, _ = cc._backend()
    assert "--no-session-persistence" in argv


def test_qa_calls_persist_so_follow_ups_can_resume(monkeypatch):
    _claude_backend(monkeypatch)
    assert "--no-session-persistence" not in cc._claude_argv("-x", persist=True)


def test_system_prompt_survives_the_windows_cmd_shim_guard():
    """proc._resolve refuses cmd.exe metacharacters for .cmd shims (npm installs)."""
    assert not re.search(r"[&|<>^]", cc._SYSTEM_PROMPT)


def test_custom_backends_are_left_alone(monkeypatch):
    """The lean flags are claude-CLI specific; an operator's own command is
    passed exactly as configured."""
    monkeypatch.setenv("TLDW_LLM_CMD", "ollama run llama3")
    argv, _ = cc._backend()
    assert argv == ["ollama", "run", "llama3"]


# --- model choice ----------------------------------------------------------------

def test_no_model_means_the_cli_default(monkeypatch):
    _claude_backend(monkeypatch)
    assert "--model" not in cc._backend()[0]


def test_use_model_pins_the_model_for_this_thread(monkeypatch):
    _claude_backend(monkeypatch)
    with cc.use_model("sonnet"):
        argv, _ = cc._backend()
        assert argv[argv.index("--model") + 1] == "sonnet"
    assert "--model" not in cc._backend()[0]          # restored on exit


def test_use_model_ignores_names_outside_the_allowlist(monkeypatch):
    """Belt and braces: the server validates first, but nothing unlisted reaches
    argv even if a caller forgets."""
    _claude_backend(monkeypatch)
    with cc.use_model("opus; rm -rf /"):
        assert "--model" not in cc._backend()[0]


def test_model_scope_is_per_thread(monkeypatch):
    _claude_backend(monkeypatch)
    seen = {}

    def other():
        seen["argv"] = cc._backend()[0]

    with cc.use_model("sonnet"):
        t = threading.Thread(target=other)
        t.start(); t.join()
    assert "--model" not in seen["argv"]              # another request's thread


# --- NDJSON streaming -------------------------------------------------------------

def _fake_deltas(monkeypatch, pieces):
    monkeypatch.setattr(cc, "_stream_deltas", lambda *a, **k: iter(pieces))


def test_stream_ndjson_reassembles_lines_split_across_deltas(monkeypatch):
    _fake_deltas(monkeypatch, ['{"a"', ': 1}\n{"b": ', '2}\n'])
    got = []
    cc.stream_ndjson("p", "x", on_obj=got.append)
    assert got == [{"a": 1}, {"b": 2}]


def test_stream_ndjson_keeps_a_final_line_without_a_newline(monkeypatch):
    """The closing line usually carries the final fields; it used to be dropped."""
    _fake_deltas(monkeypatch, ['{"a": 1}\n{"chosen_ratio": 0.3}'])
    got = []
    cc.stream_ndjson("p", "x", on_obj=got.append)
    assert got[-1] == {"chosen_ratio": 0.3}


def test_stream_ndjson_skips_junk_lines(monkeypatch):
    _fake_deltas(monkeypatch, ['Sure! Here you go:\n', '```json\n', '{"a": 1}\n',
                               '[1, 2]\n', '```'])
    got = []
    cc.stream_ndjson("p", "x", on_obj=got.append)
    assert got == [{"a": 1}]                    # prose, fences, non-objects ignored


def test_segments_keep_their_closing_line_without_a_newline(monkeypatch):
    _fake_deltas(monkeypatch, ['{"first_cue": 0, "last_cue": 2}\n',
                               '{"chosen_ratio": 0.2, "rationale": "tight"}'])
    segs = []
    done = cc.stream_ndjson_segments("p", "x", on_segment=segs.append)
    assert segs == [{"first_cue": 0, "last_cue": 2}]
    assert done == {"chosen_ratio": 0.2, "rationale": "tight"}


def test_extension_model_lists_match_the_server_allowlist():
    """Three copies of one list: add a model to one and forget another, and the
    server refuses every request that uses it."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "extension"
    for name in ("background.js", "options.js"):
        src = (root / name).read_text()
        m = re.search(r"const MODELS = \[([^\]]*)\]", src)
        assert m, f"no MODELS list in {name}"
        listed = re.findall(r'"([^"]+)"', m.group(1))
        assert sorted(listed) == sorted(cc.MODELS), name
