"""Cleanup of Maestro's runtime artifacts.

``maestro clean`` is meant to be run while the ``run`` loop is stopped. By
default it is conservative: it removes the disposable worktrees and the local
branches whose PRs have merged, but leaves the audit trail — the logs and the
run log — untouched. Explicit options opt into clearing those too.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from maestro.worktree import WorktreeManager


@dataclass(frozen=True)
class CleanReport:
    """A summary of what a clean operation removed.

    Attributes:
        worktrees_removed: Paths of the worktrees that were removed.
        branches_deleted: Names of the merged local branches that were deleted.
        logs_cleared: Whether the logs directory was cleared.
        history_cleared: Whether the run log was truncated.
    """

    worktrees_removed: list[Path]
    branches_deleted: list[str]
    logs_cleared: bool
    history_cleared: bool


def _clear_logs(logs_dir: Path) -> None:
    """Delete every ``*.log`` file in ``logs_dir``."""
    if not logs_dir.is_dir():
        return
    for log_file in logs_dir.glob("*.log"):
        log_file.unlink()


def clean(
    worktrees: WorktreeManager,
    logs_dir: Path,
    runs_log: Path,
    *,
    clear_logs: bool = False,
    clear_history: bool = False,
) -> CleanReport:
    """Remove Maestro's disposable artifacts and report what was removed.

    Always removes every managed worktree and every merged local branch.
    Optionally also clears the logs directory and truncates the run log.

    Args:
        worktrees: Manager for the target repo's worktrees.
        logs_dir: Directory holding per-issue log files.
        runs_log: Path to the ``runs.jsonl`` run log.
        clear_logs: If true, delete every log file in ``logs_dir``.
        clear_history: If true, truncate ``runs_log``.

    Returns:
        A :class:`CleanReport` describing what was removed.
    """
    removed: list[Path] = []
    for worktree in worktrees.list_managed():
        worktrees.remove(worktree.path)
        removed.append(worktree.path)
    worktrees.prune()
    branches = worktrees.delete_merged_branches()

    if clear_logs:
        _clear_logs(logs_dir)
    if clear_history and runs_log.is_file():
        runs_log.write_text("", encoding="utf-8")

    return CleanReport(
        worktrees_removed=removed,
        branches_deleted=branches,
        logs_cleared=clear_logs,
        history_cleared=clear_history,
    )
