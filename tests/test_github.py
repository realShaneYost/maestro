"""Tests for the GitHub Projects V2 trigger source."""

from __future__ import annotations

import json

import pytest

from maestro.github import (
    GitHubError,
    StatusFieldTrigger,
    _build_issues,
    _parse_open_issues,
    _parse_priority,
)


def test_parse_priority_maps_p_levels():
    assert _parse_priority("P0") == 0
    assert _parse_priority("P1") == 1
    assert _parse_priority("P10") == 10
    assert _parse_priority("p2") == 2


def test_parse_priority_unset_or_invalid_is_none():
    assert _parse_priority(None) is None
    assert _parse_priority("") is None
    assert _parse_priority("high") is None
    assert _parse_priority(3) is None


def test_parse_open_issues_extracts_numbers_and_dependencies():
    payload = {
        "data": {
            "repository": {
                "issues": {
                    "nodes": [
                        {"number": 1, "blockedBy": {"nodes": []}},
                        {"number": 2, "blockedBy": {"nodes": [{"number": 1}]}},
                    ]
                }
            }
        }
    }
    open_numbers, blocked_by = _parse_open_issues(payload)
    assert open_numbers == {1, 2}
    assert blocked_by[1] == frozenset()
    assert blocked_by[2] == frozenset({1})


def test_parse_open_issues_rejects_unexpected_payload():
    with pytest.raises(GitHubError, match="Unexpected"):
        _parse_open_issues({"data": {}})


def test_build_issues_skips_pull_requests_and_sets_fields():
    items = [
        {
            "content": {"type": "Issue", "number": 5, "title": "Real issue"},
            "status": "In Progress",
            "priority": "P1",
        },
        {
            "content": {"type": "PullRequest", "number": 6, "title": "a PR"},
            "status": "In Progress",
        },
        {
            "content": {"type": "Issue", "number": 7, "title": "Backlog issue"},
            "status": "Backlog",
        },
    ]
    issues = _build_issues(items, "In Progress", {5: frozenset({3})})
    assert [i.number for i in issues] == [5, 7]
    assert issues[0].in_trigger_column is True
    assert issues[0].priority == 1
    assert issues[0].blocked_by == frozenset({3})
    assert issues[1].in_trigger_column is False
    assert issues[1].priority is None


def test_poll_combines_board_and_dependencies():
    project_json = json.dumps(
        {
            "items": [
                {
                    "content": {"type": "Issue", "number": 9, "title": "Nine"},
                    "status": "In Progress",
                    "priority": "P0",
                }
            ],
            "totalCount": 1,
        }
    )
    graphql_json = json.dumps(
        {"data": {"repository": {"issues": {"nodes": [{"number": 9, "blockedBy": {"nodes": []}}]}}}}
    )

    def runner(args: list[str]) -> str:
        return graphql_json if "graphql" in args else project_json

    board = StatusFieldTrigger("owner/repo", 1, "In Progress", runner=runner).poll()
    assert board.open_numbers == {9}
    assert [i.number for i in board.issues] == [9]
    assert board.issues[0].in_trigger_column is True
    assert board.issues[0].priority == 0


def test_poll_raises_on_invalid_json():
    trigger = StatusFieldTrigger("owner/repo", 1, "In Progress", runner=lambda _args: "not json")
    with pytest.raises(GitHubError):
        trigger.poll()
