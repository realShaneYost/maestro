"""Read-only preflight checks for ``maestro doctor``.

Each check is a small pure-ish function that returns a :class:`CheckResult` —
a stable ``(name, level, message)`` triple. ``maestro doctor`` collects the
results, prints them, and exits non-zero if any FAIL surfaced.

The module never mutates anything. It shells out only through injectable
runners so the suite can be exercised with canned responses, in line with the
repo's testing rules.
"""

from __future__ import annotations

import enum
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from maestro.config import Config
from maestro.github import GitHubError, StatusFieldTrigger
from maestro.install import detect_install
from maestro.manifest import (
    CI_WORKFLOW,
    MANIFEST,
    PACKAGE_ROOT,
    WORKER_SKILL,
    Dependency,
    ci_template_path,
    ci_workflow_destination,
)


class CheckLevel(enum.Enum):
    """How an individual check resolved."""

    PASS = "pass"
    FAIL = "fail"
    WARN = "warn"


@dataclass(frozen=True)
class CheckResult:
    """One preflight check's outcome.

    Attributes:
        name: Short label naming the check (``"gh auth"``, ``"board reachable"``).
        level: Pass / fail / warn.
        message: Human-readable detail rendered next to ``name``.
    """

    name: str
    level: CheckLevel
    message: str


# (args) -> (returncode, stdout, stderr). Lets the suite supply canned output.
GhRunner = Callable[[list[str]], tuple[int, str, str]]


# Blessed, opinionated remediation commands. These live in the doctor layer
# (never ``core/``) so the CLI surface owns every operator-facing string. The
# login command must stay in sync with the README's Prerequisites section.
GH_AUTH_LOGIN_CMD = (
    "gh auth login --hostname github.com --git-protocol https --web --scopes project"
)
GH_AUTH_REFRESH_PROJECT_CMD = "gh auth refresh -s project --hostname github.com"


def _run_gh(args: list[str]) -> tuple[int, str, str]:
    """Run ``gh`` with ``args`` and return ``(returncode, stdout, stderr)``."""
    try:
        result = subprocess.run(["gh", *args], capture_output=True, text=True, check=False)
    except OSError as err:
        return 1, "", str(err)
    return result.returncode, result.stdout, result.stderr


def check_install() -> CheckResult:
    """Report Maestro's version and whether it runs from an isolated tool install.

    PASS when Maestro runs from a uv tool or pipx install; WARN when it runs
    from a source checkout or an unrecognized location, so the operator is
    never confused about which Maestro they are running.
    """
    info = detect_install()
    level = CheckLevel.PASS if info.is_isolated_tool else CheckLevel.WARN
    return CheckResult("install", level, info.summary)


def check_gh_auth(runner: GhRunner) -> CheckResult:
    """Verify ``gh auth status`` succeeds.

    On failure the message names the exact blessed ``gh auth login`` command so
    a logged-out operator has a copy-pasteable next step. Maestro never runs the
    login itself — authentication stays a deliberate user action.
    """
    code, _stdout, stderr = runner(["auth", "status"])
    if code == 0:
        return CheckResult("gh auth", CheckLevel.PASS, "authenticated")
    detail = stderr.strip().splitlines()[0] if stderr.strip() else f"exit {code}"
    return CheckResult(
        "gh auth",
        CheckLevel.FAIL,
        f"gh auth status failed: {detail}; log in with `{GH_AUTH_LOGIN_CMD}`",
    )


_SCOPE_LINE = re.compile(r"Token scopes:\s*(.*)$", re.MULTILINE)


def check_gh_project_scope(runner: GhRunner) -> CheckResult:
    """Verify the gh token includes the ``project`` scope.

    Parses ``gh auth status`` output for a ``Token scopes:`` line. When that
    line cannot be located the check warns instead of failing — gh's output
    format varies and an absent line is not authoritative evidence.
    """
    code, stdout, stderr = runner(["auth", "status"])
    text = stdout + "\n" + stderr
    match = _SCOPE_LINE.search(text)
    if match is None:
        return CheckResult(
            "gh project scope",
            CheckLevel.WARN,
            "could not determine token scopes from gh auth status",
        )
    scopes = {token.strip().strip("'\"") for token in match.group(1).split(",")}
    if "project" in scopes:
        return CheckResult("gh project scope", CheckLevel.PASS, "project scope present")
    return CheckResult(
        "gh project scope",
        CheckLevel.FAIL,
        f"gh token is missing the 'project' scope; run `{GH_AUTH_REFRESH_PROJECT_CMD}`"
        + (f" (current: {match.group(1).strip()})" if code == 0 else ""),
    )


