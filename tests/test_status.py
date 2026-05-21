"""Tests for worker status reconstruction and rendering."""

from __future__ import annotations

from pathlib import Path

from maestro.status import (
    WorkerStatus,
    _humanize,
    _status_table,
    _truncate,
    collect_statuses,
)
from maestro.worktree import WorktreeManager


def _porcelain(worktrees_dir: Path, *issues: int) -> str:
    blocks = ["worktree /some/repo\nHEAD aaa\nbranch refs/heads/main"]
    for number in issues:
        blocks.append(f"worktree {worktrees_dir}/issue-{number}\nHEAD bbb\ndetached")
    return "\n\n".join(blocks) + "\n"


def _manager(worktrees_dir: Path, porcelain: str) -> WorktreeManager:
    return WorktreeManager(Path("/repo"), worktrees_dir, runner=lambda _args: porcelain)


def test_collect_statuses_empty(tmp_path):
    wt_dir = tmp_path / "worktrees"
    manager = _manager(wt_dir, _porcelain(wt_dir))
    assert collect_statuses(manager, tmp_path / "logs") == []


def test_collect_statuses_reads_log_tail(tmp_path):
    wt_dir = tmp_path / "worktrees"
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "issue-7.log").write_text("starting\nworking on it\n\n", encoding="utf-8")
    manager = _manager(wt_dir, _porcelain(wt_dir, 7))
    result = collect_statuses(manager, logs)
    assert len(result) == 1
    assert result[0].issue == 7
    assert result[0].last_line == "working on it"
    assert result[0].last_activity is not None


def test_collect_statuses_sorted_by_issue(tmp_path):
    wt_dir = tmp_path / "worktrees"
    manager = _manager(wt_dir, _porcelain(wt_dir, 9, 2, 5))
    result = collect_statuses(manager, tmp_path / "logs")
    assert [s.issue for s in result] == [2, 5, 9]


def test_collect_statuses_handles_missing_log(tmp_path):
    wt_dir = tmp_path / "worktrees"
    manager = _manager(wt_dir, _porcelain(wt_dir, 3))
    result = collect_statuses(manager, tmp_path / "logs")
    assert result[0].last_activity is None
    assert result[0].last_line == ""


def test_humanize_formats_deltas():
    assert _humanize(None) == "—"
    assert _humanize(5) == "5s ago"
    assert _humanize(125) == "2m ago"
    assert _humanize(7200) == "2h ago"


def test_truncate_shortens_long_text():
    assert _truncate("short", 80) == "short"
    out = _truncate("x" * 100, 10)
    assert len(out) == 10
    assert out.endswith("…")


def test_status_table_row_count():
    statuses = [
        WorkerStatus(issue=1, last_activity=10, last_line="a"),
        WorkerStatus(issue=2, last_activity=None, last_line="b"),
    ]
    assert _status_table(statuses, max_concurrent=2).row_count == 2


def test_status_table_empty_shows_placeholder():
    assert _status_table([], max_concurrent=2).row_count == 1
