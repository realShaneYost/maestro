"""Tests for ``maestro doctor`` preflight checks."""

from __future__ import annotations

import json
from pathlib import Path

from maestro.config import Config
from maestro.doctor import (
    CheckLevel,
    check_board_reachable,
    check_board_size,
    check_gh_auth,
    check_gh_project_scope,
    check_priority_field_present,
    check_python_repo_needs_worktree_setup,
    check_repo_is_git_with_origin,
    check_status_columns_present,
    check_worker_skill_present,
    run_checks,
)
from maestro.github import StatusFieldTrigger


def _gh_ok(stdout: str = "", stderr: str = ""):
    def run(_args):
        return 0, stdout, stderr

    return run


def _gh_fail(code: int = 1, stderr: str = "boom"):
    def run(_args):
        return code, "", stderr

    return run


def _trigger(runner) -> StatusFieldTrigger:
    return StatusFieldTrigger("owner/repo", 1, "In Progress", runner=runner)


def test_gh_auth_pass():
    result = check_gh_auth(_gh_ok())
    assert result.level is CheckLevel.PASS


def test_gh_auth_fail_surfaces_first_stderr_line():
    result = check_gh_auth(_gh_fail(stderr="not logged in\n"))
    assert result.level is CheckLevel.FAIL
    assert "not logged in" in result.message


def test_gh_project_scope_pass_when_present():
    output = "Token scopes: 'repo', 'project', 'workflow'\n"
    result = check_gh_project_scope(_gh_ok(stdout=output))
    assert result.level is CheckLevel.PASS


def test_gh_project_scope_fail_when_missing():
    result = check_gh_project_scope(_gh_ok(stdout="Token scopes: 'repo', 'workflow'\n"))
    assert result.level is CheckLevel.FAIL
    assert "project" in result.message


def test_gh_project_scope_warn_when_scope_line_absent():
    result = check_gh_project_scope(_gh_ok(stdout="(unrelated)\n"))
    assert result.level is CheckLevel.WARN


def test_check_repo_is_git_pass(tmp_path):
    def run(args, cwd):
        if args == ["rev-parse", "--is-inside-work-tree"]:
            return 0, "true\n", ""
        if args == ["remote", "get-url", "origin"]:
            return 0, "git@example.com:owner/repo.git\n", ""
        return 1, "", "unexpected"

    result = check_repo_is_git_with_origin(tmp_path, runner=run)
    assert result.level is CheckLevel.PASS


def test_check_repo_is_git_fail_when_no_origin(tmp_path):
    def run(args, cwd):
        if args == ["rev-parse", "--is-inside-work-tree"]:
            return 0, "true\n", ""
        return 1, "", "no remote"

    result = check_repo_is_git_with_origin(tmp_path, runner=run)
    assert result.level is CheckLevel.FAIL
    assert "origin" in result.message


def test_check_repo_is_git_fail_when_not_a_repo(tmp_path):
    def run(args, cwd):
        return 128, "", "fatal: not a git repository\n"

    result = check_repo_is_git_with_origin(tmp_path, runner=run)
    assert result.level is CheckLevel.FAIL


def test_board_reachable_pass():
    payload = json.dumps(
        {
            "data": {
                "repositoryOwner": {
                    "projectV2": {
                        "priorityField": None,
                        "items": {"nodes": []},
                    }
                },
                "rateLimit": {"cost": 1, "remaining": 4999},
            }
        }
    )
    deps = json.dumps({"data": {"repository": {"issues": {"nodes": []}}}})

    def runner(args):
        return payload if "projectV2" in " ".join(args) else deps

    result = check_board_reachable(_trigger(runner))
    assert result.level is CheckLevel.PASS


def test_board_reachable_fail_on_bad_payload():
    def runner(_args):
        return "not json"

    result = check_board_reachable(_trigger(runner))
    assert result.level is CheckLevel.FAIL


def _fields_payload(nodes: list[dict[str, object]]) -> str:
    """Render a fields-enumeration response from single-select field nodes."""
    return json.dumps({"data": {"repositoryOwner": {"projectV2": {"fields": {"nodes": nodes}}}}})


def test_status_columns_present_pass():
    payload = _fields_payload(
        [
            {
                "name": "Status",
                "options": [
                    {"name": "Backlog"},
                    {"name": "In Progress"},
                    {"name": "In Review"},
                    {"name": "Blocked"},
                    {"name": "Done"},
                ],
            }
        ]
    )
    result = check_status_columns_present(
        _trigger(lambda _a: payload), "In Progress", "In Review", "Blocked"
    )
    assert result.level is CheckLevel.PASS


def test_status_columns_present_fail_when_missing():
    payload = _fields_payload(
        [{"name": "Status", "options": [{"name": "Backlog"}, {"name": "In Progress"}]}]
    )
    result = check_status_columns_present(
        _trigger(lambda _a: payload), "In Progress", "In Review", "Blocked"
    )
    assert result.level is CheckLevel.FAIL
    assert "'In Review'" in result.message
    assert "'Blocked'" in result.message


def test_status_columns_present_fail_when_status_field_absent():
    payload = _fields_payload([])
    result = check_status_columns_present(
        _trigger(lambda _a: payload), "In Progress", "In Review", "Blocked"
    )
    assert result.level is CheckLevel.FAIL


