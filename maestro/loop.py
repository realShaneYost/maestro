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
from maestro.core.models import RunRecord
from maestro.core.scheduler import dispatchable_issues
from maestro.core.trigger import TriggerSource
from maestro.dispatch import build_worker_command, spawn_worker
from maestro.github import GitHubError
from maestro.runlog import append_run
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

    def tick(self) -> None:
        """Run one cycle: reap finished workers, then poll and dispatch.

        Issues whose worker has finished but whose card is still in the
        trigger column are held in :attr:`_recently_dispatched` and counted
        as in-flight, so they are not immediately re-dispatched. The guard
        releases as soon as the card is observed outside the trigger column
        or the issue closes.
        """
        self._reap()
        board = self._trigger.poll()
        in_trigger = {issue.number for issue in board.issues if issue.in_trigger_column}
        self._recently_dispatched = {
            n for n in self._recently_dispatched if n in in_trigger and n in board.open_numbers
        }
        in_flight = set(self._workers) | self._recently_dispatched
        ready = dispatchable_issues(
            board.issues, board.open_numbers, in_flight, self._config.max_concurrent
        )
        for issue in ready:
            self._dispatch(issue.number)

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

    def _reap(self) -> None:
        """Record and clear every worker whose process has exited."""
        for issue, worker in list(self._workers.items()):
            code = worker.process.poll()
            if code is None:
                continue
            outcome = "done" if code == 0 else "failed"
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
            if outcome == "done":
                self._remove_worktree(worker.worktree)
            else:
                logger.warning(
                    "Worker for issue #%d failed (exit %d); worktree kept at %s",
                    issue,
                    code,
                    worker.worktree,
                )
            del self._workers[issue]
            self._recently_dispatched.add(issue)
            logger.info("Reaped worker for issue #%d (%s)", issue, outcome)

    def _remove_worktree(self, worktree: Path) -> None:
        """Remove a finished worker's worktree, tolerating failure."""
        try:
            self._worktrees.remove(worktree)
        except WorktreeError as err:
            logger.warning("Could not remove worktree %s: %s", worktree, err)
