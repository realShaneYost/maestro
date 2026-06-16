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


def blocked_by_reason(issue: Issue, open_numbers: set[int]) -> str | None:
    """Return why ``issue`` is held by an open native blocker, or ``None``.

    An issue declares its blockers via :attr:`Issue.blocked_by` (GitHub's
    native "blocked by" relationship). The issue is held while any of those
    blockers is still open. This is the single source of truth for the
    held-by-blocker message, shared by the scheduler's dispatch gate and the
    ``maestro status`` board view so the two never disagree.

    Args:
        issue: The issue being judged.
        open_numbers: Issue numbers that are still open. A blocker counts as
            satisfied only once it leaves this set.

    Returns:
        The held reason naming each open blocker, or ``None`` when no declared
        blocker is still open.
    """
    open_blockers = sorted(issue.blocked_by & open_numbers)
    if not open_blockers:
        return None
    listing = ", ".join(f"#{n}" for n in open_blockers)
    return f"skipped — blocked by open issue(s): {listing}"


def failure_cap_reason(
    issue: Issue,
    failure_counts: dict[int, int],
    max_consecutive_failures: int | None,
) -> str | None:
    """Return why ``issue`` is held by the consecutive-failure cap, or ``None``.

    An issue that has failed ``max_consecutive_failures`` times in a row without
    a clean run in between is held back, so a stuck issue cannot be redispatched
    forever — each retry is a full worker session. The streak counts are derived
    from the run log outside ``core/`` (see
    :func:`maestro.runlog.consecutive_failures`) and passed in, which keeps the
    scheduler pure. This is the single source of truth for the capped message,
    shared by the scheduler's dispatch gate and the ``maestro status`` board
    view so the two never disagree.

    Args:
        issue: The issue being judged.
        failure_counts: Map from issue number to its current consecutive-failure
            streak; an issue absent from the map has a streak of zero.
        max_consecutive_failures: The configured cap, or ``None`` to disable the
            cap entirely (every issue then passes this gate).

    Returns:
        The held reason naming the streak and the cap, or ``None`` when the cap
        is disabled or the issue's streak has not reached it.
    """
    if max_consecutive_failures is None:
        return None
    streak = failure_counts.get(issue.number, 0)
    if streak < max_consecutive_failures:
        return None
    return (
        f"skipped — hit failure cap ({streak} consecutive failed runs; "
        f"max_consecutive_failures={max_consecutive_failures})"
    )


def _ready_reason(
    issue: Issue,
    open_numbers: set[int],
    in_flight: set[int],
    failure_counts: dict[int, int],
    max_consecutive_failures: int | None,
) -> str | None:
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
    blocked = blocked_by_reason(issue, open_numbers)
    if blocked is not None:
        return blocked
    return failure_cap_reason(issue, failure_counts, max_consecutive_failures)


def explain_dispatch(
    issues: list[Issue],
    open_numbers: set[int],
    in_flight: set[int],
    max_concurrent: int,
    failure_counts: dict[int, int] | None = None,
    max_consecutive_failures: int | None = None,
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
        failure_counts: Map from issue number to its current consecutive-failure
            streak, derived from the run log (see
            :func:`maestro.runlog.consecutive_failures`). Defaults to no streaks.
        max_consecutive_failures: Cap above which an issue's failure streak holds
            it back; ``None`` disables the cap.

    Returns:
        One verdict per input issue, in the input order.
    """
    counts = failure_counts or {}
    candidates: list[Issue] = []
    candidate_set: set[int] = set()
    fixed: dict[int, str] = {}
    for issue in issues:
        reason = _ready_reason(issue, open_numbers, in_flight, counts, max_consecutive_failures)
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
    failure_counts: dict[int, int] | None = None,
    max_consecutive_failures: int | None = None,
) -> list[Issue]:
    """Select the issues to dispatch this cycle, in dispatch order.

    An issue is dispatchable when its card is in the trigger column, the issue
    is still open, it is not already being worked, every issue it is blocked by
    has been closed, and it has not hit the consecutive-failure cap. The result
    is capped so the number of live workers never exceeds ``max_concurrent``.

    Args:
        issues: Every issue currently visible on the board.
        open_numbers: Issue numbers that are still open. A blocker counts as
            satisfied only once it leaves this set.
        in_flight: Issue numbers that already have a live worker.
        max_concurrent: Hard ceiling on simultaneously running workers.
        failure_counts: Map from issue number to its current consecutive-failure
            streak, derived from the run log (see
            :func:`maestro.runlog.consecutive_failures`). Defaults to no streaks.
        max_consecutive_failures: Cap above which an issue's failure streak holds
            it back; ``None`` disables the cap.

    Returns:
        The issues to dispatch now, ordered by priority then issue number.
        Empty when no slots are free or nothing is ready.
    """
    verdicts = explain_dispatch(
        issues,
        open_numbers,
        in_flight,
        max_concurrent,
        failure_counts,
        max_consecutive_failures,
    )
    chosen = [v.issue for v in verdicts if v.dispatch]
    chosen.sort(key=_sort_key)
    return chosen