# (args, cwd) -> (returncode, stdout, stderr). Same shape as `_run_gh` plus a cwd.
GitRunner = Callable[[list[str], Path], tuple[int, str, str]]


def _run_git(args: list[str], cwd: Path) -> tuple[int, str, str]:
    """Run ``git`` with ``args`` in ``cwd`` and return ``(rc, stdout, stderr)``."""
    try:
        result = subprocess.run(
            ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=False
        )
    except OSError as err:
        return 1, "", str(err)
    return result.returncode, result.stdout, result.stderr


def check_repo_is_git_with_origin(repo_path: Path, runner: GitRunner = _run_git) -> CheckResult:
    """Verify ``repo_path`` is a git working tree with an ``origin`` remote."""
    if not repo_path.is_dir():
        return CheckResult("repo_path", CheckLevel.FAIL, f"not a directory: {repo_path}")
    code, _, stderr = runner(["rev-parse", "--is-inside-work-tree"], repo_path)
    if code != 0:
        return CheckResult("repo_path", CheckLevel.FAIL, f"not a git repository: {stderr.strip()}")
    code, stdout, _ = runner(["remote", "get-url", "origin"], repo_path)
    if code != 0 or not stdout.strip():
        return CheckResult("repo_path", CheckLevel.FAIL, "no 'origin' remote configured")
    return CheckResult("repo_path", CheckLevel.PASS, "git repo with origin remote")


def check_board_reachable(trigger: StatusFieldTrigger) -> CheckResult:
    """Verify a board poll succeeds end-to-end."""
    try:
        board = trigger.poll()
    except GitHubError as err:
        return CheckResult("board reachable", CheckLevel.FAIL, str(err))
    return CheckResult(
        "board reachable",
        CheckLevel.PASS,
        f"{len(board.issues)} item(s) visible",
    )


def check_status_columns_present(
    trigger: StatusFieldTrigger,
    trigger_column: str,
    review_column: str,
    blocked_column: str,
) -> CheckResult:
    """Verify the Status field's options include all three configured columns."""
    try:
        options = trigger.status_field_options()
    except GitHubError as err:
        return CheckResult("status columns", CheckLevel.FAIL, str(err))
    if options is None:
        return CheckResult("status columns", CheckLevel.FAIL, "Status field not found on board")
    required = [trigger_column, review_column, blocked_column]
    missing = [name for name in required if name not in options]
    if missing:
        return CheckResult(
            "status columns",
            CheckLevel.FAIL,
            "Status field is missing option(s): " + ", ".join(repr(m) for m in missing),
        )
    return CheckResult("status columns", CheckLevel.PASS, "all configured columns present")


def check_priority_field_present(trigger: StatusFieldTrigger) -> CheckResult:
    """Verify a Priority single-select field with at least one option exists.

    Args:
        trigger: The board trigger used to introspect the Priority field.

    Returns:
        FAIL when the field is missing or has no options; PASS otherwise,
        listing the options in their declared order.
    """
    try:
        options = trigger.priority_field_options()
    except GitHubError as err:
        return CheckResult("priority field", CheckLevel.FAIL, str(err))
    if options is None:
        return CheckResult("priority field", CheckLevel.FAIL, "Priority field not found on board")
    if not options:
        return CheckResult("priority field", CheckLevel.FAIL, "Priority field has no options")
    return CheckResult(
        "priority field", CheckLevel.PASS, f"{len(options)} option(s): {', '.join(options)}"
    )


