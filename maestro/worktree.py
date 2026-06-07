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

import contextlib
import logging
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# A runner takes a git argument list and returns captured stdout. Injectable so
# tests can substitute a fake; the default really runs git.
GitRunner = Callable[[list[str]], str]

# A setup runner runs a shell command in a worktree to provision its
# environment. Injectable so tests can substitute a fake; the default really
# runs the command.
SetupRunner = Callable[[str, Path], None]


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


def _run_setup(command: str, cwd: Path) -> None:
    """Run a worktree setup ``command`` in ``cwd`` through the shell.

    Args:
        command: A shell command line that provisions the worktree's
            environment, typically by creating a virtualenv.
        cwd: The worktree directory to run the command in.

    Raises:
        WorktreeError: If the command exits with a non-zero status.
    """
    try:
        subprocess.run(
            command,
            shell=True,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=True,
        )
    except subprocess.CalledProcessError as err:
        raise WorktreeError(f"worktree setup command failed: {err.stderr.strip()}") from err


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
        *,
        worktree_setup: str | None = None,
        setup_runner: SetupRunner = _run_setup,
    ) -> None:
        """Initialise the manager.

        Args:
            repo_path: Path to the target git repository.
            worktrees_dir: Directory under which per-issue worktrees are created.
            runner: Callable that runs git; injectable for tests.
            worktree_setup: Optional shell command run inside each fresh
                worktree to provision its environment. When ``None``, the
                target repo's ``.venv`` is symlinked in as a fallback.
            setup_runner: Callable that runs the ``worktree_setup`` command;
                injectable for tests.
        """
        self._repo = repo_path
        self._worktrees_dir = worktrees_dir.resolve()
        self._run = runner
        self._worktree_setup = worktree_setup
        self._setup_runner = setup_runner

    def create(self, issue_number: int) -> Path:
        """Create a detached worktree off ``origin/main`` for ``issue_number``.

        Fetches ``origin`` first so the worktree is cut from current main. The
        worktree starts detached; the worker creates its own branch inside it.
        Its environment is then provisioned so quality-gate tools work (see
        :meth:`_provision_env`). If a managed worktree for this issue already
        exists at the target path — typically a kept-for-debugging worktree
        from a previous failed run — it is force-removed first and a WARNING
        is logged: by the time the operator has re-triggered the card, the
        debugging window for the stale worktree is over by definition.

        Args:
            issue_number: The issue this worktree will host work for.

        Returns:
            The absolute path of the new worktree.

        Raises:
            WorktreeError: If a git operation or the ``worktree_setup``
                command fails.
        """
        self._worktrees_dir.mkdir(parents=True, exist_ok=True)
        path = self._worktrees_dir / f"issue-{issue_number}"
        if path.exists():
            logger.warning("Discarding stale worktree at %s before fresh dispatch", path)
            with contextlib.suppress(WorktreeError):
                self.remove(path)
            self.prune()
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
        self._provision_env(path)
        return path

    def _provision_env(self, worktree: Path) -> None:
        """Provision a fresh worktree so the worker's quality-gate tools work.

        When a ``worktree_setup`` command is configured, it is run inside the
        worktree — typically to build an isolated virtualenv. Otherwise the
        target repo's ``.venv`` is symlinked in as a fallback.

        A configured command that fails leaves nothing behind: the
        half-prepared worktree is removed before the error propagates, so the
        dispatch can be retried cleanly on a later poll.

        Args:
            worktree: The freshly created worktree directory.

        Raises:
            WorktreeError: If the ``worktree_setup`` command exits non-zero.
        """
        if self._worktree_setup is None:
            self._link_venv(worktree)
            return
        try:
            self._setup_runner(self._worktree_setup, worktree)
        except WorktreeError:
            with contextlib.suppress(WorktreeError):
                self.remove(worktree)
            raise

    def _link_venv(self, worktree: Path) -> None:
        """Symlink the target repo's ``.venv`` into ``worktree``.

        The fallback used when no ``worktree_setup`` command is configured. A
        fresh worktree omits ``.venv`` (it is gitignored), so a worker would
        have no quality-gate tools; the symlink restores them. Best-effort: if
        the repo has no ``.venv``, nothing is linked.
        """
        source = self._repo / ".venv"
        link = worktree / ".venv"
        if source.is_dir() and not link.exists():
            link.symlink_to(source, target_is_directory=True)

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
