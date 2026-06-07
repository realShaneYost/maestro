"""Tests for the GitHub Projects V2 trigger source."""

from __future__ import annotations

import json

import pytest

from maestro.github import (
    GitHubError,
    StatusFieldTrigger,
    _build_issues,
    _parse_board,
    _parse_open_issues,
    _parse_priority,
    _parse_priority_rank_map,
    _parse_status_field_options,
)


def test_parse_priority_rank_map_indexes_options_in_order():
    payload = {
        "data": {
            "repositoryOwner": {
                "projectV2": {
                    "priorityField": {
                        "options": [
                            {"name": "Critical"},
                            {"name": "High"},
                            {"name": "Normal"},
                            {"name": "Low"},
                        ]
                    }
                }
            }
        }
    }
    assert _parse_priority_rank_map(payload) == {
        "Critical": 0,
        "High": 1,
        "Normal": 2,
        "Low": 3,
    }


def test_parse_priority_rank_map_missing_field_is_empty():
    payload = {"data": {"repositoryOwner": {"projectV2": {"priorityField": None}}}}
    assert _parse_priority_rank_map(payload) == {}


def test_parse_priority_rank_map_missing_payload_is_empty():
    assert _parse_priority_rank_map({"data": {"repositoryOwner": {"projectV2": {}}}}) == {}
    assert _parse_priority_rank_map({}) == {}


def test_parse_priority_uses_option_index():
    rank = {"Critical": 0, "High": 1, "Normal": 2}
    assert _parse_priority("Critical", rank) == 0
    assert _parse_priority("Normal", rank) == 2


def test_parse_priority_unknown_option_is_none():
    assert _parse_priority("Mystery", {"High": 0}) is None


def test_parse_priority_unset_field_is_none():
    assert _parse_priority(None, {"High": 0}) is None


def test_parse_priority_with_missing_field_map_is_none_for_every_value():
    assert _parse_priority("Critical", {}) is None
    assert _parse_priority(None, {}) is None


def test_reordering_priority_options_reorders_ranks():
    payload_before = {
        "data": {
            "repositoryOwner": {
                "projectV2": {
                    "priorityField": {
                        "options": [{"name": "High"}, {"name": "Normal"}],
                    }
                }
            }
        }
    }
    payload_after = {
        "data": {
            "repositoryOwner": {
                "projectV2": {
                    "priorityField": {
                        "options": [{"name": "Normal"}, {"name": "High"}],
                    }
                }
            }
        }
    }
    before = _parse_priority_rank_map(payload_before)
    after = _parse_priority_rank_map(payload_after)
    assert _parse_priority("High", before) == 0
    assert _parse_priority("High", after) == 1
    assert _parse_priority("Normal", before) == 1
    assert _parse_priority("Normal", after) == 0


def test_parse_status_field_options_returns_ordered_names():
    payload = {
        "data": {
            "repositoryOwner": {
                "projectV2": {
                    "statusField": {
                        "options": [
                            {"name": "Backlog"},
                            {"name": "In Progress"},
                            {"name": "In Review"},
                            {"name": "Blocked"},
                            {"name": "Done"},
                        ]
                    }
                }
            }
        }
    }
    assert _parse_status_field_options(payload) == [
        "Backlog",
        "In Progress",
        "In Review",
        "Blocked",
        "Done",
    ]


def test_parse_status_field_options_missing_field_returns_none():
    payload = {"data": {"repositoryOwner": {"projectV2": {"statusField": None}}}}
    assert _parse_status_field_options(payload) is None


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


def test_parse_board_extracts_item_nodes():
    payload = {
        "data": {
            "repositoryOwner": {"projectV2": {"items": {"nodes": [{"content": None}]}}},
            "rateLimit": {"cost": 1, "remaining": 4999},
        }
    }
    assert _parse_board(payload) == [{"content": None}]


def test_parse_board_rejects_unresolved_owner_or_project():
    with pytest.raises(GitHubError, match="Unexpected project board"):
        _parse_board({"data": {"repositoryOwner": None}})


def test_build_issues_skips_non_issue_items_and_sets_fields():
    rank = {"Critical": 0, "High": 1, "Normal": 2}
    nodes = [
        {
            "content": {"__typename": "Issue", "number": 5, "title": "Real issue"},
            "status": {"name": "In Progress"},
            "priority": {"name": "High"},
        },
        {
            "content": {"__typename": "PullRequest"},
            "status": {"name": "In Progress"},
            "priority": None,
        },
        {
            "content": {"__typename": "Issue", "number": 7, "title": "Backlog issue"},
            "status": {"name": "Backlog"},
            "priority": None,
        },
        {"content": None, "status": None, "priority": None},
    ]
    issues = _build_issues(nodes, "In Progress", {5: frozenset({3})}, rank)
    assert [i.number for i in issues] == [5, 7]
    assert issues[0].in_trigger_column is True
    assert issues[0].status_column == "In Progress"
    assert issues[0].priority == 1
    assert issues[0].blocked_by == frozenset({3})
    assert issues[1].in_trigger_column is False
    assert issues[1].status_column == "Backlog"
    assert issues[1].priority is None


