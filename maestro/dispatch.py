"""Worker dispatch — spawn a headless coding agent for one issue.

Each worker is a separate process running in its own worktree. Maestro builds
the worker command from the configured prompt template, starts the process
with its combined output captured to a per-issue log file, and hands back the
process handle. Workers are fire-and-forget: Maestro never talks to a running
worker, it only waits for the process to exit.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

# The coding-agent CLI Maestro invokes for each worker.
WORKER_AGENT = "claude"


def build_worker_command(worker_prompt: str, issue_number: int) -> list[str]:
    """Build the argv for a worker process.

    The worker runs headless and unattended, so it is spawned with flags that
    let it work autonomously: ``bypassPermissions`` lets the worker run its
    ``gh``/``git`` commands without a human to approve each one (``acceptEdits``
    auto-approves only file edits, not Bash, so a worker under it blocks on the
    first ``gh`` call and dies). The grant is safe by construction — each worker
    runs in an isolated git worktree, never merges (its authority ends at
    PR-open), and a human reviews every PR. ``stream-json`` output emits the
    full agentic trace so the run is observable even if it aborts.

    Args:
        worker_prompt: Prompt template containing the ``{issue}`` placeholder.
        issue_number: The issue the worker will run.

    Returns:
        The argv list for the headless agent invocation.
    """
    prompt = worker_prompt.format(issue=issue_number)
    return [
        WORKER_AGENT,
        "-p",
        prompt,
        "--permission-mode",
        "bypassPermissions",
        "--output-format",
        "stream-json",
        "--verbose",
    ]


def spawn_worker(command: list[str], cwd: Path, log_path: Path) -> subprocess.Popen[bytes]:
    """Spawn ``command`` in ``cwd`` with output captured to ``log_path``.

    The log file is truncated on open, so it holds only the latest dispatch;
    the cross-run record is ``runs.jsonl``. The child receives its own
    duplicated file descriptor, so the parent's handle is closed immediately
    without affecting the worker.

    Args:
        command: The worker argv, e.g. from :func:`build_worker_command`.
        cwd: Working directory for the worker — its prepared worktree.
        log_path: File to capture the worker's combined stdout and stderr.

    Returns:
        The started process handle. The caller owns waiting on it.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as log_file:
        process: subprocess.Popen[bytes] = subprocess.Popen(
            command,
            cwd=str(cwd),
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
    return process
