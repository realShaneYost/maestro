"""Tests for ``maestro add`` — one-step add of an issue to the board."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from maestro.add import add_issue_to_board
from maestro.config import Config

GhRunner = Callable[[list[str]], tuple[int, str, str]]


def _config(tmp_path: Path) -> Config:
    return Config(
        repo_path=tmp_path,
        repo_slug="owner/repo",
        project_number=7,
        trigger_column="In Progress",
        max_concurrent=2,
        poll_interval=60,
        worker_prompt="/cc-work {issue}",
        theme="nord",
        worktree_setup=None,
        review_column="In Review",
        blocked_column="Blocked",
    )


def _field_ids_payload(columns: list[str] | None, *, project_id: str, field_id: str) -> str:
    """Render the field-IDs GraphQL response for a board with these columns.

    ``columns`` of ``None`` models a board with no Status field. Each option's
    ID is derived from its name so a test can assert the live ID was used.
    """
    nodes: list[dict[str, object]] = []
    if columns is not None:
        nodes.append(
            {
                "id": field_id,
                "name": "Status",
                "options": [{"id": f"opt-{name}", "name": name} for name in columns],
            }
        )
    return json.dumps(
        {"data": {"repositoryOwner": {"projectV2": {"id": project_id, "fields": {"nodes": nodes}}}}}
    )


def _make_runner(
    *,
    columns: list[str] | None,
    project_id: str = "proj-1",
    field_id: str = "field-status",
    item_id: str = "item-99",
    add_code: int = 0,
    edit_code: int = 0,
) -> tuple[GhRunner, list[list[str]]]:
    """Build a gh runner dispatching on request shape, recording every call."""
    calls: list[list[str]] = []

    def run(args: list[str]) -> tuple[int, str, str]:
        calls.append(list(args))
        if args[:2] == ["api", "graphql"]:
            return 0, _field_ids_payload(columns, project_id=project_id, field_id=field_id), ""
        if args[:2] == ["project", "item-add"]:
            if add_code != 0:
                return add_code, "", "add failed"
            return 0, json.dumps({"id": item_id}), ""
        if args[:2] == ["project", "item-edit"]:
            return edit_code, "", "edit failed" if edit_code else ""
        return 0, "", ""

    return run, calls


def test_add_defaults_to_leftmost_column(tmp_path):
    runner, calls = _make_runner(columns=["Todo", "In Progress", "In Review", "Blocked"])
    report = add_issue_to_board(_config(tmp_path), 42, gh_runner=runner)
    assert report.column == "Todo"
    assert report.added is True
    assert report.status_set is True
    assert report.aborted is None
    edit = next(c for c in calls if c[:2] == ["project", "item-edit"])
    assert "opt-Todo" in edit
    assert "field-status" in edit
    assert "proj-1" in edit
    assert "item-99" in edit


def test_add_honors_explicit_status(tmp_path):
    runner, calls = _make_runner(columns=["Todo", "In Progress", "In Review", "Blocked"])
    report = add_issue_to_board(_config(tmp_path), 42, status="In Review", gh_runner=runner)
    assert report.column == "In Review"
    assert report.status_set is True
    edit = next(c for c in calls if c[:2] == ["project", "item-edit"])
    assert "opt-In Review" in edit


def test_add_builds_issue_url_from_slug(tmp_path):
    runner, calls = _make_runner(columns=["Todo"])
    add_issue_to_board(_config(tmp_path), 42, gh_runner=runner)
    add = next(c for c in calls if c[:2] == ["project", "item-add"])
    assert "https://github.com/owner/repo/issues/42" in add
    assert "7" in add  # project number
    assert "owner" in add  # project owner


def test_add_unknown_status_aborts_before_mutating(tmp_path):
    runner, calls = _make_runner(columns=["Todo", "In Progress"])
    report = add_issue_to_board(_config(tmp_path), 42, status="Nope", gh_runner=runner)
    assert report.added is False
    assert report.status_set is False
    assert report.aborted is not None
    assert "Nope" in report.aborted
    assert report.available_columns == ["Todo", "In Progress"]
    assert not any(c[:2] == ["project", "item-add"] for c in calls)


def test_add_aborts_when_no_status_field(tmp_path):
    runner, calls = _make_runner(columns=None)
    report = add_issue_to_board(_config(tmp_path), 42, gh_runner=runner)
    assert report.added is False
    assert report.aborted is not None
    assert "Status field" in report.aborted
    assert not any(c[:2] == ["project", "item-add"] for c in calls)


def test_add_reports_item_add_failure(tmp_path):
    runner, _calls = _make_runner(columns=["Todo"], add_code=1)
    report = add_issue_to_board(_config(tmp_path), 42, gh_runner=runner)
    assert report.added is False
    assert report.status_set is False
    assert report.aborted is not None
    assert "add failed" in report.aborted


def test_add_reports_status_set_failure(tmp_path):
    runner, _calls = _make_runner(columns=["Todo"], edit_code=1)
    report = add_issue_to_board(_config(tmp_path), 42, gh_runner=runner)
    assert report.added is True
    assert report.status_set is False
    assert report.aborted is not None
    assert "edit failed" in report.aborted