def test_priority_field_present_pass():
    payload = _fields_payload(
        [{"name": "Priority", "options": [{"name": "Critical"}, {"name": "High"}]}]
    )
    result = check_priority_field_present(_trigger(lambda _a: payload))
    assert result.level is CheckLevel.PASS
    assert "Critical" in result.message


def test_priority_field_present_fail_when_missing():
    payload = _fields_payload([])
    result = check_priority_field_present(_trigger(lambda _a: payload))
    assert result.level is CheckLevel.FAIL


def test_priority_field_present_fail_when_no_options():
    payload = _fields_payload([{"name": "Priority", "options": []}])
    result = check_priority_field_present(_trigger(lambda _a: payload))
    assert result.level is CheckLevel.FAIL
    assert "no options" in result.message


def test_worker_skill_present_warns_when_missing(tmp_path):
    result = check_worker_skill_present(tmp_path, "/cc-work {issue}")
    assert result.level is CheckLevel.WARN
    assert "cc-work" in result.message


def test_worker_skill_present_pass_when_file_exists(tmp_path):
    skill = tmp_path / ".claude" / "skills" / "cc-work" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("hello", encoding="utf-8")
    result = check_worker_skill_present(tmp_path, "/cc-work {issue}")
    assert result.level is CheckLevel.PASS


def test_python_repo_needs_worktree_setup_warn(tmp_path):
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    result = check_python_repo_needs_worktree_setup(tmp_path, None)
    assert result is not None
    assert result.level is CheckLevel.WARN
    assert "worktree_setup" in result.message


def test_python_repo_with_setup_passes(tmp_path):
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    result = check_python_repo_needs_worktree_setup(tmp_path, "uv venv .venv")
    assert result is not None
    assert result.level is CheckLevel.PASS


def test_non_python_repo_skipped_entirely(tmp_path):
    assert check_python_repo_needs_worktree_setup(tmp_path, None) is None


def test_board_size_warns_near_cap():
    issues = [
        {
            "content": {"__typename": "Issue", "number": n, "title": f"#{n}"},
            "status": {"name": "Backlog"},
            "priority": None,
        }
        for n in range(1, 101)
    ]
    board_payload = json.dumps(
        {
            "data": {
                "repositoryOwner": {
                    "projectV2": {"priorityField": None, "items": {"nodes": issues}}
                },
                "rateLimit": {"cost": 1, "remaining": 4999},
            }
        }
    )
    deps = json.dumps(
        {
            "data": {
                "repository": {
                    "issues": {
                        "nodes": [{"number": n, "blockedBy": {"nodes": []}} for n in range(1, 5)]
                    }
                }
            }
        }
    )

    def trigger_runner(args):
        return board_payload if "projectV2" in " ".join(args) else deps

    result = check_board_size(_trigger(trigger_runner))
    assert result is not None
    assert result.level is CheckLevel.WARN
    assert "100" in result.message


def test_board_size_returns_none_when_below_cap():
    board_payload = json.dumps(
        {
            "data": {
                "repositoryOwner": {"projectV2": {"priorityField": None, "items": {"nodes": []}}},
                "rateLimit": {"cost": 1, "remaining": 4999},
            }
        }
    )
    deps = json.dumps({"data": {"repository": {"issues": {"nodes": []}}}})

    def trigger_runner(args):
        return board_payload if "projectV2" in " ".join(args) else deps

    assert check_board_size(_trigger(trigger_runner)) is None


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


def test_run_checks_aggregates_results(tmp_path):
    config = _config(tmp_path)
    board_payload = json.dumps(
        {
            "data": {
                "repositoryOwner": {
                    "projectV2": {
                        "priorityField": {"options": [{"name": "Critical"}]},
                        "items": {"nodes": []},
                    }
                },
                "rateLimit": {"cost": 1, "remaining": 4999},
            }
        }
    )
    fields_payload = json.dumps(
        {
            "data": {
                "repositoryOwner": {
                    "projectV2": {
                        "fields": {
                            "nodes": [
                                {
                                    "name": "Status",
                                    "options": [
                                        {"name": "Backlog"},
                                        {"name": "In Progress"},
                                        {"name": "In Review"},
                                        {"name": "Blocked"},
                                    ],
                                },
                                {"name": "Priority", "options": [{"name": "Critical"}]},
                            ]
                        }
                    }
                }
            }
        }
    )
    deps_payload = json.dumps({"data": {"repository": {"issues": {"nodes": []}}}})

    def gh(args):
        joined = " ".join(args)
        if "auth" in joined:
            return 0, "Token scopes: 'repo', 'project'\n", ""
        if "fields(first" in joined:
            return 0, fields_payload, ""
        if "projectV2" in joined:
            return 0, board_payload, ""
        if "repository" in joined:
            return 0, deps_payload, ""
        return 0, "", ""

    def git(args, cwd):
        if args[0] == "rev-parse":
            return 0, "true\n", ""
        if args[0] == "remote":
            return 0, "git@example.com:owner/repo.git\n", ""
        return 1, "", "unexpected"

    results = run_checks(config, gh_runner=gh, git_runner=git)
    names = [r.name for r in results]
    assert "gh auth" in names
    assert "gh project scope" in names
    assert "board reachable" in names
    assert "status columns" in names
    assert "priority field" in names
    assert "worker skill" in names
    # No FAILs in this happy path:
    assert not [r for r in results if r.level is CheckLevel.FAIL]
