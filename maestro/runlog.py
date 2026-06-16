"""Append-only run log — one JSON line per completed worker run.

The run log (``runs.jsonl``) is Maestro's audit trail and durable feedback
mechanism: every worker run leaves exactly one record. It is the operator's
inspection trail, and Maestro reads it back for one purpose — deriving each
issue's consecutive-failure streak (:func:`consecutive_failures`), the
structured input the scheduler's failure cap gates on. That derivation lives
here, outside ``core/``, so the scheduler never reads state itself.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from maestro.core.models import Outcome, RunRecord

_VALID_OUTCOMES: frozenset[Outcome] = frozenset({"done", "blocked", "failed"})


def append_run(log_path: Path, record: RunRecord) -> None:
    """Append one run record to the JSONL run log at ``log_path``.

    Args:
        log_path: Path to the ``runs.jsonl`` file; parent dirs are created.
        record: The completed run to record.
    """
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(record), sort_keys=True) + "\n")


def read_runs(log_path: Path) -> list[RunRecord]:
    """Read every run record from the JSONL run log at ``log_path``.

    A missing log yields an empty list. Malformed lines are skipped, so a
    partially written final line never breaks reading. A record whose
    ``outcome`` is not one of the documented :data:`Outcome` literals is
    treated as malformed too — keeping the run log's typed contract honest.

    Args:
        log_path: Path to the ``runs.jsonl`` file.

    Returns:
        The recorded runs, oldest first.
    """
    if not log_path.is_file():
        return []
    records: list[RunRecord] = []
    for raw in log_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
            outcome = data["outcome"]
            if outcome not in _VALID_OUTCOMES:
                continue
            record = RunRecord(
                issue=data["issue"],
                started=data["started"],
                ended=data["ended"],
                outcome=outcome,
                pr_url=data.get("pr_url"),
            )
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
        records.append(record)
    return records


def consecutive_failures(records: list[RunRecord]) -> dict[int, int]:
    """Return each issue's count of trailing consecutive failed runs.

    The records are walked in recorded order. For each issue the count is the
    number of ``failed`` runs since its most recent non-failed run — a clean
    run (``done`` or ``blocked``) resets the streak to zero. Issues whose latest
    run was not a failure are omitted, so the result maps only issues that
    currently carry a non-zero failure streak.

    This is the structured input the scheduler's failure cap reads (see
    :func:`maestro.core.scheduler.failure_cap_reason`): the streak is derived
    here, outside ``core/``, and passed in — the scheduler never reads the run
    log itself.

    Args:
        records: Run records, oldest first, as :func:`read_runs` returns them.

    Returns:
        A ``{issue-number: streak}`` map of current consecutive-failure counts,
        excluding any issue whose streak is zero.
    """
    counts: dict[int, int] = {}
    for record in records:
        if record.outcome == "failed":
            counts[record.issue] = counts.get(record.issue, 0) + 1
        else:
            counts[record.issue] = 0
    return {issue: count for issue, count in counts.items() if count > 0}
