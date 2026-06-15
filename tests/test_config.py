"""Tests for Maestro configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from maestro.config import ConfigError, load_config, runtime_paths

_MINIMAL = """\
repo_slug: "octocat/hello-world"
project_number: 7
trigger_column: "In Progress"
"""


def _write(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / "maestro.yml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def test_loads_minimal_config_and_applies_defaults(tmp_path):
    config = load_config(_write(tmp_path, _MINIMAL), repo_path=tmp_path)
    assert config.repo_path == tmp_path
    assert config.repo_slug == "octocat/hello-world"
    assert config.project_number == 7
    assert config.trigger_column == "In Progress"
    assert config.max_concurrent == 2
    assert config.poll_interval == 60
    assert config.worker_prompt == "/cc-work {issue}"
    assert config.theme == "nord"
    assert config.worktree_setup is None
    assert config.review_column == "In Review"
    assert config.blocked_column == "Blocked"
    assert config.manage_ci is False


def test_manage_ci_defaults_off_and_accepts_true(tmp_path):
    assert load_config(_write(tmp_path, _MINIMAL), repo_path=tmp_path).manage_ci is False
    body = _MINIMAL + "manage_ci: true\n"
    assert load_config(_write(tmp_path, body), repo_path=tmp_path).manage_ci is True


def test_manage_ci_must_be_boolean(tmp_path):
    body = _MINIMAL + 'manage_ci: "yes"\n'
    with pytest.raises(ConfigError, match="manage_ci must be a boolean"):
        load_config(_write(tmp_path, body), repo_path=tmp_path)


def test_repo_path_in_file_is_ignored_in_favour_of_caller(tmp_path):
    # A stray repo_path in the file must not override the caller-supplied one.
    body = _MINIMAL + 'repo_path: "/somewhere/else"\n'
    config = load_config(_write(tmp_path, body), repo_path=tmp_path)
    assert config.repo_path == tmp_path


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.yml", repo_path=tmp_path)


def test_missing_required_key_raises(tmp_path):
    with pytest.raises(ConfigError, match="required key"):
        load_config(_write(tmp_path, 'repo_slug: "a/b"\n'), repo_path=tmp_path)


def test_non_mapping_config_raises(tmp_path):
    with pytest.raises(ConfigError, match="mapping"):
        load_config(_write(tmp_path, "- just\n- a\n- list\n"), repo_path=tmp_path)


def test_bad_repo_slug_raises(tmp_path):
    body = _MINIMAL.replace('repo_slug: "octocat/hello-world"', 'repo_slug: "not-a-slug"')
    with pytest.raises(ConfigError, match="owner/name"):
        load_config(_write(tmp_path, body), repo_path=tmp_path)


def test_repo_path_must_be_a_directory(tmp_path):
    with pytest.raises(ConfigError, match="not a directory"):
        load_config(_write(tmp_path, _MINIMAL), repo_path=tmp_path / "does-not-exist")


def test_non_positive_max_concurrent_raises(tmp_path):
    body = _MINIMAL + "max_concurrent: 0\n"
    with pytest.raises(ConfigError, match="positive integer"):
        load_config(_write(tmp_path, body), repo_path=tmp_path)


def test_worker_prompt_must_contain_issue_placeholder(tmp_path):
    body = _MINIMAL + 'worker_prompt: "/cc-work please"\n'
    with pytest.raises(ConfigError, match="placeholder"):
        load_config(_write(tmp_path, body), repo_path=tmp_path)


def test_worktree_setup_is_loaded_when_set(tmp_path):
    body = _MINIMAL + 'worktree_setup: "uv venv .venv"\n'
    config = load_config(_write(tmp_path, body), repo_path=tmp_path)
    assert config.worktree_setup == "uv venv .venv"


def test_empty_worktree_setup_raises(tmp_path):
    body = _MINIMAL + 'worktree_setup: ""\n'
    with pytest.raises(ConfigError, match="worktree_setup must not be empty"):
        load_config(_write(tmp_path, body), repo_path=tmp_path)


def test_empty_review_column_raises(tmp_path):
    body = _MINIMAL + 'review_column: ""\n'
    with pytest.raises(ConfigError, match="review_column must not be empty"):
        load_config(_write(tmp_path, body), repo_path=tmp_path)


def test_empty_blocked_column_raises(tmp_path):
    body = _MINIMAL + 'blocked_column: ""\n'
    with pytest.raises(ConfigError, match="blocked_column must not be empty"):
        load_config(_write(tmp_path, body), repo_path=tmp_path)


def test_duplicate_column_names_raise(tmp_path):
    body = _MINIMAL + 'review_column: "In Progress"\n'
    with pytest.raises(ConfigError, match="distinct"):
        load_config(_write(tmp_path, body), repo_path=tmp_path)


def test_duplicate_review_and_blocked_raise(tmp_path):
    body = _MINIMAL + 'review_column: "Done"\n' + 'blocked_column: "Done"\n'
    with pytest.raises(ConfigError, match="distinct"):
        load_config(_write(tmp_path, body), repo_path=tmp_path)


def test_runtime_paths_are_sibling_of_repo():
    paths = runtime_paths(Path("/repos/astromech"))
    assert paths.root == Path("/repos/astromech.maestro")
    assert paths.worktrees_dir == Path("/repos/astromech.maestro/worktrees")
    assert paths.logs_dir == Path("/repos/astromech.maestro/logs")
    assert paths.runs_log == Path("/repos/astromech.maestro/runs.jsonl")
