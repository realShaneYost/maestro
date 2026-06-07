"""Dispatch scheduling — pure, deterministic, model-free.

The scheduler answers one question: given the issues currently on the board
and the workers currently in flight, which issues should be dispatched right
now, and in what order? It reads only structured inputs (trigger-column
membership, declared blockers, priority) — there is no language model here and
there must never be one. All the reasoning was spent when the issue was
written; this module only executes the plan that reasoning already encoded.
"""

from __future__ import annotations

from dataclasses import dataclass

from maestro.core.models import Issue


def _sort_key(issue: Issue) -> tuple[bool, int, int]:
    """Return the dispatch-order sort key for ``issue``.

    Issues with an explicit priority run before issues without one; within
    each group a lower priority value runs first, then a lower issue number
    as a deterministic tie-breaker.
    """
    return (issue.priority is None, issue.priority or 0, issue.number)


@dataclass(frozen=True)
class DispatchVerdict:
    """One issue's dispatch decision with a human-readable reason.

    Attributes:
        issue: The issue being judged.
        dispatch: Whether the scheduler would dispatch a worker for it now.
        reason: A short explanation written for the operator.
    """

    issue: Issue
    dispatch: bool
    reason: str


def _ready_reason(issue: Issue, open_numbers: set[int], in_flight: set[int]) -> str | None:
    """Return the first per-issue reason ``issue`` is not ready, or ``None`` if ready.

    ``None`` means the issue passes every per-issue gate; the caller still has
    to apply the global ``max_concurrent`` cap.
    """
    if not issue.in_trigger_column:
        column = issue.status_column or "no column"
        return f"skipped — not in trigger column (in {column!r})"
    if issue.number not in open_numbers:
        return "skipped — issue is closed"
    if issue.number in in_flight:
        return "skipped — already in flight"
    open_blockers = sorted(issue.blocked_by & open_numbers)
    if open_blockers:
        listing = ", ".join(f"#{n}" for n in open_blockers)
        return f"skipped — blocked by open issue(s): {listing}"
    return None


def explain_dispatch(
    issues: list[Issue],
    open_numbers: set[int],
    in_flight: set[int],
    max_concurrent: int,
) -> list[DispatchVerdict]:
    """Return a per-issue dispatch decision with a human-readable reason.

    The verdicts use the same rules as :func:`dispatchable_issues`. Within the
    pool of per-issue-ready candidates the scheduler can only dispatch
    ``max_concurrent - len(in_flight)`` workers; ready candidates that fall
    outside that cap are marked ``skipped — ready, but no free worker slots
    (max_concurrent reached)``.

    Args:
        issues: Every issue currently visible on the board.
        open_numbers: Issue numbers that are still open.
        in_flight: Issue numbers that already have a live worker.
        max_concurrent: Hard ceiling on simultaneously running workers.

    Returns:
        One verdict per input issue, in the input order.
    """
    candidates: list[Issue] = []
    candidate_set: set[int] = set()
    fixed: dict[int, str] = {}
    for issue in issues:
        reason = _ready_reason(issue, open_numbers, in_flight)
        if reason is None:
            candidates.append(issue)
            candidate_set.add(issue.number)
        else:
            fixed[issue.number] = reason

    free_slots = max(0, max_concurrent - len(in_flight))
    ordered = sorted(candidates, key=_sort_key)
    dispatch_set = {issue.number for issue in ordered[:free_slots]}

    verdicts: list[DispatchVerdict] = []
    for issue in issues:
        if issue.number not in candidate_set:
            verdicts.append(
                DispatchVerdict(issue=issue, dispatch=False, reason=fixed[issue.number])
            )
            continue
        if issue.number in dispatch_set:
            rank = issue.priority
            tag = f"priority rank {rank}" if rank is not None else "no priority set"
            verdicts.append(
                DispatchVerdict(issue=issue, dispatch=True, reason=f"would dispatch ({tag})")
            )
        else:
            verdicts.append(
                DispatchVerdict(
                    issue=issue,
                    dispatch=False,
                    reason="skipped — ready, but no free worker slots (max_concurrent reached)",
                )
            )
    return verdicts


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
    verdicts = explain_dispatch(issues, open_numbers, in_flight, max_concurrent)
    chosen = [v.issue for v in verdicts if v.dispatch]
    chosen.sort(key=_sort_key)
    return chosen
