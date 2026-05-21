"""Tests for cleanup of Maestro's runtime artifacts."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path

from maestro.clean import clean
from maestro.worktree import WorktreeManager


def _runner(
    worktrees_dir: Path, issues: Iterable[int], gone_branches: Iterable[str]
) -> Callable[[list[str]], str]:
    """Build a fake git runner returning canned worktree and branch listings."""
    porcelain = "\n\n".join(f"worktree {worktrees_dir}/issue-{n}\nHEAD x\ndetached" for n in issues)
    for_each_ref = "\n".join(f"{branch} [gone]" for branch in gone_branches)

    def run(args: list[str]) -> str:
        if "list" in args and "--porcelain" in args:
            return porcelain
        if "for-each-ref" in args:
            return for_each_ref
        return ""

    return run


def _manager(tmp_path: Path, issues=(), gone_branches=()) -> WorktreeManager:
    wt_dir = tmp_path / "worktrees"
    return WorktreeManager(
        tmp_path / "repo", wt_dir, runner=_runner(wt_dir.resolve(), issues, gone_branches)
    )


def test_clean_removes_worktrees_and_branches(tmp_path):
    manager = _manager(tmp_path, issues=[1, 2], gone_branches=["issue/1-x"])
    report = clean(manager, tmp_path / "logs", tmp_path / "runs.jsonl")
    assert len(report.worktrees_removed) == 2
    assert report.branches_deleted == ["issue/1-x"]
    assert report.logs_cleared is False
    assert report.history_cleared is False


def test_clean_default_keeps_logs_and_history(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "issue-1.log").write_text("data", encoding="utf-8")
    runs = tmp_path / "runs.jsonl"
    runs.write_text('{"issue": 1}\n', encoding="utf-8")
    clean(_manager(tmp_path), logs, runs)
    assert (logs / "issue-1.log").exists()
    assert runs.read_text(encoding="utf-8") != ""


def test_clean_logs_flag_clears_log_files(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "issue-1.log").write_text("data", encoding="utf-8")
    (logs / "issue-2.log").write_text("data", encoding="utf-8")
    report = clean(_manager(tmp_path), logs, tmp_path / "runs.jsonl", clear_logs=True)
    assert list(logs.glob("*.log")) == []
    assert report.logs_cleared is True


def test_clean_history_flag_truncates_run_log(tmp_path):
    runs = tmp_path / "runs.jsonl"
    runs.write_text('{"issue": 1}\n', encoding="utf-8")
    report = clean(_manager(tmp_path), tmp_path / "logs", runs, clear_history=True)
    assert runs.read_text(encoding="utf-8") == ""
    assert report.history_cleared is True


def test_delete_merged_branches_targets_only_gone_non_main(tmp_path):
    listing = "main \nissue/5-done [gone]\nissue/9-active [ahead 1]\nissue/3-done [gone]\n"
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(args)
        return listing if "for-each-ref" in args else ""

    manager = WorktreeManager(tmp_path / "repo", tmp_path / "wt", runner=runner)
    deleted = manager.delete_merged_branches()
    assert sorted(deleted) == ["issue/3-done", "issue/5-done"]
    assert sum(1 for call in calls if "branch" in call and "-D" in call) == 2
