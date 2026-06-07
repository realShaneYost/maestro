"""Core data models shared across Maestro.

These types are deliberately plain: frozen dataclasses and enums with no
behaviour beyond construction. They are the vocabulary the scheduler, the
trigger sources, and the run log all speak.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class WorkerState(enum.Enum):
    """Lifecycle state of a dispatched worker, as surfaced by ``status``."""

    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    BLOCKED = "blocked"
    FAILED = "failed"


@dataclass(frozen=True)
class Issue:
    """A GitHub issue as the scheduler needs to see it.

    Attributes:
        number: The issue number.
        title: The issue title, for display only.
        priority: Sort key where a lower value runs first; ``None`` sorts last.
        blocked_by: Issue numbers this issue declares as blockers. The issue is
            held back while any blocker is still open.
        in_trigger_column: Whether the issue's project card currently sits in
            the configured trigger column.
        status_column: The current Status-field value on the issue's card
            (``"In Progress"``, ``"Blocked"``, …), or ``None`` when the Status
            field is unset on the card. ``in_trigger_column`` is derived from
            this; downstream code reads ``status_column`` directly when it
            needs to know *which* column the card sits in.
    """

    number: int
    title: str
    priority: int | None
    blocked_by: frozenset[int]
    in_trigger_column: bool
    status_column: str | None


@dataclass(frozen=True)
class RunRecord:
    """One line of the append-only run log (``runs.jsonl``).

    Attributes:
        issue: The issue number the worker handled.
        started: ISO-8601 timestamp of when the worker was spawned.
        ended: ISO-8601 timestamp of when the worker exited.
        outcome: Terminal state — one of ``done``, ``failed``, ``blocked``.
        pr_url: URL of the PR the worker opened, or ``None`` if it opened none.
    """

    issue: int
    started: str
    ended: str
    outcome: str
    pr_url: str | None


@dataclass
class BoardState:
    """A snapshot of the project board as a trigger source sees it.

    Attributes:
        issues: Every issue currently visible on the board.
        open_numbers: Issue numbers that are still open.
    """

    issues: list[Issue]
    open_numbers: set[int]
