import json
import re
import sys
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


# --- other backends -------------------------------------------------------------

def test_backend_info_describes_the_claude_cli(monkeypatch):
    _claude_backend(monkeypatch)
    info = cc.backend_info()
    assert info["kind"] == "claude" and info["models"] == sorted(cc.MODELS)
    assert info["streaming"] is True


def test_backend_info_never_leaks_the_configured_command(monkeypatch):
    """/health is unauthenticated; the command is operator config."""
    monkeypatch.setenv("TLDW_LLM_CMD", "llm -m gpt-4o --key sk-secret")
    info = cc.backend_info()
    assert info["kind"] == "custom" and info["models"] == []
    assert "sk-secret" not in json.dumps(info) and "llm" not in json.dumps(info)


def test_a_custom_backend_streams_its_stdout(monkeypatch):
    """Everything built on streaming works for any model, not only Claude's CLI."""
    monkeypatch.setenv("TLDW_LLM_CMD",
                       f'{sys.executable} -c "import sys;sys.stdout.write(sys.stdin.read())"')
    out = list(cc._stream_deltas("PROMPT", "PAYLOAD", timeout=30))
    assert "PAYLOAD" in "".join(out)


def test_a_multibyte_character_split_across_chunks_survives(monkeypatch):
    """A chunk boundary can land mid-character; decoding each chunk alone would
    corrupt the text."""
    script = ("import sys,time\n"
              "b='caf\\u00e9 \\u2014 r\\u00e9sum\\u00e9'.encode()\n"
              "for i in range(0,len(b),3):\n"
              "    sys.stdout.buffer.write(b[i:i+3]); sys.stdout.buffer.flush()\n")
    monkeypatch.setenv("TLDW_LLM_CMD", f'{sys.executable} -c "{script}"')
    assert "".join(cc._stream_deltas("p", "x", timeout=30)) == "café — résumé"


# --- the stock claude process: environment, directory, and a watchdog ------------
#
# These drive the real _stream_deltas against a real child process standing in
# for the CLI. Every earlier test of this path replaced it with a stub, so the
# code that actually talks to `claude` -- and the hang the audit found in it --
# had no coverage at all.

import os
import time
from pathlib import Path

from youtube_tldw import TldrTimeoutError

RESULT = json.dumps({"type": "result", "result": "", "usage": {}, "session_id": "s"})
DELTA = json.dumps({"type": "stream_event", "event": {
    "type": "content_block_delta", "delta": {"type": "text_delta", "text": "hello"}}})


def _fake_cli(monkeypatch, script: str):
    """Make _stream_deltas launch `python -c script` as though it were claude."""
    monkeypatch.setattr(cc, "is_claude_cli", lambda: True)
    monkeypatch.setattr(cc, "_claude_argv",
                        lambda *a, **k: [sys.executable, "-c", script])


def _capture_popen(monkeypatch):
    seen = {}
    real = cc.subprocess.Popen

    def spy(*a, **kw):
        seen.update(kw)
        return real(*a, **kw)

    monkeypatch.setattr(cc.subprocess, "Popen", spy)
    return seen


def test_one_shot_streams_run_with_prompt_caching_off(monkeypatch):
    """Nothing resumes a summary or a segment selection, so the cache they write
    is never read -- and the CLI bills that write at twice the input rate."""
    _fake_cli(monkeypatch, f"print({RESULT!r})")
    seen = _capture_popen(monkeypatch)
    list(cc._stream_deltas("p", "x", timeout=10, persist=False))
    assert seen["env"]["DISABLE_PROMPT_CACHING"] == "1"


def test_qa_keeps_caching_even_if_the_shell_turned_it_off(monkeypatch):
    """Follow-up questions resume the session and do read its cache."""
    monkeypatch.setenv("DISABLE_PROMPT_CACHING", "1")
    _fake_cli(monkeypatch, f"print({RESULT!r})")
    seen = _capture_popen(monkeypatch)
    list(cc._stream_deltas("p", "x", timeout=10, persist=True))
    assert "DISABLE_PROMPT_CACHING" not in seen["env"]


def test_the_cache_setting_never_leaks_into_the_server_process(monkeypatch):
    """Per call, never os.environ: Q&A runs alongside summaries on other threads."""
    _fake_cli(monkeypatch, f"print({RESULT!r})")
    list(cc._stream_deltas("p", "x", timeout=10, persist=False))
    assert "DISABLE_PROMPT_CACHING" not in os.environ


def test_claude_runs_in_its_own_directory(monkeypatch):
    """Not wherever `tldw serve` was started, where Q&A sessions used to land
    among the user's own Claude Code sessions."""
    _fake_cli(monkeypatch, "import os; print(os.getcwd())")
    seen = _capture_popen(monkeypatch)
    list(cc._stream_deltas("p", "x", timeout=10))
    assert Path(seen["cwd"]) == cc.CLAUDE_DIR
    assert cc.CLAUDE_DIR.is_dir()
    assert (cc.CLAUDE_DIR.stat().st_mode & 0o777) == 0o700


