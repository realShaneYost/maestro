"""``maestro add`` — add an existing issue to the board in one step.

Creating a GitHub issue does not place it on the Projects V2 board; a user
must otherwise run ``gh project item-add`` and then a second ``gh project
item-edit`` to set its Status. That second step takes the Status option's node
ID, which GitHub *regenerates* whenever a column is edited — a cached ID fails
silently. ``maestro add`` collapses both steps into one and reads the live
option ID at the moment of the write, so it never trips on a stale ID.

By default the card lands in the board's leftmost Status column (the
conventional backlog/Todo entry column); ``--status NAME`` targets any other
column. The trigger column is deliberately not the default — adding an issue
should not also dispatch it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from maestro.config import Config
from maestro.doctor import GhRunner, _run_gh
from maestro.github import GitHubError, StatusFieldTrigger


@dataclass
class AddReport:
    """A summary of what ``maestro add`` did.

    Attributes:
        issue: The issue number that was targeted.
        column: The Status column the card was placed in (or the requested
            column when it could not be resolved); ``None`` before resolution.
        available_columns: The board's Status columns in board order, surfaced
            so an unknown ``--status`` can be reported with the valid choices.
        added: Whether the issue was added to the board this run.
        status_set: Whether the card's Status was set to ``column``.
        aborted: A reason string when the command bailed out; ``None`` on
            success.
    """

    issue: int
    column: str | None = None
    available_columns: list[str] = field(default_factory=list)
    added: bool = False
    status_set: bool = False
    aborted: str | None = None


def _build_trigger(config: Config, gh: GhRunner) -> StatusFieldTrigger:
    """Build a StatusFieldTrigger whose runner adapts the doctor's gh runner."""

    def trigger_runner(args: list[str]) -> str:
        code, stdout, stderr = gh(args)
        if code != 0:
            raise GitHubError(f"gh {' '.join(args)} failed: {stderr.strip()}")
        return stdout

    return StatusFieldTrigger(
        config.repo_slug,
        config.project_number,
        config.trigger_column,
        runner=trigger_runner,
    )


def add_issue_to_board(
    config: Config, issue: int, *, status: str | None = None, gh_runner: GhRunner = _run_gh
) -> AddReport:
    """Add issue ``issue`` to the board and set its Status in one step.

    The Status field's live option IDs are read first; the requested column
    (or the leftmost column when ``status`` is ``None``) is resolved to its
    current option ID, which is then used for the write — so a regenerated
    column ID never causes a stale-ID failure.

    Args:
        config: Loaded Maestro configuration.
        issue: The issue number to add.
        status: The Status column to place the card in; defaults to the board's
            leftmost Status column when ``None``.
        gh_runner: Callable that runs gh; injectable for tests.

    Returns:
        An :class:`AddReport` describing the outcome and any abort reason.
    """
    report = AddReport(issue=issue)
    trigger = _build_trigger(config, gh_runner)

    try:
        ref = trigger.status_field_ref()
    except GitHubError as err:
        report.aborted = f"failed to read the board's Status field: {err}"
        return report
    if ref is None or not ref.options:
        report.aborted = (
            "board has no Status field with options; configure the board "
            "(see maestro setup / docs/usage.md) before adding issues"
        )
        return report

    report.available_columns = list(ref.options)
    target = status if status is not None else report.available_columns[0]
    report.column = target
    if target not in ref.options:
        report.aborted = (
            f"Status column {target!r} is not on the board; available columns: "
            + ", ".join(repr(name) for name in report.available_columns)
        )
        return report

    owner = config.repo_slug.split("/", 1)[0]
    issue_url = f"https://github.com/{config.repo_slug}/issues/{issue}"
    code, stdout, stderr = gh_runner(
        [
            "project",
            "item-add",
            str(config.project_number),
            "--owner",
            owner,
            "--url",
            issue_url,
            "--format",
            "json",
        ]
    )
    if code != 0:
        report.aborted = f"failed to add issue #{issue} to the board: {stderr.strip()}"
        return report
    try:
        item_id = json.loads(stdout)["id"]
    except (json.JSONDecodeError, KeyError, TypeError):
        report.aborted = f"could not read the added item's ID from gh output: {stdout!r}"
        return report
    if not isinstance(item_id, str):
        report.aborted = f"could not read the added item's ID from gh output: {stdout!r}"
        return report
    report.added = True

    code, _stdout, stderr = gh_runner(
        [
            "project",
            "item-edit",
            "--id",
            item_id,
            "--field-id",
            ref.field_id,
            "--project-id",
            ref.project_id,
            "--single-select-option-id",
            ref.options[target],
        ]
    )
    if code != 0:
        report.aborted = (
            f"added issue #{issue} to the board but failed to set its Status: {stderr.strip()}"
        )
        return report
    report.status_set = True
    return report
