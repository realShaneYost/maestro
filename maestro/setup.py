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
from importlib.resources.abc import Traversable

from maestro.config import Config
from maestro.doctor import (
    CheckLevel,
    GhRunner,
    _run_gh,
    check_gh_auth,
    check_gh_project_scope,
)
from maestro.github import GitHubError, StatusFieldTrigger
from maestro.install import InstallInfo, detect_install
from maestro.manifest import PACKAGE_FILES, ci_workflow_destination, read_ci_template


@dataclass
class SetupReport:
    """A summary of what ``maestro setup`` did.

    Attributes:
        install: How the running Maestro was installed; populated every run so
            the operator can confirm version and install source.
        aborted: A reason string when setup bailed out before making changes.
        priority_created: Whether the Priority field was created this run.
        priority_already_present: Whether the Priority field was already present.
        status_missing: Status-field options the operator must add by hand.
        status_present: Whether all configured Status columns are present.
        ci_managed: Whether ``manage_ci`` is enabled for this repo.
        ci_written: Whether the CI workflow was newly written this run.
        ci_already_present: Whether the CI workflow file already existed and was
            left untouched.
        ci_error: A message when materializing the CI workflow failed.
    """

    install: InstallInfo | None = None
    aborted: str | None = None
    priority_created: bool = False
    priority_already_present: bool = False
    status_missing: list[str] = field(default_factory=list)
    status_present: bool = False
    ci_managed: bool = False
    ci_written: bool = False
    ci_already_present: bool = False
    ci_error: str | None = None


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


def _materialize_ci(config: Config, report: SetupReport, package_root: Traversable) -> None:
    """Write the CI skeleton into the repo when ``manage_ci`` is enabled.

    A no-op when ``manage_ci`` is off (the default) — the repo is left untouched.
    When on, the namespaced workflow is written only if absent, so a second run
    mutates nothing; an existing file (the user's edits) is never overwritten.

    Args:
        config: Loaded Maestro configuration.
        report: The report mutated in place with the outcome.
        package_root: Root the CI template is resolved against.
    """
    report.ci_managed = config.manage_ci
    if not config.manage_ci:
        return
    dest = ci_workflow_destination(config.repo_path)
    if dest.exists():
        report.ci_already_present = True
        return
    try:
        content = read_ci_template(package_root)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(content, encoding="utf-8")
    except OSError as err:
        report.ci_error = str(err)
        return
    report.ci_written = True


def run_setup(
    config: Config, *, gh_runner: GhRunner = _run_gh, package_root: Traversable = PACKAGE_FILES
) -> SetupReport:
    """Bootstrap the board's Priority field and verify Status columns.

    Materializing the optional CI workflow is filesystem-only, so it happens
    first and independently of the board work below — a gh outage that aborts the
    board bootstrap still leaves the requested workflow in place.

    Args:
        config: Loaded Maestro configuration.
        gh_runner: Callable that runs gh; injectable for tests.
        package_root: Root the CI template is resolved against; defaults to the
            installed package. Injectable for tests.

    Returns:
        A :class:`SetupReport` describing the actions taken and any manual
        follow-ups required.
    """
    report = SetupReport(install=detect_install())
    _materialize_ci(config, report, package_root)

    auth = check_gh_auth(gh_runner)
    if auth.level is CheckLevel.FAIL:
        report.aborted = f"gh auth: {auth.message}"
        return report
    scope = check_gh_project_scope(gh_runner)
    if scope.level is CheckLevel.FAIL:
        report.aborted = f"gh project scope: {scope.message}"
        return report

    trigger = _build_trigger(config, gh_runner)

    try:
        existing = trigger.priority_field_options()
    except GitHubError as err:
        report.aborted = f"failed to read Priority field: {err}"
        return report
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
