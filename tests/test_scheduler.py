"""Tests for the pure dispatch scheduler."""

from __future__ import annotations

from maestro.core.models import Issue
from maestro.core.scheduler import dispatchable_issues


def make_issue(number, *, priority=None, blocked_by=None, in_trigger_column=True):
    """Build an Issue with test-friendly defaults."""
    return Issue(
        number=number,
        title=f"Issue {number}",
        priority=priority,
        blocked_by=frozenset(blocked_by or ()),
        in_trigger_column=in_trigger_column,
    )


def test_no_issues_dispatches_nothing():
    assert dispatchable_issues([], set(), set(), max_concurrent=2) == []


def test_dispatches_trigger_column_issue():
    result = dispatchable_issues([make_issue(1)], {1}, set(), max_concurrent=2)
    assert [i.number for i in result] == [1]


def test_skips_issue_not_in_trigger_column():
    issues = [make_issue(1, in_trigger_column=False)]
    assert dispatchable_issues(issues, {1}, set(), max_concurrent=2) == []


def test_skips_closed_issue():
    # Issue 1 is in the trigger column but absent from the open set.
    assert dispatchable_issues([make_issue(1)], set(), set(), max_concurrent=2) == []


def test_skips_issue_with_open_blocker():
    issues = [make_issue(2, blocked_by={1})]
    # Issue 1 is still open, so 2 is held back.
    assert dispatchable_issues(issues, {1, 2}, set(), max_concurrent=2) == []


def test_dispatches_issue_once_blocker_closed():
    issues = [make_issue(2, blocked_by={1})]
    # Issue 1 is closed (absent from the open set), so 2 is free.
    result = dispatchable_issues(issues, {2}, set(), max_concurrent=2)
    assert [i.number for i in result] == [2]


def test_skips_in_flight_issue():
    assert dispatchable_issues([make_issue(1)], {1}, {1}, max_concurrent=2) == []


def test_respects_max_concurrent():
    issues = [make_issue(1), make_issue(2), make_issue(3)]
    result = dispatchable_issues(issues, {1, 2, 3}, set(), max_concurrent=2)
    assert [i.number for i in result] == [1, 2]


def test_counts_in_flight_against_max_concurrent():
    issues = [make_issue(2), make_issue(3)]
    # One worker is already running, so only one slot is free.
    result = dispatchable_issues(issues, {2, 3}, {1}, max_concurrent=2)
    assert [i.number for i in result] == [2]


def test_orders_by_priority_then_number():
    issues = [
        make_issue(5, priority=2),
        make_issue(3, priority=1),
        make_issue(9, priority=1),
        make_issue(1, priority=None),
    ]
    result = dispatchable_issues(issues, {1, 3, 5, 9}, set(), max_concurrent=4)
    # Priority 1 before 2, ties broken by issue number, no-priority last.
    assert [i.number for i in result] == [3, 9, 5, 1]
