"""Tests for the Maestro run loop."""

from __future__ import annotations

from maestro.config import Config
from maestro.core.models import BoardState, Issue
from maestro.loop import Loop
from maestro.runlog import read_runs
from maestro.worktree import WorktreeError, WorktreeManager


class FakeProcess:
    """A controllable stand-in for a worker process."""

    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def finish(self, code: int) -> None:
        self.returncode = code


class FakeTrigger:
    """A trigger source returning a board the test controls."""

    def __init__(self, board: BoardState) -> None:
        self.board = board

    def poll(self) -> BoardState:
        return self.board


def _fake_spawn(command, cwd, log_path):
    return FakeProcess()


def _board(*numbers: int) -> BoardState:
    issues = [
        Issue(
            number=n,
            title=f"#{n}",
            priority=None,
            blocked_by=frozenset(),
            in_trigger_column=True,
        )
        for n in numbers
    ]
    return BoardState(issues=issues, open_numbers=set(numbers))


def _config(tmp_path, max_concurrent=2) -> Config:
    return Config(
        repo_path=tmp_path,
        repo_slug="owner/repo",
        project_number=1,
        trigger_column="In Progress",
        max_concurrent=max_concurrent,
        poll_interval=60,
        worker_prompt="/cc-work {issue}",
        theme="nord",
    )


def _make_loop(tmp_path, trigger, *, max_concurrent=2, sleep=lambda _s: None):
    worktrees = WorktreeManager(tmp_path / "repo", tmp_path / "worktrees", runner=lambda _args: "")
    return Loop(
        _config(tmp_path, max_concurrent),
        trigger,
        worktrees,
        tmp_path / "logs",
        tmp_path / "runs.jsonl",
        spawn=_fake_spawn,
        sleep=sleep,
    )


def test_tick_dispatches_ready_issues(tmp_path):
    loop = _make_loop(tmp_path, FakeTrigger(_board(1, 2)))
    loop.tick()
    assert set(loop._workers) == {1, 2}


def test_tick_respects_max_concurrent(tmp_path):
    loop = _make_loop(tmp_path, FakeTrigger(_board(1, 2, 3)), max_concurrent=2)
    loop.tick()
    assert len(loop._workers) == 2


def test_tick_does_not_redispatch_in_flight(tmp_path):
    loop = _make_loop(tmp_path, FakeTrigger(_board(1)))
    loop.tick()
    loop.tick()
    assert len(loop._workers) == 1


def test_reap_records_a_done_worker(tmp_path):
    trigger = FakeTrigger(_board(5))
    loop = _make_loop(tmp_path, trigger)
    loop.tick()
    loop._workers[5].process.finish(0)
    trigger.board = _board()  # issue 5 has left the trigger column
    loop.tick()
    assert 5 not in loop._workers
    records = read_runs(tmp_path / "runs.jsonl")
    assert len(records) == 1
    assert records[0].issue == 5
    assert records[0].outcome == "done"


def test_reap_marks_a_failed_worker(tmp_path):
    trigger = FakeTrigger(_board(8))
    loop = _make_loop(tmp_path, trigger)
    loop.tick()
    loop._workers[8].process.finish(1)
    trigger.board = _board()
    loop.tick()
    assert read_runs(tmp_path / "runs.jsonl")[0].outcome == "failed"


def test_run_exits_cleanly_on_interrupt(tmp_path):
    def interrupt(_seconds):
        raise KeyboardInterrupt

    loop = _make_loop(tmp_path, FakeTrigger(_board()), sleep=interrupt)
    loop.run()  # must return without raising


def test_tick_skips_issue_when_worktree_setup_fails(tmp_path):
    def boom(args: list[str]) -> str:
        if "worktree" in args and "add" in args:
            raise WorktreeError("simulated failure")
        return ""

    worktrees = WorktreeManager(tmp_path / "repo", tmp_path / "worktrees", runner=boom)
    loop = Loop(
        _config(tmp_path),
        FakeTrigger(_board(1)),
        worktrees,
        tmp_path / "logs",
        tmp_path / "runs.jsonl",
        spawn=_fake_spawn,
        sleep=lambda _s: None,
    )
    loop.tick()
    assert loop._workers == {}
