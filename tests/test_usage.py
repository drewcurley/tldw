import json

import pytest

from youtube_tldw import cli, usage


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "usage.jsonl")
    usage._local.interaction = None
    yield
    usage._local.interaction = None


ENVELOPE = {
    "session_id": "sess-1", "total_cost_usd": 0.4825, "duration_ms": 2830,
    "usage": {"input_tokens": 2, "cache_creation_input_tokens": 48127,
              "cache_read_input_tokens": 0, "output_tokens": 11},
}


def test_parse_usage_reads_the_cli_envelope():
    u = usage.parse_usage(ENVELOPE, "summarize")
    assert u.input_tokens == 2 and u.cache_creation_tokens == 48127
    assert u.output_tokens == 11 and u.cost_usd == pytest.approx(0.4825)
    assert u.session_id == "sess-1" and u.step == "summarize"
    # Cached or not, the model still had to be handed all of it.
    assert u.billed_input == 48129
    assert u.total_tokens == 48140


def test_parse_usage_survives_a_bare_envelope():
    u = usage.parse_usage({}, "ask")
    assert u.billed_input == 0 and u.cost_usd == 0.0 and u.session_id == ""


def test_records_group_into_one_interaction(capsys):
    with usage.interaction("ask", "vid1"):
        usage.record(usage.parse_usage(ENVELOPE, "ask"))
        usage.record(usage.parse_usage(
            {"total_cost_usd": 0.027,
             "usage": {"input_tokens": 2, "cache_read_input_tokens": 52607,
                       "output_tokens": 7}}, "ask"))
        inter = usage.current()
        assert len(inter.calls) == 2
        totals = inter.totals()
        assert totals.output_tokens == 18
        assert totals.cost_usd == pytest.approx(0.5095)
    assert usage.current() is None                    # scope closed

    rows = usage.read_rows()
    assert [r["kind"] for r in rows] == ["ask", "ask"]
    assert [r["video_id"] for r in rows] == ["vid1", "vid1"]
    assert rows[0]["cache_creation_tokens"] == 48127


def test_nested_interactions_do_not_split_the_group():
    with usage.interaction("summarize", "vid1"):
        with usage.interaction("segments", "vid1"):     # inner is a no-op
            usage.record(usage.parse_usage(ENVELOPE, "inner"))
        assert usage.current().kind == "summarize"
        assert len(usage.current().calls) == 1
    assert usage.current() is None


def test_accounting_never_breaks_the_request(monkeypatch, capsys):
    """A broken log must not take down the summary it was measuring."""
    def boom(_row):
        raise OSError("disk full")

    monkeypatch.setattr(usage, "_append", boom)
    with usage.interaction("ask", "vid1"):
        usage.record(usage.parse_usage(ENVELOPE, "ask"))   # must not raise
    assert "usage accounting failed" in capsys.readouterr().out


def test_record_outside_an_interaction_is_allowed():
    usage.record(usage.parse_usage(ENVELOPE, "cli"))
    rows = usage.read_rows()
    assert len(rows) == 1 and rows[0]["kind"] == ""


def test_read_rows_skips_corrupt_lines(tmp_path):
    f = tmp_path / "u.jsonl"
    f.write_text('{"step":"a"}\nnot json\n\n[1,2]\n{"step":"b"}\n')
    assert [r["step"] for r in usage.read_rows(f)] == ["a", "b"]


def test_read_rows_on_a_missing_file():
    assert usage.read_rows() == []


def _write(path, rows):
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n")


def test_usage_report_summarizes(tmp_path, capsys):
    f = tmp_path / "u.jsonl"
    _write(f, [
        {"ts": 1e9, "kind": "summarize", "video_id": "v1", "step": "summarize",
         "input_tokens": 10, "cache_creation_tokens": 40000, "cache_read_tokens": 0,
         "output_tokens": 900, "cost_usd": 0.40},
        {"ts": 1e9, "kind": "ask", "video_id": "v1", "step": "ask",
         "input_tokens": 2, "cache_creation_tokens": 100, "cache_read_tokens": 50000,
         "output_tokens": 200, "cost_usd": 0.03},
        {"ts": 1e9, "kind": "summarize", "video_id": "v2", "step": "summarize",
         "input_tokens": 10, "cache_creation_tokens": 30000, "cache_read_tokens": 0,
         "output_tokens": 500, "cost_usd": 0.20},
    ])
    assert cli.main(["usage", "--file", str(f)]) == 0
    out = capsys.readouterr().out
    assert "3 model call(s) over 2 video(s)" in out
    assert "$0.63 total" in out
    assert "summarize" in out and "ask" in out
    assert "per video:" in out                 # the number that matters

    assert cli.main(["usage", "--file", str(f), "--by-video"]) == 0
    out = capsys.readouterr().out
    assert "v1" in out and "v2" in out


def test_usage_report_with_no_data(tmp_path, capsys):
    assert cli.main(["usage", "--file", str(tmp_path / "nope.jsonl")]) == 0
    assert "No usage recorded" in capsys.readouterr().out


def test_every_model_call_is_labelled_with_its_step(monkeypatch):
    """Mislabelled steps quietly corrupt the per-video cost figures — the segment
    call used to be recorded as 'summarize'."""
    from youtube_tldw import summarize
    from youtube_tldw.transcript import Cue

    seen = []

    def fake_ask_json(prompt, payload, *, validate, timeout, step="summarize"):
        seen.append(step)
        return validate({"segments": [{"first_cue": 0, "last_cue": 1}],
                         "chosen_ratio": 0.3, "rationale": "ok"})

    monkeypatch.setattr(summarize, "ask_json", fake_ask_json)
    monkeypatch.setattr(summarize, "is_claude_cli", lambda: False)
    cues = [Cue(i * 1000, i * 1000 + 900, f"line {i}") for i in range(4)]
    summarize.select_video_segments(cues, "C", "T", None, None, timeout=1)
    assert seen == ["segments"]                   # not "summarize"