def test_build_issues_with_empty_rank_yields_none_priority():
    nodes = [
        {
            "content": {"__typename": "Issue", "number": 1, "title": "Whatever"},
            "status": {"name": "In Progress"},
            "priority": {"name": "Critical"},
        },
    ]
    issues = _build_issues(nodes, "In Progress", {}, {})
    assert issues[0].priority is None


def test_build_issues_status_column_none_when_unset():
    nodes = [
        {
            "content": {"__typename": "Issue", "number": 4, "title": "Whatever"},
            "status": None,
            "priority": None,
        },
    ]
    issues = _build_issues(nodes, "In Progress", {}, {})
    assert issues[0].status_column is None
    assert issues[0].in_trigger_column is False


def test_poll_combines_board_priorities_and_dependencies():
    board_json = json.dumps(
        {
            "data": {
                "repositoryOwner": {
                    "projectV2": {
                        "priorityField": {
                            "options": [
                                {"name": "Critical"},
                                {"name": "High"},
                                {"name": "Normal"},
                            ]
                        },
                        "items": {
                            "nodes": [
                                {
                                    "content": {
                                        "__typename": "Issue",
                                        "number": 9,
                                        "title": "Nine",
                                    },
                                    "status": {"name": "In Progress"},
                                    "priority": {"name": "Critical"},
                                }
                            ]
                        },
                    }
                },
                "rateLimit": {"cost": 1, "remaining": 4999},
            }
        }
    )
    deps_json = json.dumps(
        {"data": {"repository": {"issues": {"nodes": [{"number": 9, "blockedBy": {"nodes": []}}]}}}}
    )

    def runner(args: list[str]) -> str:
        return board_json if "projectV2" in " ".join(args) else deps_json

    board = StatusFieldTrigger("owner/repo", 1, "In Progress", runner=runner).poll()
    assert board.open_numbers == {9}
    assert [i.number for i in board.issues] == [9]
    assert board.issues[0].in_trigger_column is True
    assert board.issues[0].status_column == "In Progress"
    assert board.issues[0].priority == 0


def test_poll_with_missing_priority_field_yields_none_priorities():
    board_json = json.dumps(
        {
            "data": {
                "repositoryOwner": {
                    "projectV2": {
                        "priorityField": None,
                        "items": {
                            "nodes": [
                                {
                                    "content": {
                                        "__typename": "Issue",
                                        "number": 2,
                                        "title": "Two",
                                    },
                                    "status": {"name": "In Progress"},
                                    "priority": None,
                                }
                            ]
                        },
                    }
                },
                "rateLimit": {"cost": 1, "remaining": 4999},
            }
        }
    )
    deps_json = json.dumps(
        {"data": {"repository": {"issues": {"nodes": [{"number": 2, "blockedBy": {"nodes": []}}]}}}}
    )

    def runner(args: list[str]) -> str:
        return board_json if "projectV2" in " ".join(args) else deps_json

    board = StatusFieldTrigger("owner/repo", 1, "In Progress", runner=runner).poll()
    assert board.issues[0].priority is None


def test_poll_raises_on_invalid_json():
    trigger = StatusFieldTrigger("owner/repo", 1, "In Progress", runner=lambda _args: "not json")
    with pytest.raises(GitHubError):
        trigger.poll()


def test_status_field_options_fetches_field():
    payload = json.dumps(
        {
            "data": {
                "repositoryOwner": {
                    "projectV2": {
                        "statusField": {
                            "options": [
                                {"name": "Backlog"},
                                {"name": "In Progress"},
                                {"name": "Done"},
                            ]
                        }
                    }
                }
            }
        }
    )
    trigger = StatusFieldTrigger("owner/repo", 1, "In Progress", runner=lambda _args: payload)
    assert trigger.status_field_options() == ["Backlog", "In Progress", "Done"]


def test_status_field_options_missing_field_returns_none():
    payload = json.dumps({"data": {"repositoryOwner": {"projectV2": {"statusField": None}}}})
    trigger = StatusFieldTrigger("owner/repo", 1, "In Progress", runner=lambda _args: payload)
    assert trigger.status_field_options() is None
