"""Locate a worker's full run log for debugging.

Each dispatch truncates the per-issue log file (``issue-<N>.log``) before the
worker starts, so the file holds exactly the latest run's complete output —
Claude's raw ``stream-json`` trace. ``maestro logs <issue>`` dumps that file
verbatim so an operator can read it or paste it to Claude when a run goes
wrong; ``status --watch`` renders a summarised live view and is left untouched.

This module is a thin filesystem helper: it returns the log text (or ``None``
when absent) and lets the CLI render it, keeping screen I/O out of the core.
"""

from __future__ import annotations

from pathlib import Path


def log_path_for(logs_dir: Path, issue: int) -> Path:
    """Return the per-issue log path for ``issue`` under ``logs_dir``.

    Args:
        logs_dir: Directory holding the per-issue log files.
        issue: The issue number whose log path to build.

    Returns:
        The ``issue-<N>.log`` path — the same name dispatch writes to.
    """
    return logs_dir / f"issue-{issue}.log"


def read_log(logs_dir: Path, issue: int) -> str | None:
    """Return the complete log text for ``issue``'s most recent run.

    The per-issue log is truncated on each dispatch, so its contents are
    exactly the latest run. Undecodable bytes are replaced rather than raised
    so a partially written or corrupt log still dumps.

    Args:
        logs_dir: Directory holding the per-issue log files.
        issue: The issue number whose latest-run log to read.

    Returns:
        The full log text, or ``None`` if no log exists for that issue.
    """
    path = log_path_for(logs_dir, issue)
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8", errors="replace")
