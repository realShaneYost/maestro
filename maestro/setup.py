"""``maestro setup`` — narrowly scoped board bootstrap.

The command:
  * verifies gh auth + project scope (reusing the doctor's checks);
  * creates a Priority single-select field on the board if absent — and
    leaves it untouched if it already exists; and
  * checks the Status field has the configured ``trigger``, ``review``, and
    ``blocked`` columns, printing manual instructions if any are missing
    (it never mutates the Status field).

The setup is idempotent: a second run mutates nothing and reports it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from maestro.config import Config
from maestro.doctor import (
    CheckLevel,
    GhRunner,
    _fetch_priority_options,
    _run_gh,
    check_gh_auth,
    check_gh_project_scope,
)
from maestro.github import GitHubError, StatusFieldTrigger


@dataclass
class SetupReport:
    """A summary of what ``maestro setup`` did.

    Attributes:
        aborted: A reason string when setup bailed out before making changes.
        priority_created: Whether the Priority field was created this run.
        priority_already_present: Whether the Priority field was already present.
        status_missing: Status-field options the operator must add by hand.
        status_present: Whether all configured Status columns are present.
    """

    aborted: str | None = None
    priority_created: bool = False
    priority_already_present: bool = False
    status_missing: list[str] = field(default_factory=list)
    status_present: bool = False


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


_PRIORITY_OPTIONS = ("Critical", "High", "Normal", "Low")


def run_setup(config: Config, *, gh_runner: GhRunner = _run_gh) -> SetupReport:
    """Bootstrap the board's Priority field and verify Status columns.

    Args:
        config: Loaded Maestro configuration.
        gh_runner: Callable that runs gh; injectable for tests.

    Returns:
        A :class:`SetupReport` describing the actions taken and any manual
        follow-ups required.
    """
    report = SetupReport()

    auth = check_gh_auth(gh_runner)
    if auth.level is CheckLevel.FAIL:
        report.aborted = f"gh auth: {auth.message}"
        return report
    scope = check_gh_project_scope(gh_runner)
    if scope.level is CheckLevel.FAIL:
        report.aborted = f"gh project scope: {scope.message}"
        return report

    trigger = _build_trigger(config, gh_runner)

    existing = _fetch_priority_options(trigger, gh_runner)
    if existing is None:
        owner = config.repo_slug.split("/", 1)[0]
        code, _stdout, stderr = gh_runner(
            [
                "project",
                "field-create",
                str(config.project_number),
                "--owner",
                owner,
                "--name",
                "Priority",
                "--data-type",
                "SINGLE_SELECT",
                "--single-select-options",
                ",".join(_PRIORITY_OPTIONS),
            ]
        )
        if code != 0:
            report.aborted = f"failed to create Priority field: {stderr.strip()}"
            return report
        report.priority_created = True
    else:
        report.priority_already_present = True

    try:
        status_options = trigger.status_field_options()
    except GitHubError as err:
        report.aborted = f"failed to read Status field: {err}"
        return report
    required = [config.trigger_column, config.review_column, config.blocked_column]
    if status_options is None:
        report.status_missing = required
    else:
        report.status_missing = [name for name in required if name not in status_options]
    report.status_present = not report.status_missing
    return report
