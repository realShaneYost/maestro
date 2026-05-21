"""Worker status — reconstruct and render what workers are doing.

``status`` derives everything from artifacts already on disk: the git worktrees
Maestro manages (which issues are in flight) and the per-issue log files (what
each worker is doing). It keeps no state of its own, so it works from any
terminal and even after the ``run`` loop has crashed.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.live import Live
from rich.table import Table

from maestro.worktree import WorktreeManager


@dataclass(frozen=True)
class WorkerStatus:
    """A single worker's status as reconstructed from disk.

    Attributes:
        issue: The issue number the worker is handling.
        last_activity: Seconds since the worker's log was last written, or
            ``None`` if it has no log yet.
        last_line: The last non-empty line of the worker's log, or "" if none.
    """

    issue: int
    last_activity: float | None
    last_line: str


def _issue_from_worktree(path: Path) -> int | None:
    """Extract the issue number from a worktree path named ``issue-N``."""
    name = path.name
    if not name.startswith("issue-"):
        return None
    suffix = name[len("issue-") :]
    return int(suffix) if suffix.isdigit() else None


def _read_log_tail(log_path: Path, now: float) -> tuple[float | None, str]:
    """Return (seconds since last write, last non-empty line) for a log file."""
    if not log_path.is_file():
        return None, ""
    age = now - log_path.stat().st_mtime
    lines = [
        line
        for line in log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    ]
    return age, (lines[-1] if lines else "")


def collect_statuses(
    worktrees: WorktreeManager, logs_dir: Path, now: float | None = None
) -> list[WorkerStatus]:
    """Reconstruct the status of every in-flight worker.

    Args:
        worktrees: Manager for the target repo's worktrees.
        logs_dir: Directory holding per-issue log files.
        now: Current epoch time; defaults to ``time.time()`` (injectable for
            tests).

    Returns:
        One :class:`WorkerStatus` per managed worktree, sorted by issue number.
    """
    moment = time.time() if now is None else now
    statuses: list[WorkerStatus] = []
    for worktree in worktrees.list_managed():
        issue = _issue_from_worktree(worktree.path)
        if issue is None:
            continue
        last_activity, last_line = _read_log_tail(logs_dir / f"issue-{issue}.log", moment)
        statuses.append(WorkerStatus(issue=issue, last_activity=last_activity, last_line=last_line))
    return sorted(statuses, key=lambda status: status.issue)


def _humanize(seconds: float | None) -> str:
    """Render a seconds delta as a short human string, e.g. ``3m ago``."""
    if seconds is None:
        return "—"
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    return f"{int(seconds // 3600)}h ago"


def _truncate(text: str, width: int) -> str:
    """Truncate ``text`` to ``width`` characters, adding an ellipsis if cut."""
    if len(text) <= width:
        return text
    return text[: width - 1] + "…"


def _status_table(statuses: list[WorkerStatus], max_concurrent: int) -> Table:
    """Build the rich table that ``status`` renders."""
    table = Table(
        title=f"Maestro — {len(statuses)} worker(s) in flight (max {max_concurrent})",
        title_style="header",
    )
    table.add_column("Issue", justify="right", style="running")
    table.add_column("Last activity", justify="right", style="queued")
    table.add_column("Latest")
    if not statuses:
        table.add_row("—", "—", "no workers in flight")
        return table
    for status in statuses:
        table.add_row(
            f"#{status.issue}",
            _humanize(status.last_activity),
            _truncate(status.last_line, 80),
        )
    return table


def render_once(
    console: Console,
    worktrees: WorktreeManager,
    logs_dir: Path,
    max_concurrent: int,
) -> None:
    """Collect worker status and print the table once."""
    statuses = collect_statuses(worktrees, logs_dir)
    console.print(_status_table(statuses, max_concurrent))


def watch(
    console: Console,
    worktrees: WorktreeManager,
    logs_dir: Path,
    max_concurrent: int,
    interval: float = 2.0,
) -> None:
    """Render a live-refreshing status view until interrupted with Ctrl-C."""
    try:
        with Live(console=console, refresh_per_second=4, screen=False) as live:
            while True:
                statuses = collect_statuses(worktrees, logs_dir)
                live.update(_status_table(statuses, max_concurrent))
                time.sleep(interval)
    except KeyboardInterrupt:
        pass
