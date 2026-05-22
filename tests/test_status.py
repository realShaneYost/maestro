"""Tests for worker-log status rendering."""

from __future__ import annotations

import json

from maestro.status import (
    LogLine,
    _issue_of,
    _parse_event_line,
    _render_line,
    _short_title,
    _summarize,
    recent_lines,
)


def test_issue_of_extracts_number(tmp_path):
    assert _issue_of(tmp_path / "issue-58.log") == 58
    assert _issue_of(tmp_path / "notes.log") is None


def test_short_title_truncates_long_titles():
    assert _short_title("short") == "short"
    out = _short_title("a very long issue title here", 15)
    assert len(out) == 15
    assert out.endswith("…")


def test_summarize_system_event():
    assert _summarize({"type": "system", "subtype": "init"}) == "session init"


def test_summarize_result_event():
    assert _summarize({"type": "result", "subtype": "success"}) == "finished: success"


def test_summarize_result_drops_cost_keeps_duration_and_turns():
    message = _summarize(
        {
            "type": "result",
            "subtype": "success",
            "total_cost_usd": 1.5,
            "duration_ms": 288706,
            "num_turns": 43,
        }
    )
    assert message is not None
    assert "finished: success" in message
    assert "43 turns" in message
    assert "$" not in message


def test_summarize_rate_limit_event():
    assert _summarize({"type": "rate_limit_event"}) == "rate limit reached — waiting"


def test_summarize_tool_use_shows_bash_detail():
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Bash",
                    "input": {"description": "Fetch origin", "command": "git fetch"},
                }
            ]
        },
    }
    assert _summarize(event) == "Bash: Fetch origin"


def test_summarize_tool_use_shows_edited_file():
    event = {
        "type": "assistant",
        "message": {
            "content": [
                {
                    "type": "tool_use",
                    "name": "Edit",
                    "input": {"file_path": "obsidian-notes/WORKFLOW.md"},
                }
            ]
        },
    }
    assert _summarize(event) == "Edit: obsidian-notes/WORKFLOW.md"


def test_summarize_skips_user_events():
    assert _summarize({"type": "user", "message": {}}) is None


def test_parse_event_line_valid_json():
    line = _parse_event_line(7, json.dumps({"type": "system", "subtype": "init"}))
    assert line is not None
    assert line.issue == 7
    assert line.message == "session init"
    assert line.is_error is False


def test_parse_event_line_blank_is_skipped():
    assert _parse_event_line(7, "   ") is None


def test_parse_event_line_non_json_is_flagged_error():
    line = _parse_event_line(7, "Traceback (most recent call last):")
    assert line is not None
    assert line.is_error is True
    assert "Traceback" in line.message


def test_parse_event_line_marks_error_results():
    line = _parse_event_line(7, json.dumps({"type": "result", "subtype": "error_x"}))
    assert line is not None
    assert line.is_error is True


def test_recent_lines_reads_each_log(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    events = (
        json.dumps({"type": "system", "subtype": "init"})
        + "\n"
        + json.dumps({"type": "result", "subtype": "success"})
        + "\n"
    )
    (logs / "issue-3.log").write_text(events, encoding="utf-8")
    lines = recent_lines(logs)
    assert [line.issue for line in lines] == [3, 3]
    assert lines[0].message == "session init"


def test_recent_lines_missing_dir_is_empty(tmp_path):
    assert recent_lines(tmp_path / "absent") == []


def test_render_line_contains_all_parts():
    text = _render_line(
        LogLine(issue=58, message="using Bash", is_error=False),
        "12:00:00",
        "Add a tagline",
    )
    assert "#58" in text.plain
    assert "Add a tagline" in text.plain
    assert "12:00:00" in text.plain
    assert "using Bash" in text.plain


def test_render_line_without_title_shows_number_only():
    text = _render_line(LogLine(issue=58, message="m", is_error=False), "12:00:00", "")
    assert text.plain.startswith("#58  ")
