"""Tests for Maestro configuration loading and validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from maestro.config import ConfigError, load_config

_MINIMAL = """\
repo_path: "{repo}"
repo_slug: "octocat/hello-world"
project_number: 7
trigger_column: "In Progress"
"""


def _write(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / "maestro.yml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def test_loads_minimal_config_and_applies_defaults(tmp_path):
    config = load_config(_write(tmp_path, _MINIMAL.format(repo=tmp_path)))
    assert config.repo_path == tmp_path
    assert config.repo_slug == "octocat/hello-world"
    assert config.project_number == 7
    assert config.trigger_column == "In Progress"
    assert config.max_concurrent == 2
    assert config.poll_interval == 60
    assert config.worker_prompt == "/cc-work {issue}"
    assert config.theme == "nord"


def test_missing_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "absent.yml")


def test_missing_required_key_raises(tmp_path):
    with pytest.raises(ConfigError, match="required key"):
        load_config(_write(tmp_path, 'repo_slug: "a/b"\n'))


def test_non_mapping_config_raises(tmp_path):
    with pytest.raises(ConfigError, match="mapping"):
        load_config(_write(tmp_path, "- just\n- a\n- list\n"))


def test_bad_repo_slug_raises(tmp_path):
    body = _MINIMAL.format(repo=tmp_path).replace(
        'repo_slug: "octocat/hello-world"', 'repo_slug: "not-a-slug"'
    )
    with pytest.raises(ConfigError, match="owner/name"):
        load_config(_write(tmp_path, body))


def test_repo_path_must_be_a_directory(tmp_path):
    body = _MINIMAL.format(repo=tmp_path / "does-not-exist")
    with pytest.raises(ConfigError, match="not a directory"):
        load_config(_write(tmp_path, body))


def test_non_positive_max_concurrent_raises(tmp_path):
    body = _MINIMAL.format(repo=tmp_path) + "max_concurrent: 0\n"
    with pytest.raises(ConfigError, match="positive integer"):
        load_config(_write(tmp_path, body))


def test_worker_prompt_must_contain_issue_placeholder(tmp_path):
    body = _MINIMAL.format(repo=tmp_path) + 'worker_prompt: "/cc-work please"\n'
    with pytest.raises(ConfigError, match="placeholder"):
        load_config(_write(tmp_path, body))