def test_the_directory_is_recreated_if_something_cleaned_it(monkeypatch):
    import shutil
    _fake_cli(monkeypatch, f"print({RESULT!r})")
    list(cc._stream_deltas("p", "x", timeout=10))
    shutil.rmtree(cc.CLAUDE_DIR)
    list(cc._stream_deltas("p", "x", timeout=10))       # must not fail
    assert cc.CLAUDE_DIR.is_dir()


def test_buffered_calls_get_the_same_treatment(monkeypatch):
    seen = {}
    monkeypatch.setattr(cc, "run", lambda argv, **kw: (seen.update(kw), ProcResult(
        0, json.dumps({"type": "result", "result": '{"a": 1}'}), ""))[1])
    _claude_backend(monkeypatch)
    cc.ask_json("p", "x", validate=lambda d: d, timeout=5)
    assert seen["env"]["DISABLE_PROMPT_CACHING"] == "1"
    assert Path(seen["cwd"]) == cc.CLAUDE_DIR


def test_custom_backends_keep_their_own_directory_and_environment():
    """A relative `./model.sh` resolves against the working directory, so moving
    it would break the backend; and its caching is its own business."""
    assert cc._launch_opts(["./model.sh", "--x"]) == {}
    assert cc._launch_opts(["llm", "-m", "gpt"]) == {}


# --- the watchdog -----------------------------------------------------------------

def test_a_silent_claude_is_killed_at_its_deadline(monkeypatch, tmp_path):
    """(a) The audit's one High: the deadline was only checked when a line
    arrived, so a process that went quiet held a request and a slot forever."""
    pid_file = tmp_path / "pid"
    _fake_cli(monkeypatch,
              f"import os, time; open({str(pid_file)!r}, 'w').write(str(os.getpid())); "
              "time.sleep(20)")
    t0 = time.monotonic()
    with pytest.raises(TldrTimeoutError):
        list(cc._stream_deltas("p", "x", timeout=0.5))
    assert time.monotonic() - t0 < 5
    pid = int(pid_file.read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)                           # really gone, not orphaned


def test_a_flood_of_stderr_cannot_wedge_the_stream(monkeypatch):
    """(b) stderr used to be read only on failure. A CLI writing more than a
    pipe's worth of it would block forever."""
    _fake_cli(monkeypatch,
              "import sys; sys.stderr.write('x' * 1_000_000); sys.stderr.flush(); "
              f"print({DELTA!r}); print({RESULT!r})")
    assert "".join(cc._stream_deltas("p", "x", timeout=10)) == "hello"


def test_a_cli_that_lingers_after_answering_still_succeeds(monkeypatch):
    """(c) A complete answer is a success. The loop stops at the result rather
    than waiting for EOF, and our own kill of the lingering process is not an
    error."""
    monkeypatch.setattr(cc, "_EXIT_GRACE", 0.2)
    _fake_cli(monkeypatch,
              f"import sys, time; print({DELTA!r}); print({RESULT!r}); "
              "sys.stdout.flush(); time.sleep(60)")
    t0 = time.monotonic()
    assert "".join(cc._stream_deltas("p", "x", timeout=30)) == "hello"
    assert time.monotonic() - t0 < 5             # nowhere near the 30s deadline


def test_the_watchdog_firing_after_eof_does_not_kill_or_fail(monkeypatch):
    """(d) The race fixed in bc22a4f, made deterministic: the timer fires after
    the stream has drained. It must neither kill a process exiting 0 nor turn a
    clean run into an error."""
    fired = []

    class HandTimer:
        def __init__(self, _t, fn):
            self.fn, self.daemon = fn, False

        def start(self):
            fired.append(self)

        def cancel(self):
            pass

    monkeypatch.setattr(cc, "_Timer", HandTimer)
    _fake_cli(monkeypatch, f"print({DELTA!r}); print({RESULT!r})")
    procs = []
    real = cc.subprocess.Popen
    monkeypatch.setattr(cc.subprocess, "Popen",
                        lambda *a, **k: procs.append(real(*a, **k)) or procs[-1])
    assert "".join(cc._stream_deltas("p", "x", timeout=30)) == "hello"
    fired[0].fn()                                # the deadline, arriving too late
    assert procs[0].returncode == 0              # exited cleanly, was not killed


def test_a_failing_cli_reports_its_stderr(monkeypatch):
    """Draining stderr on a thread must not lose the message a failure needs."""
    _fake_cli(monkeypatch,
              "import sys; sys.stderr.write('auth expired\\n'); sys.exit(3)")
    with pytest.raises(ClaudeError, match="auth expired"):
        list(cc._stream_deltas("p", "x", timeout=10))


def test_a_timed_out_resume_is_not_retried_from_scratch(monkeypatch):
    """A resumed call that ran out of time would only run out of time again,
    doubling the wait."""
    from youtube_tldw import ask
    calls = []

    def stream(*a, resume=None, **k):
        calls.append(resume)
        raise TldrTimeoutError("slow")

    monkeypatch.setattr(ask, "stream_text", stream)
    meta = type("M", (), {"title": "T", "channel": "C"})()
    with pytest.raises(TldrTimeoutError):
        ask.stream_answer(meta, [], [], "q", session_id="old-session")
    assert calls == ["old-session"]


