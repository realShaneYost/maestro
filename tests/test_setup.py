"""Tests for ``maestro setup`` — narrowly scoped board bootstrap."""

from __future__ import annotations

import json
from pathlib import Path

from maestro.config import Config
from maestro.setup import run_setup


def _config(tmp_path: Path) -> Config:
    return Config(
        repo_path=tmp_path,
        repo_slug="owner/repo",
        project_number=1,
        trigger_column="In Progress",
        max_concurrent=2,
        poll_interval=60,
        worker_prompt="/cc-work {issue}",
        theme="nord",
        worktree_setup=None,
        review_column="In Review",
        blocked_column="Blocked",
    )


def _fields_payload(status: list[str], priority_options: list[str] | None) -> str:
    """Render the fields-enumeration response that both lookups now share."""
    nodes: list[dict[str, object]] = []
    if status:
        nodes.append({"name": "Status", "options": [{"name": name} for name in status]})
    if priority_options is not None:
        nodes.append({"name": "Priority", "options": [{"name": name} for name in priority_options]})
    return json.dumps({"data": {"repositoryOwner": {"projectV2": {"fields": {"nodes": nodes}}}}})


def _make_runner(*, status: list[str], priority_options: list[str] | None):
    """Build a gh runner whose responses depend on the request shape."""
    created: list[list[str]] = []
    state: dict[str, list[str] | None] = {"priority_options": priority_options}

    def run(args: list[str]) -> tuple[int, str, str]:
        if args[:2] == ["auth", "status"]:
            return 0, "Token scopes: 'repo', 'project'\n", ""
        if args[:2] == ["project", "field-create"]:
            created.append(list(args))
            state["priority_options"] = ["Critical", "High", "Normal", "Low"]
            return 0, "created\n", ""
        if args[:2] == ["api", "graphql"]:
            return 0, _fields_payload(status, state["priority_options"]), ""
        return 0, "", ""

    return run, created


def test_setup_creates_priority_field_when_absent(tmp_path):
    runner, created = _make_runner(
        status=["Backlog", "In Progress", "In Review", "Blocked"], priority_options=None
    )
    report = run_setup(_config(tmp_path), gh_runner=runner)
    assert report.priority_created is True
    assert report.priority_already_present is False
    assert report.status_present is True
    assert any(
        call[:2] == ["project", "field-create"] and "Critical,High,Normal,Low" in call
        for call in created
    )


def test_setup_skips_priority_when_present(tmp_path):
    runner, created = _make_runner(
        status=["Backlog", "In Progress", "In Review", "Blocked"],
        priority_options=["Critical", "High", "Normal"],
    )
    report = run_setup(_config(tmp_path), gh_runner=runner)
    assert report.priority_created is False
    assert report.priority_already_present is True
    assert created == []


def test_setup_reports_missing_status_options_without_mutating(tmp_path):
    runner, created = _make_runner(
        status=["Backlog", "In Progress"],
        priority_options=["Critical"],
    )
    report = run_setup(_config(tmp_path), gh_runner=runner)
    assert report.status_present is False
    assert "In Review" in report.status_missing
    assert "Blocked" in report.status_missing
    assert "In Progress" not in report.status_missing
    # The runner must NOT have been asked to mutate the Status field.
    assert not any("statusField" in " ".join(call) and "create" in call for call in created)


def test_setup_is_idempotent(tmp_path):
    """Running setup twice mutates nothing on the second run."""
    runner, created = _make_runner(
        status=["Backlog", "In Progress", "In Review", "Blocked"],
        priority_options=None,
    )
    first = run_setup(_config(tmp_path), gh_runner=runner)
    second = run_setup(_config(tmp_path), gh_runner=runner)
    assert first.priority_created is True
    assert second.priority_created is False
    assert second.priority_already_present is True
    # Only one create call total — the second run reused the now-present field.
    assert len(created) == 1


def test_setup_reports_install_info(tmp_path):
    runner, _created = _make_runner(
        status=["Backlog", "In Progress", "In Review", "Blocked"],
        priority_options=["Critical"],
    )
    report = run_setup(_config(tmp_path), gh_runner=runner)
    assert report.install is not None
    assert "maestro" in report.install.summary


def test_setup_reports_install_info_even_when_aborted(tmp_path):
    def runner(args):
        if args[:2] == ["auth", "status"]:
            return 1, "", "not logged in\n"
        return 0, "", ""

    report = run_setup(_config(tmp_path), gh_runner=runner)
    assert report.aborted is not None
    assert report.install is not None


def test_setup_aborts_when_gh_auth_fails(tmp_path):
    def runner(args):
        if args[:2] == ["auth", "status"]:
            return 1, "", "not logged in\n"
        return 0, "", ""

    report = run_setup(_config(tmp_path), gh_runner=runner)
    assert report.aborted is not None
    assert "gh auth" in report.aborted


def test_setup_aborts_when_project_scope_missing(tmp_path):
    def runner(args):
        if args[:2] == ["auth", "status"]:
            return 0, "Token scopes: 'repo'\n", ""
        return 0, "", ""

    report = run_setup(_config(tmp_path), gh_runner=runner)
    assert report.aborted is not None
    assert "project scope" in report.aborted
