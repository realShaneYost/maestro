"""Tests for the Maestro run loop."""

from __future__ import annotations

from maestro.config import Config
from maestro.core.models import BoardState, Issue
from maestro.github import GitHubError
from maestro.loop import Loop, classify_outcome
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


class RaisingTrigger:
    """A trigger source whose poll always fails, as a rate-limited one would."""

    def poll(self) -> BoardState:
        raise GitHubError("simulated rate-limit error")


def _fake_spawn(command, cwd, log_path):
    return FakeProcess()


def test_classify_outcome_review_column_is_done():
    statuses = {7: "In Review"}
    assert classify_outcome(7, 0, statuses, {7}, "In Review", "Blocked") == "done"


def test_classify_outcome_blocked_column_is_blocked_even_with_exit_zero():
    statuses = {7: "Blocked"}
    assert classify_outcome(7, 0, statuses, {7}, "In Review", "Blocked") == "blocked"


def test_classify_outcome_still_in_trigger_is_failed_on_any_exit():
    statuses = {7: "In Progress"}
    assert classify_outcome(7, 0, statuses, {7}, "In Review", "Blocked") == "failed"
    assert classify_outcome(7, 1, statuses, {7}, "In Review", "Blocked") == "failed"


def test_classify_outcome_closed_issue_falls_back_to_exit_code():
    assert classify_outcome(7, 0, {}, set(), "In Review", "Blocked") == "done"
    assert classify_outcome(7, 1, {}, set(), "In Review", "Blocked") == "failed"


def _issue(number: int, *, status_column: str | None = "In Progress") -> Issue:
    return Issue(
        number=number,
        title=f"#{number}",
        priority=None,
        blocked_by=frozenset(),
        in_trigger_column=status_column == "In Progress",
        status_column=status_column,
    )


def _board(*numbers: int) -> BoardState:
    return BoardState(
        issues=[_issue(n) for n in numbers],
        open_numbers=set(numbers),
    )


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
        worktree_setup=None,
        review_column="In Review",
        blocked_column="Blocked",
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


def _board_with(issue: Issue) -> BoardState:
    return BoardState(issues=[issue], open_numbers={issue.number})


def test_reap_records_a_done_worker_by_review_column(tmp_path):
    trigger = FakeTrigger(_board(5))
    loop = _make_loop(tmp_path, trigger)
    loop.tick()
    loop._workers[5].process.finish(0)
    trigger.board = _board_with(_issue(5, status_column="In Review"))
    loop.tick()
    assert 5 not in loop._workers
    records = read_runs(tmp_path / "runs.jsonl")
    assert len(records) == 1
    assert records[0].issue == 5
    assert records[0].outcome == "done"


def test_reap_records_blocked_by_column_and_removes_worktree(tmp_path):
    calls: list[list[str]] = []

    def runner(args: list[str]) -> str:
        calls.append(list(args))
        return ""

    worktrees = WorktreeManager(tmp_path / "repo", tmp_path / "worktrees", runner=runner)
    trigger = FakeTrigger(_board(8))
    loop = Loop(
        _config(tmp_path),
        trigger,
        worktrees,
        tmp_path / "logs",
        tmp_path / "runs.jsonl",
        spawn=_fake_spawn,
        sleep=lambda _s: None,
    )
    loop.tick()
    loop._workers[8].process.finish(2)  # non-zero blocked exit
    trigger.board = _board_with(_issue(8, status_column="Blocked"))
    loop.tick()
    record = read_runs(tmp_path / "runs.jsonl")[0]
    assert record.outcome == "blocked"
    assert any("worktree" in call and "remove" in call for call in calls)


def test_reap_crashed_exit_zero_in_trigger_column_is_failed(tmp_path):
    """A worker that exits cleanly without parking its card is a contract crash."""
    trigger = FakeTrigger(_board(7))
    loop = _make_loop(tmp_path, trigger)
    loop.tick()
    loop._workers[7].process.finish(0)
    # Card stays in trigger column — no parking happened.
    loop.tick()
    assert read_runs(tmp_path / "runs.jsonl")[0].outcome == "failed"


def test_reap_closed_issue_fallback_uses_exit_code(tmp_path):
    """If the issue is closed and removed from the board, the exit code wins."""
    trigger = FakeTrigger(_board(11))
    loop = _make_loop(tmp_path, trigger)
    loop.tick()
    loop._workers[11].process.finish(0)
    trigger.board = _board()  # issue 11 has left the board entirely
    loop.tick()
    assert read_runs(tmp_path / "runs.jsonl")[0].outcome == "done"

    trigger2 = FakeTrigger(_board(12))
    loop2 = _make_loop(tmp_path, trigger2)
    loop2.tick()
    loop2._workers[12].process.finish(1)
    trigger2.board = _board()
    loop2.tick()
    assert read_runs(tmp_path / "runs.jsonl")[-1].outcome == "failed"


def test_run_exits_cleanly_on_interrupt(tmp_path):
    def interrupt(_seconds):
        raise KeyboardInterrupt

    loop = _make_loop(tmp_path, FakeTrigger(_board()), sleep=interrupt)
    loop.run()  # must return without raising


def test_run_survives_a_failed_poll(tmp_path):
    def interrupt(_seconds):
        raise KeyboardInterrupt

    loop = _make_loop(tmp_path, RaisingTrigger(), sleep=interrupt)
    loop.run()  # a GitHubError from the poll must not propagate out of run()


def test_tick_does_not_redispatch_a_just_finished_issue_still_in_trigger(tmp_path):
    trigger = FakeTrigger(_board(1))
    loop = _make_loop(tmp_path, trigger)
    loop.tick()
    assert set(loop._workers) == {1}
    loop._workers[1].process.finish(0)
    loop.tick()  # reap; card is still in the trigger column
    assert loop._workers == {}
    assert loop._recently_dispatched == {1}
    loop.tick()  # next tick: guard still holds, no re-dispatch
    assert loop._workers == {}
    assert loop._recently_dispatched == {1}


def test_recently_dispatched_releases_when_card_leaves_trigger(tmp_path):
    trigger = FakeTrigger(_board(1))
    loop = _make_loop(tmp_path, trigger)
    loop.tick()
    loop._workers[1].process.finish(0)
    trigger.board = _board()  # card has left the trigger column
    loop.tick()  # reap adds to guard
    assert loop._recently_dispatched == {1}
    loop.tick()  # next tick: release runs first, the guard clears
    assert loop._recently_dispatched == set()
    trigger.board = _board(1)  # card re-enters the trigger column
    loop.tick()
    assert set(loop._workers) == {1}


def test_recently_dispatched_releases_when_issue_closes(tmp_path):
    trigger = FakeTrigger(_board(1))
    loop = _make_loop(tmp_path, trigger)
    loop.tick()
    loop._workers[1].process.finish(0)
    # Card stays in the trigger column, but the issue is no longer open.
    trigger.board = BoardState(issues=[_issue(1)], open_numbers=set())
    loop.tick()  # reap adds to guard
    assert loop._recently_dispatched == {1}
    loop.tick()  # release runs first on the next tick and removes the closed issue
    assert loop._recently_dispatched == set()


def test_recently_dispatched_guards_failed_runs_too(tmp_path):
    trigger = FakeTrigger(_board(1))
    loop = _make_loop(tmp_path, trigger)
    loop.tick()
    loop._workers[1].process.finish(1)  # non-zero exit — failed run
    loop.tick()
    assert loop._workers == {}
    assert loop._recently_dispatched == {1}
    loop.tick()  # guard holds across subsequent ticks while the card stays
    assert loop._workers == {}


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
