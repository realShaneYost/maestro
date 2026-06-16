"""Tests for the pure dispatch scheduler."""

from __future__ import annotations

from maestro.core.models import Issue
from maestro.core.scheduler import (
    blocked_by_reason,
    dispatchable_issues,
    explain_dispatch,
    failure_cap_reason,
)


def make_issue(
    number,
    *,
    priority=None,
    blocked_by=None,
    in_trigger_column=True,
    status_column="In Progress",
):
    """Build an Issue with test-friendly defaults."""
    return Issue(
        number=number,
        title=f"Issue {number}",
        priority=priority,
        blocked_by=frozenset(blocked_by or ()),
        in_trigger_column=in_trigger_column,
        status_column=status_column,
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


def test_blocked_by_gate_holds_then_releases():
    # Regression for the parallel-collision bug (issue #17): two issues where
    # #38 declares #37 as a native `blockedBy` blocker. While #37 is open, #38
    # must be held back so the two workers cannot run in parallel and collide;
    # once #37 closes, #38 becomes dispatchable on the next poll.
    blocker = make_issue(37)
    dependent = make_issue(38, blocked_by={37})
    issues = [blocker, dependent]

    # Both open: the blocker is dispatchable, the dependent is held.
    held = dispatchable_issues(issues, {37, 38}, set(), max_concurrent=2)
    assert [i.number for i in held] == [37]

    # Blocker closed (absent from the open set): the dependent is released.
    released = dispatchable_issues([dependent], {38}, set(), max_concurrent=2)
    assert [i.number for i in released] == [38]


def test_blocked_by_reason_names_each_open_blocker():
    issue = make_issue(38, blocked_by={37, 5})
    # Only blockers still in the open set are named, ascending.
    assert blocked_by_reason(issue, {5, 37, 38}) == "skipped — blocked by open issue(s): #5, #37"


def test_blocked_by_reason_none_when_blockers_closed():
    issue = make_issue(38, blocked_by={37})
    assert blocked_by_reason(issue, {38}) is None


def test_blocked_by_reason_none_without_blockers():
    assert blocked_by_reason(make_issue(38), {38}) is None


def test_blocked_by_reason_matches_dispatch_verdict():
    # The board view reuses blocked_by_reason; the scheduler's verdict uses the
    # same string. They must never disagree on why an issue is held.
    issue = make_issue(38, blocked_by={37})
    verdict = explain_dispatch([issue], {37, 38}, set(), max_concurrent=2)[0]
    assert verdict.reason == blocked_by_reason(issue, {37, 38})


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


def _explain_one(issue, *, open_numbers=None, in_flight=None, max_concurrent=2):
    """Return the single verdict for ``issue`` from explain_dispatch."""
    if open_numbers is None:
        open_numbers = {issue.number}
    if in_flight is None:
        in_flight = set()
    return explain_dispatch([issue], open_numbers, in_flight, max_concurrent)[0]


def test_explain_would_dispatch_with_priority_rank():
    v = _explain_one(make_issue(1, priority=0))
    assert v.dispatch is True
    assert "priority rank 0" in v.reason


def test_explain_would_dispatch_with_no_priority_set():
    v = _explain_one(make_issue(1))
    assert v.dispatch is True
    assert "no priority set" in v.reason


def test_explain_skips_when_not_in_trigger_column():
    v = _explain_one(make_issue(1, in_trigger_column=False, status_column="Backlog"))
    assert v.dispatch is False
    assert "not in trigger column" in v.reason
    assert "Backlog" in v.reason


def test_explain_status_column_none_falls_back_to_no_column_label():
    v = _explain_one(make_issue(1, in_trigger_column=False, status_column=None))
    assert v.dispatch is False
    assert "no column" in v.reason


def test_explain_skips_closed_issue():
    v = _explain_one(make_issue(1), open_numbers=set())
    assert v.dispatch is False
    assert "closed" in v.reason


def test_explain_skips_in_flight_issue():
    v = _explain_one(make_issue(1), in_flight={1})
    assert v.dispatch is False
    assert "already in flight" in v.reason


def test_explain_skips_blocked_by_open_issue():
    issues = [make_issue(2, blocked_by={1})]
    verdicts = explain_dispatch(issues, {1, 2}, set(), max_concurrent=2)
    assert verdicts[0].dispatch is False
    assert "blocked by open issue(s): #1" in verdicts[0].reason


def test_explain_skips_extra_ready_when_max_concurrent_reached():
    issues = [make_issue(1), make_issue(2), make_issue(3)]
    verdicts = explain_dispatch(issues, {1, 2, 3}, set(), max_concurrent=2)
    skipped = [v for v in verdicts if not v.dispatch]
    assert len(skipped) == 1
    assert "no free worker slots" in skipped[0].reason


def test_explain_dispatch_set_matches_dispatchable_issues():
    issues = [
        make_issue(5, priority=2),
        make_issue(3, priority=1, blocked_by={9}),
        make_issue(9, priority=1, in_trigger_column=False, status_column="Backlog"),
        make_issue(1, priority=None),
        make_issue(7, priority=0),
    ]
    open_numbers = {1, 3, 5, 7, 9}
    in_flight: set[int] = {1}
    max_concurrent = 3
    verdicts = explain_dispatch(issues, open_numbers, in_flight, max_concurrent)
    explained = {v.issue.number for v in verdicts if v.dispatch}
    direct = {
        i.number for i in dispatchable_issues(issues, open_numbers, in_flight, max_concurrent)
    }
    assert explained == direct


def test_explain_returns_one_verdict_per_input_issue_in_order():
    issues = [make_issue(7), make_issue(3), make_issue(5)]
    verdicts = explain_dispatch(issues, {3, 5, 7}, set(), max_concurrent=10)
    assert [v.issue.number for v in verdicts] == [7, 3, 5]


def test_failure_cap_reason_holds_at_the_cap():
    issue = make_issue(1)
    reason = failure_cap_reason(issue, {1: 3}, 3)
    assert reason is not None
    assert "hit failure cap" in reason
    assert "3 consecutive failed runs" in reason
    assert "max_consecutive_failures=3" in reason


def test_failure_cap_reason_none_below_the_cap():
    assert failure_cap_reason(make_issue(1), {1: 2}, 3) is None


def test_failure_cap_reason_none_for_unseen_issue():
    assert failure_cap_reason(make_issue(1), {}, 3) is None


def test_failure_cap_reason_none_when_cap_disabled():
    # A None cap disables the gate even with a large streak.
    assert failure_cap_reason(make_issue(1), {1: 99}, None) is None


def test_dispatch_skips_issue_at_failure_cap():
    issues = [make_issue(1)]
    result = dispatchable_issues(
        issues, {1}, set(), max_concurrent=2, failure_counts={1: 3}, max_consecutive_failures=3
    )
    assert result == []


def test_dispatch_allows_issue_below_failure_cap():
    issues = [make_issue(1)]
    result = dispatchable_issues(
        issues, {1}, set(), max_concurrent=2, failure_counts={1: 2}, max_consecutive_failures=3
    )
    assert [i.number for i in result] == [1]


def test_dispatch_no_cap_applied_without_arguments():
    # Backward-compatible default: omitting the cap dispatches regardless.
    issues = [make_issue(1)]
    result = dispatchable_issues(issues, {1}, set(), max_concurrent=2)
    assert [i.number for i in result] == [1]


def test_explain_skips_capped_issue_with_matching_reason():
    issue = make_issue(1)
    verdict = explain_dispatch(
        [issue], {1}, set(), 2, failure_counts={1: 4}, max_consecutive_failures=3
    )[0]
    assert verdict.dispatch is False
    assert verdict.reason == failure_cap_reason(issue, {1: 4}, 3)


def test_blocked_by_outranks_failure_cap():
    # An issue both blocked by an open issue and over the cap reports the
    # blocker first, mirroring the scheduler's gate precedence.
    issue = make_issue(2, blocked_by={1})
    verdict = explain_dispatch(
        [issue], {1, 2}, set(), 2, failure_counts={2: 5}, max_consecutive_failures=3
    )[0]
    assert verdict.dispatch is False
    assert "blocked by open issue(s): #1" in verdict.reason