def check_worker_skill_present(repo_path: Path, worker_prompt: str) -> CheckResult:
    """WARN when the worker skill file does not exist under ``repo_path``.

    The skill name is the first token after the leading ``/`` in the prompt:
    ``/cc-work {issue}`` → ``cc-work``. The file is expected at
    ``<repo_path>/.claude/skills/<name>/SKILL.md``.
    """
    if not worker_prompt.startswith("/"):
        return CheckResult(
            "worker skill",
            CheckLevel.WARN,
            "worker_prompt does not begin with '/'; skipping skill-file check",
        )
    name = worker_prompt[1:].split()[0]
    skill = repo_path / ".claude" / "skills" / name / "SKILL.md"
    if skill.is_file():
        return CheckResult("worker skill", CheckLevel.PASS, f"found {skill}")
    return CheckResult(
        "worker skill",
        CheckLevel.WARN,
        f"skill file not found at {skill} (the prompt may target a different agent layout)",
    )


def check_dependency(dependency: Dependency, repo_path: Path) -> CheckResult:
    """Report whether a manifest dependency is present at its expected location.

    A missing required dependency FAILs; a missing optional one WARNs. The
    worker skill is reported by :func:`check_worker_skill_present` instead, which
    follows the configured ``worker_prompt``, so this is the right reporter for
    the remaining manifest entries.

    Args:
        dependency: The manifest entry to check.
        repo_path: Root of the driven repository.

    Returns:
        A :class:`CheckResult` naming the dependency, its location, and presence.
    """
    if dependency.is_present(repo_path):
        return CheckResult(dependency.name, CheckLevel.PASS, f"present at {dependency.location}")
    level = CheckLevel.FAIL if dependency.required else CheckLevel.WARN
    qualifier = "required" if dependency.required else "optional"
    return CheckResult(
        dependency.name,
        level,
        f"{qualifier} dependency missing at {dependency.location}",
    )


def check_ci_template(
    repo_path: Path, manage_ci: bool, package_root: Path = PACKAGE_ROOT
) -> CheckResult:
    """Report the optional CI skeleton's managed state, with an enable hint.

    CI is an opt-in enhancement: its only payoff is the worker's automated rework
    signal (failed checks) on top of human review. The skeleton therefore ships
    disabled. This check surfaces that present-but-disabled state so an operator
    knows the template exists and how to turn it on, WARNs when it is enabled but
    not yet installed, and PASSes once the managed workflow is in place.

    Args:
        repo_path: Root of the driven repository.
        manage_ci: Whether ``manage_ci`` is enabled in the config.
        package_root: Root the CI template is resolved against; injectable for
            tests.

    Returns:
        A :class:`CheckResult` named after the CI-workflow dependency.
    """
    if not manage_ci:
        return CheckResult(
            CI_WORKFLOW,
            CheckLevel.WARN,
            "optional CI template available but disabled; enable it by setting "
            "manage_ci: true in maestro.yml and running maestro setup",
        )
    destination = ci_workflow_destination(repo_path)
    if destination.is_file():
        return CheckResult(
            CI_WORKFLOW,
            CheckLevel.PASS,
            f"managed; workflow present at {destination.relative_to(repo_path)}",
        )
    if not ci_template_path(package_root).is_file():
        return CheckResult(
            CI_WORKFLOW,
            CheckLevel.WARN,
            "manage_ci is on but the packaged CI template is unavailable; "
            "reinstall Maestro from source to materialize it",
        )
    return CheckResult(
        CI_WORKFLOW,
        CheckLevel.WARN,
        "manage_ci is on but the workflow is not installed yet; run maestro setup",
    )


def check_python_repo_needs_worktree_setup(
    repo_path: Path, worktree_setup: str | None
) -> CheckResult | None:
    """WARN when a Python project has no ``worktree_setup`` configured.

    Returns ``None`` for non-Python repos so the doctor can drop the row from
    its output entirely. Reports a single sentence explaining the shared-venv
    editable-install footgun and pointing at ``maestro.example.yml``.
    """
    looks_python = (repo_path / "pyproject.toml").is_file() or (
        repo_path / "requirements.txt"
    ).is_file()
    if not looks_python:
        return None
    if worktree_setup is not None:
        return CheckResult("worktree setup", CheckLevel.PASS, "worktree_setup configured")
    return CheckResult(
        "worktree setup",
        CheckLevel.WARN,
        "repo_path looks like a Python project but worktree_setup is unset; the fallback "
        "symlinks the repo's .venv and its editable install points at the primary "
        "checkout, so a worker's tests run against the wrong code. See "
        "maestro.example.yml's worktree_setup key.",
    )


