"""Dispatch scheduling — pure, deterministic, model-free.

The scheduler answers one question: given the issues currently on the board
and the workers currently in flight, which issues should be dispatched right
now, and in what order? It reads only structured inputs (trigger-column
membership, declared blockers, priority) — there is no language model here and
there must never be one. All the reasoning was spent when the issue was
written; this module only executes the plan that reasoning already encoded.
"""

from __future__ import annotations

from maestro.core.models import Issue


def _sort_key(issue: Issue) -> tuple[bool, int, int]:
    """Return the dispatch-order sort key for ``issue``.

    Issues with an explicit priority run before issues without one; within
    each group a lower priority value runs first, then a lower issue number
    as a deterministic tie-breaker.
    """
    return (issue.priority is None, issue.priority or 0, issue.number)


def dispatchable_issues(
    issues: list[Issue],
    open_numbers: set[int],
    in_flight: set[int],
    max_concurrent: int,
) -> list[Issue]:
    """Select the issues to dispatch this cycle, in dispatch order.

    An issue is dispatchable when its card is in the trigger column, the issue
    is still open, it is not already being worked, and every issue it is
    blocked by has been closed. The result is capped so the number of live
    workers never exceeds ``max_concurrent``.

    Args:
        issues: Every issue currently visible on the board.
        open_numbers: Issue numbers that are still open. A blocker counts as
            satisfied only once it leaves this set.
        in_flight: Issue numbers that already have a live worker.
        max_concurrent: Hard ceiling on simultaneously running workers.

    Returns:
        The issues to dispatch now, ordered by priority then issue number.
        Empty when no slots are free or nothing is ready.
    """
    free_slots = max_concurrent - len(in_flight)
    if free_slots <= 0:
        return []

    ready = [
        issue
        for issue in issues
        if issue.in_trigger_column
        and issue.number in open_numbers
        and issue.number not in in_flight
        and not (issue.blocked_by & open_numbers)
    ]
    ready.sort(key=_sort_key)
    return ready[:free_slots]
