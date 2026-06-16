"""The Maestro run loop — poll, schedule, dispatch, reap.

The loop ties the pieces together. Each tick it asks the trigger source what is
on the board, asks the scheduler which issues to dispatch, starts a worker in a
fresh worktree for each, and reaps any workers that have finished — recording a
run-log line and removing the worktree on success. The loop owns no durable
state: in-flight workers live only in memory, everything else is on disk or in
GitHub.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol

from maestro.config import Config
from maestro.core.models import BoardState, Outcome, RunRecord
from maestro.core.scheduler import dispatchable_issues, failure_cap_reason
from maestro.core.trigger import TriggerSource
from maestro.dispatch import build_worker_command, spawn_worker
from maestro.github import GitHubError
from maestro.runlog import append_run, consecutive_failures, read_runs
from maestro.worktree import WorktreeError, WorktreeManager

logger = logging.getLogger(__name__)


class WorkerProcess(Protocol):
    """The slice of a process handle the loop needs to track a worker."""

    returncode: int | None

    def poll(self) -> int | None:
        """Return the exit code if the process has finished, else ``None``."""


# Starts a worker process given (command, cwd, log_path). Injectable so tests
# avoid real subprocesses; the default really spawns a coding agent.
SpawnFunc = Callable[[list[str], Path, Path], WorkerProcess]


@dataclass
class _RunningWorker:
    """A worker the loop is currently tracking."""

    issue: int
    worktree: Path
    process: WorkerProcess
    started: str


def _utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string."""
    return datetime.now(UTC).isoformat()


def classify_outcome(
    issue: int,
    exit_code: int,
    status_by_issue: dict[int, str | None],
    open_numbers: set[int],
    review_column: str,
    blocked_column: str,
) -> Outcome:
    """Classify a finished worker's run as ``done``, ``blocked``, or ``failed``.

    The card's current column is the semantic outcome; the exit code is
    only consulted as a tie-breaker for closed-and-removed issues that no
    longer appear on the board at all.

    Args:
        issue: The issue number being classified.
        exit_code: The worker process's exit code.
        status_by_issue: Map from issue number to Status-field value (or
            ``None`` when the field is unset or the issue is missing from
            the board entirely).
        open_numbers: Issue numbers still open on the repository.
        review_column: Configured ``review_column`` from the config.
        blocked_column: Configured ``blocked_column`` from the config.

    Returns:
        The classification this run resolves to.
    """
    column = status_by_issue.get(issue)
    if column == review_column:
        return "done"
    if column == blocked_column:
        return "blocked"
    if issue not in open_numbers:
        return "done" if exit_code == 0 else "failed"
    return "failed"


