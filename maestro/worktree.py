"""Git worktree lifecycle management.

Maestro gives every dispatched worker its own git worktree so parallel workers
never share a working tree. A worktree is disposable scratch space: it is cut
from ``origin/main`` in a detached-HEAD state, the worker creates its own branch
inside it, and the worktree is removed once the worker is done.

Maestro's entire git surface lives here — managing worktrees (``add``,
``remove``, ``list``, ``prune``) and deleting the local branches of merged
work. It never merges, rebases, or commits.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# A runner takes a git argument list and returns captured stdout. Injectable so
# tests can substitute a fake; the default really runs git.
GitRunner = Callable[[list[str]], str]


class WorktreeError(RuntimeError):
    """Raised when a git worktree operation fails."""


def _run_git(args: list[str]) -> str:
    """Run ``git`` with ``args`` and return its stdout.

    Raises:
        WorktreeError: If git exits with a non-zero status.
    """
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as err:
        raise WorktreeError(f"git {' '.join(args)} failed: {err.stderr.strip()}") from err
    return result.stdout


@dataclass(frozen=True)
class Worktree:
    """A git worktree as reported by ``git worktree list``.

    Attributes:
        path: Absolute path to the worktree's working directory.
        branch: The checked-out branch, or ``None`` for a detached HEAD.
    """

    path: Path
    branch: str | None


def _parse_worktree_list(porcelain: str) -> list[Worktree]:
    """Parse ``git worktree list --porcelain`` output into Worktree records."""
    worktrees: list[Worktree] = []
    for block in porcelain.strip().split("\n\n"):
        if not block.strip():
            continue
        path: Path | None = None
        branch: str | None = None
        for line in block.splitlines():
            if line.startswith("worktree "):
                path = Path(line[len("worktree ") :])
            elif line.startswith("branch "):
                branch = line[len("branch ") :].removeprefix("refs/heads/")
        if path is not None:
            worktrees.append(Worktree(path=path, branch=branch))
    return worktrees


class WorktreeManager:
    """Creates, lists, and removes worktrees of one target repository."""

    def __init__(
        self,
        repo_path: Path,
        worktrees_dir: Path,
        runner: GitRunner = _run_git,
    ) -> None:
        """Initialise the manager.

        Args:
            repo_path: Path to the target git repository.
            worktrees_dir: Directory under which per-issue worktrees are created.
            runner: Callable that runs git; injectable for tests.
        """
        self._repo = repo_path
        self._worktrees_dir = worktrees_dir.resolve()
        self._run = runner

    def create(self, issue_number: int) -> Path:
        """Create a detached worktree off ``origin/main`` for ``issue_number``.

        Fetches ``origin`` first so the worktree is cut from current main. The
        worktree starts detached; the worker creates its own branch inside it.
        The caller must ensure no worktree for this issue already exists.

        Args:
            issue_number: The issue this worktree will host work for.

        Returns:
            The absolute path of the new worktree.
        """
        self._worktrees_dir.mkdir(parents=True, exist_ok=True)
        path = self._worktrees_dir / f"issue-{issue_number}"
        self._run(["-C", str(self._repo), "fetch", "origin", "--quiet"])
        self._run(
            [
                "-C",
                str(self._repo),
                "worktree",
                "add",
                "--detach",
                str(path),
                "origin/main",
            ]
        )
        return path

    def remove(self, path: Path) -> None:
        """Remove the worktree at ``path``, discarding any uncommitted changes."""
        self._run(["-C", str(self._repo), "worktree", "remove", "--force", str(path)])

    def list_managed(self) -> list[Worktree]:
        """Return the worktrees Maestro manages (those under its worktrees dir)."""
        out = self._run(["-C", str(self._repo), "worktree", "list", "--porcelain"])
        return [
            wt for wt in _parse_worktree_list(out) if wt.path.is_relative_to(self._worktrees_dir)
        ]

    def prune(self) -> None:
        """Prune administrative files for worktrees whose directory is gone."""
        self._run(["-C", str(self._repo), "worktree", "prune"])

    def delete_merged_branches(self) -> list[str]:
        """Delete local branches whose upstream is gone (merged, remote-deleted).

        ``main`` is never deleted. A branch still checked out in a worktree
        cannot be deleted, so callers should remove worktrees first.

        Returns:
            The names of the branches that were deleted.
        """
        listing = self._run(
            [
                "-C",
                str(self._repo),
                "for-each-ref",
                "refs/heads",
                "--format",
                "%(refname:short) %(upstream:track)",
            ]
        )
        gone = [
            parts[0]
            for parts in (line.split() for line in listing.splitlines())
            if len(parts) == 2 and parts[1] == "[gone]" and parts[0] != "main"
        ]
        for branch in gone:
            self._run(["-C", str(self._repo), "branch", "-D", branch])
        return gone