def check_board_size(trigger: StatusFieldTrigger) -> CheckResult | None:
    """WARN when the board or the open-issues list reaches the 100-item cap.

    Returns ``None`` when both sit comfortably below the cap — there is no
    actionable signal to surface.
    """
    try:
        board = trigger.poll()
    except GitHubError:
        return None
    board_count = len(board.issues)
    open_count = len(board.open_numbers)
    near_cap: list[str] = []
    if board_count >= 100:
        near_cap.append(f"board has {board_count} item(s)")
    if open_count >= 100:
        near_cap.append(f"repo has {open_count} open issue(s)")
    if not near_cap:
        return None
    return CheckResult(
        "pagination",
        CheckLevel.WARN,
        "Maestro's queries cap at 100 items without pagination; " + " and ".join(near_cap),
    )


def run_checks(
    config: Config,
    *,
    gh_runner: GhRunner = _run_gh,
    git_runner: GitRunner = _run_git,
    trigger: StatusFieldTrigger | None = None,
) -> list[CheckResult]:
    """Run every preflight check in order and return their results.

    Args:
        config: Loaded Maestro configuration.
        gh_runner: Callable that runs gh; injectable for tests.
        git_runner: Callable that runs git; injectable for tests.
        trigger: Pre-built trigger source; built from ``config`` when omitted.

    Returns:
        One :class:`CheckResult` per check, in the order they ran.
    """
    if trigger is None:
        trigger = StatusFieldTrigger(
            config.repo_slug,
            config.project_number,
            config.trigger_column,
            runner=_adapt_runner(gh_runner),
        )

    results: list[CheckResult] = []
    results.append(check_install())
    results.append(check_gh_auth(gh_runner))
    results.append(check_gh_project_scope(gh_runner))
    results.append(check_repo_is_git_with_origin(config.repo_path, git_runner))
    results.append(check_board_reachable(trigger))
    results.append(
        check_status_columns_present(
            trigger, config.trigger_column, config.review_column, config.blocked_column
        )
    )
    results.append(check_priority_field_present(trigger))
    # Report every editable dependency the manifest enumerates. The worker skill
    # is reported via the prompt-aware check so a non-default or non-Claude skill
    # layout still resolves correctly; the rest report against their fixed paths.
    for dependency in MANIFEST:
        if dependency.name == WORKER_SKILL:
            results.append(check_worker_skill_present(config.repo_path, config.worker_prompt))
        elif dependency.name == CI_WORKFLOW:
            # CI is an opt-in skeleton gated by manage_ci, not a bare directory
            # presence check — report the managed state and the enable hint.
            results.append(check_ci_template(config.repo_path, config.manage_ci))
        else:
            results.append(check_dependency(dependency, config.repo_path))
    py = check_python_repo_needs_worktree_setup(config.repo_path, config.worktree_setup)
    if py is not None:
        results.append(py)
    size = check_board_size(trigger)
    if size is not None:
        results.append(size)
    return results


def _adapt_runner(gh_runner: GhRunner) -> Callable[[list[str]], str]:
    """Adapt a ``(rc, stdout, stderr)`` gh runner to the trigger's stdout-only API.

    The trigger contract expects a runner that returns stdout and raises
    :class:`GitHubError` on non-zero exit. ``maestro doctor`` wants the
    richer tuple so it can surface stderr and the exit code in its own
    output, so a small adapter bridges the two shapes.
    """

    def runner(args: list[str]) -> str:
        code, stdout, stderr = gh_runner(args)
        if code != 0:
            raise GitHubError(f"gh {' '.join(args)} failed: {stderr.strip()}")
        return stdout

    return runner