class Loop:
    """Polls the board and dispatches workers until interrupted."""

    def __init__(
        self,
        config: Config,
        trigger: TriggerSource,
        worktrees: WorktreeManager,
        logs_dir: Path,
        runs_log: Path,
        *,
        spawn: SpawnFunc = spawn_worker,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """Initialise the loop.

        Args:
            config: Validated Maestro configuration.
            trigger: Source of board state.
            worktrees: Manager for the target repo's worktrees.
            logs_dir: Directory for per-issue worker logs.
            runs_log: Path to the append-only run log.
            spawn: Worker-spawn function; injectable for tests.
            sleep: Sleep function; injectable for tests.
        """
        self._config = config
        self._trigger = trigger
        self._worktrees = worktrees
        self._logs_dir = logs_dir
        self._runs_log = runs_log
        self._spawn = spawn
        self._sleep = sleep
        self._workers: dict[int, _RunningWorker] = {}
        # Issues reaped this session whose card has not yet been observed
        # outside the trigger column or closed. The scheduler treats these as
        # still in-flight, so a just-finished worker is not immediately
        # re-dispatched while its card is still sitting in the trigger column.
        # A `maestro run` restart starts the set empty by construction, which
        # preserves the documented rework path of dropping a card back in.
        self._recently_dispatched: set[int] = set()
        # Issues already logged as held by the failure cap this session. The
        # streak is durable in the run log, so without this de-duplication the
        # warning would repeat every poll; an issue drops out the moment it is
        # no longer capped, so a later re-cap warns afresh.
        self._capped_logged: set[int] = set()

    def tick(self) -> None:
        """Run one cycle: poll, then reap and dispatch against that board.

        The board is polled first so the reap step can classify finished
        workers by the column their card now sits in (``review_column`` →
        ``done``, ``blocked_column`` → ``blocked``, anywhere else →
        ``failed``). Issues whose worker has finished but whose card is still
        in the trigger column are held in :attr:`_recently_dispatched` and
        counted as in-flight, so they are not immediately re-dispatched. The
        guard releases as soon as the card is observed outside the trigger
        column or the issue closes.
        """
        board = self._trigger.poll()
        in_trigger = {issue.number for issue in board.issues if issue.in_trigger_column}
        self._recently_dispatched = {
            n for n in self._recently_dispatched if n in in_trigger and n in board.open_numbers
        }
        self._reap(board)
        in_flight = set(self._workers) | self._recently_dispatched
        failure_counts = consecutive_failures(read_runs(self._runs_log))
        self._log_failure_caps(board, in_flight, failure_counts)
        ready = dispatchable_issues(
            board.issues,
            board.open_numbers,
            in_flight,
            self._config.max_concurrent,
            failure_counts,
            self._config.max_consecutive_failures,
        )
        for issue in ready:
            self._dispatch(issue.number)

    def _log_failure_caps(
        self, board: BoardState, in_flight: set[int], failure_counts: dict[int, int]
    ) -> None:
        """Warn once per issue the consecutive-failure cap is holding back.

        A capped issue is one that is in the trigger column, still open, not in
        flight, and whose failure streak has reached ``max_consecutive_failures``
        — it would otherwise dispatch. It is logged a single time per session
        (the streak is durable in the run log, so an undeduplicated warning would
        repeat every poll), and re-logged if it later re-caps after a clean run.
        The card is deliberately left untouched: the run loop never writes to
        GitHub, so an operator clears the cap by fixing the issue and moving its
        card, which resets the streak on the next clean run.

        Args:
            board: The board state from this tick's poll.
            in_flight: Issue numbers counted as having a live worker this tick.
            failure_counts: Per-issue consecutive-failure streaks from the run log.
        """
        capped: set[int] = set()
        for issue in board.issues:
            if (
                not issue.in_trigger_column
                or issue.number not in board.open_numbers
                or issue.number in in_flight
            ):
                continue
            reason = failure_cap_reason(
                issue, failure_counts, self._config.max_consecutive_failures
            )
            if reason is None:
                continue
            capped.add(issue.number)
            if issue.number not in self._capped_logged:
                logger.warning("Holding issue #%d — %s", issue.number, reason)
        self._capped_logged = capped

    def run(self) -> None:
        """Tick forever, sleeping between cycles, until interrupted.

        A failed board poll — for example, a transient GitHub rate-limit
        error — is logged and skipped; the loop retries on the next cycle
        rather than terminating.
        """
        logger.info("Maestro loop starting; polling every %ss", self._config.poll_interval)
        try:
            while True:
                try:
                    self.tick()
                except GitHubError as err:
                    logger.warning("Board poll failed; retrying next cycle: %s", err)
                self._sleep(self._config.poll_interval)
        except KeyboardInterrupt:
            logger.info(
                "Maestro loop stopped; %d worker(s) still in flight",
                len(self._workers),
            )

    def _dispatch(self, issue: int) -> None:
        """Create a worktree and spawn a worker for ``issue``."""
        try:
            worktree = self._worktrees.create(issue)
        except WorktreeError as err:
            logger.warning("Skipping issue #%d — worktree setup failed: %s", issue, err)
            return
        command = build_worker_command(self._config.worker_prompt, issue)
        process = self._spawn(command, worktree, self._logs_dir / f"issue-{issue}.log")
        self._workers[issue] = _RunningWorker(
            issue=issue, worktree=worktree, process=process, started=_utc_now()
        )
        logger.info("Dispatched worker for issue #%d", issue)

    def _reap(self, board: BoardState) -> None:
        """Record and clear every worker whose process has exited.

        The freshly polled ``board`` is the source of truth for the run's
        outcome: the column the card now sits in classifies the run, not the
        exit code. A worker that exits without parking its card — even with
        exit code 0 — is a contract violation and is recorded as ``failed``.

        Args:
            board: The board state from this tick's poll.
        """
        status_by_issue = {issue.number: issue.status_column for issue in board.issues}
        for issue, worker in list(self._workers.items()):
            code = worker.process.poll()
            if code is None:
                continue
            outcome = classify_outcome(
                issue,
                code,
                status_by_issue,
                board.open_numbers,
                self._config.review_column,
                self._config.blocked_column,
            )
            column = status_by_issue.get(issue)
            append_run(
                self._runs_log,
                RunRecord(
                    issue=issue,
                    started=worker.started,
                    ended=_utc_now(),
                    outcome=outcome,
                    pr_url=None,
                ),
            )
            if outcome in {"done", "blocked"}:
                self._remove_worktree(worker.worktree)
            else:
                logger.warning(
                    "Worker for issue #%d failed (exit %d, column %r); worktree kept at %s",
                    issue,
                    code,
                    column,
                    worker.worktree,
                )
            del self._workers[issue]
            self._recently_dispatched.add(issue)
            logger.info(
                "Reaped worker for issue #%d (%s, column %r)",
                issue,
                outcome,
                column,
            )

    def _remove_worktree(self, worktree: Path) -> None:
        """Remove a finished worker's worktree, tolerating failure."""
        try:
            self._worktrees.remove(worktree)
        except WorktreeError as err:
            logger.warning("Could not remove worktree %s: %s", worktree, err)
