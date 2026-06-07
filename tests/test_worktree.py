"""Tests for git worktree lifecycle management."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from maestro.worktree import WorktreeError, WorktreeManager, _parse_worktree_list


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Create a git repo with an 'origin' remote and a committed main branch."""
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(remote)],
        check=True,
        capture_output=True,
    )
    local = tmp_path / "local"
    subprocess.run(["git", "clone", str(remote), str(local)], check=True, capture_output=True)
    _git(local, "config", "user.email", "test@example.com")
    _git(local, "config", "user.name", "Test")
    (local / "README.md").write_text("hello\n", encoding="utf-8")
    _git(local, "add", "README.md")
    _git(local, "commit", "-m", "initial")
    _git(local, "push", "origin", "main")
    return local


def test_parse_worktree_list_handles_branch_and_detached():
    porcelain = (
        "worktree /repo\nHEAD abc\nbranch refs/heads/main\n\n"
        "worktree /repo/wt/issue-1\nHEAD def\ndetached\n"
    )
    result = _parse_worktree_list(porcelain)
    assert [(str(w.path), w.branch) for w in result] == [
        ("/repo", "main"),
        ("/repo/wt/issue-1", None),
    ]


def test_create_makes_a_detached_worktree(repo, tmp_path):
    manager = WorktreeManager(repo, tmp_path / "worktrees")
    path = manager.create(42)
    assert path.is_dir()
    assert (path / "README.md").is_file()
    managed = manager.list_managed()
    assert [w.path for w in managed] == [path]
    assert managed[0].branch is None  # detached HEAD


def test_create_symlinks_venv_when_present(repo, tmp_path):
    (repo / ".venv").mkdir()
    path = WorktreeManager(repo, tmp_path / "worktrees").create(11)
    venv_link = path / ".venv"
    assert venv_link.is_symlink()
    assert venv_link.resolve() == (repo / ".venv").resolve()


def test_remove_deletes_the_worktree(repo, tmp_path):
    manager = WorktreeManager(repo, tmp_path / "worktrees")
    path = manager.create(7)
    assert path.is_dir()
    manager.remove(path)
    assert not path.exists()
    assert manager.list_managed() == []


def test_list_managed_excludes_the_primary_worktree(repo, tmp_path):
    manager = WorktreeManager(repo, tmp_path / "worktrees")
    manager.create(1)
    paths = [w.path for w in manager.list_managed()]
    assert repo not in paths
    assert len(paths) == 1


def test_worktree_setup_runs_instead_of_the_symlink(repo, tmp_path):
    (repo / ".venv").mkdir()
    calls: list[tuple[str, Path]] = []
    manager = WorktreeManager(
        repo,
        tmp_path / "worktrees",
        worktree_setup="provision-env",
        setup_runner=lambda command, cwd: calls.append((command, cwd)),
    )
    path = manager.create(5)
    assert calls == [("provision-env", path)]
    assert not (path / ".venv").exists()  # setup ran; the symlink was skipped


def test_failed_worktree_setup_raises_and_removes_the_worktree(repo, tmp_path):
    def boom(command: str, cwd: Path) -> None:
        raise WorktreeError("setup blew up")

    worktrees_dir = tmp_path / "worktrees"
    manager = WorktreeManager(
        repo, worktrees_dir, worktree_setup="provision-env", setup_runner=boom
    )
    with pytest.raises(WorktreeError, match="setup blew up"):
        manager.create(9)
    assert not (worktrees_dir / "issue-9").exists()
    assert manager.list_managed() == []


def test_create_discards_stale_worktree_at_target_path(repo, tmp_path, caplog):
    """A leftover worktree from a previous failed run is discarded and re-created."""
    manager = WorktreeManager(repo, tmp_path / "worktrees")
    first = manager.create(42)
    # Touch a file inside the stale worktree so we can verify it was discarded.
    sentinel = first / "stale-marker.txt"
    sentinel.write_text("leftover", encoding="utf-8")
    with caplog.at_level("WARNING", logger="maestro.worktree"):
        second = manager.create(42)
    assert second == first
    assert not sentinel.exists()
    assert any("stale" in record.message.lower() for record in caplog.records)
    assert manager.list_managed() == [wt for wt in manager.list_managed() if wt.path == second]
