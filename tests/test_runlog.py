"""Tests for the append-only run log."""

from __future__ import annotations

from maestro.core.models import RunRecord
from maestro.runlog import append_run, consecutive_failures, read_runs


def _record(issue: int, outcome: str = "done") -> RunRecord:
    return RunRecord(
        issue=issue,
        started="2026-05-21T00:00:00Z",
        ended="2026-05-21T00:30:00Z",
        outcome=outcome,
        pr_url=f"https://example.com/pr/{issue}",
    )


def test_read_runs_missing_file_returns_empty(tmp_path):
    assert read_runs(tmp_path / "runs.jsonl") == []


def test_append_then_read_round_trips(tmp_path):
    log = tmp_path / "runs.jsonl"
    append_run(log, _record(1))
    append_run(log, _record(2, outcome="failed"))
    runs = read_runs(log)
    assert [r.issue for r in runs] == [1, 2]
    assert runs[1].outcome == "failed"
    assert runs[0].pr_url == "https://example.com/pr/1"


def test_read_runs_skips_malformed_lines(tmp_path):
    log = tmp_path / "runs.jsonl"
    append_run(log, _record(5))
    with log.open("a", encoding="utf-8") as handle:
        handle.write("{not valid json\n")
        handle.write("\n")
    assert [r.issue for r in read_runs(log)] == [5]


def test_append_creates_parent_directory(tmp_path):
    log = tmp_path / "nested" / "dir" / "runs.jsonl"
    append_run(log, _record(9))
    assert read_runs(log)[0].issue == 9


def test_consecutive_failures_empty_for_no_records():
    assert consecutive_failures([]) == {}


def test_consecutive_failures_counts_trailing_failures():
    records = [_record(1, "failed"), _record(1, "failed"), _record(1, "failed")]
    assert consecutive_failures(records) == {1: 3}


def test_consecutive_failures_resets_on_done():
    # Two failures, then a clean done run, then one more failure: streak is 1.
    records = [
        _record(1, "failed"),
        _record(1, "failed"),
        _record(1, "done"),
        _record(1, "failed"),
    ]
    assert consecutive_failures(records) == {1: 1}


def test_consecutive_failures_resets_on_blocked():
    # A blocked run is a clean park, not a crash — it resets the streak.
    records = [_record(2, "failed"), _record(2, "blocked")]
    assert consecutive_failures(records) == {}


def test_consecutive_failures_omits_issues_with_zero_streak():
    # Issue 1 ends clean (omitted); issue 2 ends failed (kept).
    records = [_record(1, "failed"), _record(1, "done"), _record(2, "failed")]
    assert consecutive_failures(records) == {2: 1}


def test_consecutive_failures_tracks_issues_independently():
    records = [
        _record(1, "failed"),
        _record(2, "failed"),
        _record(1, "failed"),
        _record(2, "done"),
    ]
    assert consecutive_failures(records) == {1: 2}
