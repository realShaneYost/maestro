"""Tests for worker-log status rendering."""

from __future__ import annotations

import json

from maestro.core.models import BoardState, Issue
from maestro.status import (
    LogLine,
    _issue_of,
    _parse_event_line,
    _render_line,
    _short_title,
    _summarize,
    recent_lines,
    render_board_summary,
)


def _issue(
    number: int,
    title: str = "",
    *,
    status_column: str | None,
) -> Issue:
    return Issue(
        number=number,
        title=title,
        priority=None,
        blocked_by=frozenset(),
        in_trigger_column=status_column == "In Progress",
        status_column=status_column,
    )


def _flatten(lines):
    return "\n".join(line.plain for line in lines)


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


def test_render_board_summary_orders_columns_fixed_then_others_alpha():
    board = BoardState(
        issues=[
            _issue(1, "in-progress one", status_column="In Progress"),
            _issue(2, "blocked one", status_column="Blocked"),
            _issue(3, "review one", status_column="In Review"),
            _issue(4, "backlog one", status_column="Backlog"),
            _issue(5, "done one", status_column="Done"),
        ],
        open_numbers={1, 2, 3, 4, 5},
    )
    lines = render_board_summary(board, "In Progress", "Blocked", "In Review")
    text = _flatten(lines)
    # Fixed-order columns first, then the other observed columns alphabetically.
    assert text.index("In Progress") < text.index("Blocked")
    assert text.index("Blocked") < text.index("In Review")
    assert text.index("In Review") < text.index("Backlog")
    assert text.index("Backlog") < text.index("Done")


def test_render_board_summary_omits_empty_columns():
    board = BoardState(
        issues=[_issue(1, "only", status_column="In Progress")],
        open_numbers={1},
    )
    lines = render_board_summary(board, "In Progress", "Blocked", "In Review")
    text = _flatten(lines)
    assert "Blocked" not in text
    assert "In Review" not in text
    assert "In Progress" in text


def test_render_board_summary_skips_issues_without_a_status():
    board = BoardState(
        issues=[_issue(1, "untriaged", status_column=None)],
        open_numbers={1},
    )
    assert render_board_summary(board, "In Progress", "Blocked", "In Review") == []


def test_render_board_summary_lists_issue_numbers_and_short_titles():
    board = BoardState(
        issues=[
            _issue(42, "Implement feature X", status_column="In Progress"),
            _issue(7, "Tiny ticket", status_column="In Progress"),
        ],
        open_numbers={42, 7},
    )
    lines = render_board_summary(board, "In Progress", "Blocked", "In Review")
    text = _flatten(lines)
    # Issues are sorted by number within the column.
    assert text.index("#7") < text.index("#42")
    assert "Implement feature X" in text
    assert "Tiny ticket" in text


def test_render_board_summary_annotates_held_issue():
    board = BoardState(
        issues=[
            _issue(38, "build the CLI", status_column="In Progress"),
            _issue(7, "unrelated", status_column="In Progress"),
        ],
        open_numbers={7, 38},
    )
    held = {38: "skipped — blocked by open issue(s): #37"}
    lines = render_board_summary(board, "In Progress", "Blocked", "In Review", held)
    text = _flatten(lines)
    # The held issue carries its reason inline; the unrelated one does not.
    assert "#38  build the CLI  — skipped — blocked by open issue(s): #37" in text
    assert "#7  unrelated\n" in text + "\n"


def test_render_board_summary_no_held_reasons_leaves_rows_plain():
    board = BoardState(
        issues=[_issue(1, "plain", status_column="In Progress")],
        open_numbers={1},
    )
    lines = render_board_summary(board, "In Progress", "Blocked", "In Review")
    assert "blocked by" not in _flatten(lines)
