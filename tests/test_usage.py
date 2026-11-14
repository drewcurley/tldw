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
    assert "3 model call(s)" in out
    assert "$0.63 total" in out
    assert "summarize" in out and "ask" in out
    assert "per video:" in out                 # the number that matters

    assert cli.main(["usage", "--file", str(f), "--by-video"]) == 0
    out = capsys.readouterr().out
    assert "v1" in out and "v2" in out


def test_stats_count_videos_and_time_saved(tmp_path):
    f = tmp_path / "u.jsonl"
    _write(f, [
        {"ts": 1e9, "kind": "video", "video_id": "v1",
         "video_ms": 20 * 60_000, "read_ms": 3 * 60_000},
        {"ts": 1e9, "kind": "video", "video_id": "v2",
         "video_ms": 40 * 60_000, "read_ms": 5 * 60_000},
        {"ts": 1e9, "kind": "summarize", "video_id": "v1", "cost_usd": 0.4},
    ])
    st = usage.stats(f)
    assert st["videos"] == 2                       # the model call isn't a video
    assert st["watch_ms"] == 60 * 60_000
    assert st["saved_ms"] == 52 * 60_000
    assert st["saved_hours"] == pytest.approx(0.9, abs=0.05)


def test_stats_do_not_double_count_a_resummarized_video(tmp_path):
    f = tmp_path / "u.jsonl"
    _write(f, [
        {"ts": 1e9, "kind": "video", "video_id": "v1",
         "video_ms": 20 * 60_000, "read_ms": 3 * 60_000},
        {"ts": 2e9, "kind": "video", "video_id": "v1",
         "video_ms": 20 * 60_000, "read_ms": 2 * 60_000},
    ])
    st = usage.stats(f)
    assert st["videos"] == 1
    assert st["read_ms"] == 2 * 60_000             # the later run wins


def test_note_video_records_read_time_from_word_count(tmp_path, monkeypatch):
    monkeypatch.setattr(usage, "USAGE_FILE", tmp_path / "u.jsonl")
    usage.note_video("v1", 18 * 60_000, 1200)      # 1200 words at 200wpm = 6 min
    row = usage.read_rows()[0]
    assert row["kind"] == "video" and row["video_id"] == "v1"
    assert row["read_ms"] == 6 * 60_000
    assert usage.stats()["saved_ms"] == 12 * 60_000


def test_usage_report_leads_with_time_saved(tmp_path, capsys):
    f = tmp_path / "u.jsonl"
    _write(f, [
        {"ts": 1e9, "kind": "video", "video_id": "v1",
         "video_ms": 45 * 60_000, "read_ms": 5 * 60_000},
        {"ts": 1e9, "kind": "summarize", "video_id": "v1", "step": "summarize",
         "cost_usd": 0.4, "output_tokens": 900},
    ])
    assert cli.main(["usage", "--file", str(f)]) == 0
    out = capsys.readouterr().out
    assert "1 video(s) TL;DW'd" in out
    assert "hours you didn't have to watch" in out
    assert "1 model call(s)" in out                # the video row isn't a call


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


def test_parse_usage_reads_the_model_from_model_usage():
    """The CLI's result event has no top-level model — it's under modelUsage."""
    u = usage.parse_usage({"usage": {"output_tokens": 1},
                           "modelUsage": {"claude-sonnet-5": {"outputTokens": 1}}})
    assert u.model == "claude-sonnet-5"


def test_usage_report_breaks_cost_down_by_model(tmp_path, capsys):
    f = tmp_path / "u.jsonl"
    _write(f, [
        {"ts": 1e9, "kind": "summarize", "video_id": "v1", "step": "summarize",
         "model": "claude-opus-5[1m]", "cost_usd": 0.10, "output_tokens": 3000},
        {"ts": 1e9, "kind": "summarize", "video_id": "v2", "step": "summarize",
         "model": "claude-sonnet-5", "cost_usd": 0.04, "output_tokens": 1600},
    ])
    assert cli.main(["usage", "--file", str(f)]) == 0
    out = capsys.readouterr().out
    assert "claude-opus-5 " in out            # the [1m] context suffix is folded
    assert "claude-sonnet-5" in out