def test_a_complete_answer_beats_a_deadline_that_fires_mid_stream(monkeypatch):
    """The precedence rule: once the whole answer has been written, the deadline
    firing -- and our kill -- must not turn it into a timeout."""
    fired = []

    class HandTimer:
        def __init__(self, _t, fn):
            self.fn, self.daemon = fn, False

        def start(self):
            fired.append(self)

        def cancel(self):
            pass

    monkeypatch.setattr(cc, "_Timer", HandTimer)
    monkeypatch.setattr(cc, "_EXIT_GRACE", 0.2)
    # Both lines are written and flushed before the child sleeps, so the answer is
    # already in the pipe when the deadline fires.
    _fake_cli(monkeypatch, f"import sys, time; print({DELTA!r}); print({RESULT!r}); "
                           "sys.stdout.flush(); time.sleep(20)")
    gen = cc._stream_deltas("p", "x", timeout=30)
    assert next(gen) == "hello"
    fired[0].fn()                                # deadline fires now, mid-stream
    assert list(gen) == []                       # finishes cleanly, no timeout


# --- an error the CLI reports is an error, not an answer ---------------------------
#
# The CLI reports failures -- not logged in, usage limit, API overloaded part-way
# through -- as a `result` event with is_error. Treating every result as success
# cached half-written summaries and served "Not logged in" as a Q&A reply.

ERR = json.dumps({"type": "result", "is_error": True, "subtype": "error",
                  "result": "API Error: overloaded", "usage": {}})


@pytest.mark.parametrize("exit_code", [1, 0])
def test_an_error_result_raises_even_after_partial_output(monkeypatch, exit_code):
    _fake_cli(monkeypatch, f"import sys; print({DELTA!r}); print({ERR!r}); "
                           f"sys.exit({exit_code})")
    gen = cc._stream_deltas("p", "x", timeout=10)
    assert next(gen) == "hello"                    # the partial text arrived...
    with pytest.raises(ClaudeError, match="API Error: overloaded"):
        list(gen)                                  # ...but it is not a success


def test_a_not_logged_in_reply_is_never_returned_as_an_answer(monkeypatch):
    err = json.dumps({"type": "result", "is_error": True,
                      "result": "Not logged in · Please run /login", "usage": {}})
    _fake_cli(monkeypatch, f"import sys; print({err!r}); sys.exit(1)")
    with pytest.raises(ClaudeError, match="Not logged in"):
        cc.stream_text("p", "x", on_delta=lambda _t: None, timeout=10)


def test_a_partial_summary_is_never_mistaken_for_a_complete_one(monkeypatch):
    """The streamed summary path, end to end through the real parser."""
    from youtube_tldw import summarize
    from youtube_tldw.transcript import Cue
    line = lambda o: json.dumps({"type": "stream_event", "event": {
        "type": "content_block_delta",
        "delta": {"type": "text_delta", "text": json.dumps(o) + "\n"}}})
    _fake_cli(monkeypatch, f"import sys; print({line({'key_point': 'A'})!r}); "
                           f"print({line({'paragraph': 'half'})!r}); print({ERR!r}); "
                           "sys.exit(1)")
    with pytest.raises(ClaudeError):
        summarize.summarize_text([Cue(0, 1000, "x")], "C", "T", None, timeout=10,
                                 on_partial=lambda *_: None)


def test_output_after_the_answer_is_drained_not_cut_off(monkeypatch, tmp_path):
    """A persisted Q&A session is flushed as the CLI exits. Closing the pipe under
    it could turn that write into a broken pipe and cost the session."""
    marker = tmp_path / "flushed"
    _fake_cli(monkeypatch,
              f"import sys; print({DELTA!r}); print({RESULT!r}); sys.stdout.flush(); "
              "sys.stdout.write('x' * 200000); sys.stdout.flush(); "
              f"open({str(marker)!r}, 'w').write('ok')")
    assert "".join(cc._stream_deltas("p", "x", timeout=10)) == "hello"
    assert marker.exists(), "the CLI was cut off before it finished writing"


def test_qa_clears_every_per_model_caching_switch(monkeypatch):
    for name in ("DISABLE_PROMPT_CACHING_OPUS", "DISABLE_PROMPT_CACHING_SONNET"):
        monkeypatch.setenv(name, "1")
    env = cc._claude_process_opts(persist=True)["env"]
    assert not any(k.startswith("DISABLE_PROMPT_CACHING") for k in env)


def test_an_unwritable_cache_dir_doesnt_break_every_call(monkeypatch):
    """A root-owned ~/.cache used to turn every summary into a 500."""
    def refuse(*a, **k):
        raise PermissionError("read-only")
    monkeypatch.setattr(type(cc.CLAUDE_DIR), "mkdir", refuse)
    opts = cc._claude_process_opts(persist=False)
    assert "cwd" not in opts and opts["env"]["DISABLE_PROMPT_CACHING"] == "1"