def test_served_rows_are_not_counted_as_model_calls(monkeypatch, tmp_path, capsys):
    """A selection reaching the panel costs nothing and isn't a call; counting it
    would inflate the totals and every per-step average."""
    import json
    from youtube_tldw import cli
    log = tmp_path / "u.jsonl"
    rows = [
        {"ts": 1, "kind": "segments", "step": "segments", "trigger": "prefetch",
         "cost_usd": 0.07, "video_id": "a"},
        {"ts": 2, "kind": "segments", "step": "segments", "trigger": "request",
         "cost_usd": 0.07, "video_id": "b"},
        {"ts": 3, "kind": "segments", "step": "segments", "cost_usd": 0.12,
         "video_id": "c"},                                  # from before tracking
        {"ts": 4, "kind": "segments_served", "video_id": "a", "how": "cache"},
        {"ts": 5, "kind": "segments_served", "video_id": "b", "how": "live"},
    ]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert cli._run_usage(["--file", str(log)]) == 0
    out = capsys.readouterr().out
    assert "3 model call(s)" in out
    assert "1 prefetch call(s) ($0.07), 1 on request, 2 selection(s) played" in out
    assert "1 recorded before tracking began" in out


# --- automatic summaries count only once someone opens them (issue #20) ----------

def _video(vid, ts, trigger=None, video_ms=600_000, read_ms=60_000):
    row = {"ts": ts, "kind": "video", "step": "video", "video_id": vid,
           "video_ms": video_ms, "read_ms": read_ms, "words": 200}
    if trigger:
        row["trigger"] = trigger
    return row


def _served(vid, ts, how="viewed"):
    return {"ts": ts, "kind": "summary_served", "video_id": vid, "how": how}


def _stats(tmp_path, rows):
    log = tmp_path / "s.jsonl"
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return usage.stats(log)


def test_an_automatic_summary_nobody_opened_does_not_count(tmp_path):
    assert _stats(tmp_path, [_video("a", 1, "auto")])["videos"] == 0


@pytest.mark.parametrize("served_ts", [0, 2])
def test_an_opened_automatic_summary_counts_whenever_it_was_opened(tmp_path, served_ts):
    """No timestamp comparison: a clock change can't flip it, and having read an
    earlier summary of the video is just as much proof."""
    st = _stats(tmp_path, [_video("a", 1, "auto"), _served("a", served_ts)])
    assert st["videos"] == 1 and st["watch_ms"] == 600_000


def test_a_later_automatic_run_never_uncounts_a_video_someone_asked_for(tmp_path):
    st = _stats(tmp_path, [_video("a", 1), _video("a", 2, "auto", video_ms=900_000)])
    assert st["videos"] == 1
    assert st["watch_ms"] == 900_000          # the latest run's numbers


def test_untagged_rows_from_before_this_change_still_count(tmp_path):
    assert _stats(tmp_path, [_video("a", 1), _video("b", 2)])["videos"] == 2


def test_a_served_row_alone_is_not_a_video(tmp_path):
    assert _stats(tmp_path, [_served("x", 1)])["videos"] == 0


def test_an_opened_automatic_summary_is_counted_once(tmp_path):
    st = _stats(tmp_path, [_video("a", 1, "auto"), _served("a", 2), _served("a", 3),
                           _video("a", 4, "auto")])
    assert st["videos"] == 1


def test_only_an_automatic_rows_tag_is_written(tmp_path):
    usage.note_video("a", 600_000, 100)
    usage.note_video("b", 600_000, 100, trigger="auto")
    rows = usage.read_rows()
    assert "trigger" not in rows[0]
    assert rows[1]["trigger"] == "auto"


def test_note_summary_served_writes_its_row():
    usage.note_summary_served("abcdefghijk", "viewed")
    (row,) = usage.read_rows()
    assert (row["kind"], row["video_id"], row["how"]) == ("summary_served", "abcdefghijk",
                                                           "viewed")


def test_served_rows_are_not_model_calls_in_tldw_usage(tmp_path, capsys):
    log = tmp_path / "u.jsonl"
    rows = [{"ts": 1, "kind": "summarize", "step": "summarize", "cost_usd": 0.10,
             "video_id": "a", "model": "opus"},
            _video("a", 2, "auto"), _served("a", 3), _served("b", 4)]
    log.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert cli._run_usage(["--file", str(log)]) == 0
    out = capsys.readouterr().out
    assert "1 model call(s)" in out
    assert "summary_served" not in out and "\n  ?" not in out
    assert "1 video(s) TL;DW'd" in out
    assert usage.NOT_MODEL_CALLS == frozenset({"video", "segments_served", "summary_served"})


def test_tldw_usage_filters_with_the_one_shared_list():
    import inspect
    src = inspect.getsource(cli._run_usage)
    assert "usage_mod.NOT_MODEL_CALLS" in src
    assert '"segments_served")' not in src          # no local copy of the list
