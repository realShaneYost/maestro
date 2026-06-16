"""Command-line entry point for Maestro.

This module wires up argument parsing and dispatches to subcommand handlers for
``run``, ``status``, ``once``, ``logs``, ``add``, ``clean``, ``doctor``,
``setup``, and ``update``.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from maestro.add import AddReport, add_issue_to_board
from maestro.clean import CleanReport, clean
from maestro.config import Config, ConfigError, RuntimePaths, load_config, runtime_paths
from maestro.core.scheduler import (
    DispatchVerdict,
    blocked_by_reason,
    explain_dispatch,
    failure_cap_reason,
)
from maestro.doctor import CheckLevel, CheckResult, run_checks
from maestro.github import GitHubError, StatusFieldTrigger
from maestro.logs import read_log
from maestro.loop import Loop
from maestro.manifest import CI_WORKFLOW_FILENAME
from maestro.runlog import consecutive_failures, read_runs
from maestro.setup import SetupReport, run_setup
from maestro.status import print_board_section, print_board_unreachable, render_recent, watch
from maestro.theme import ThemeError, load_theme
from maestro.update import (
    DependencyReconciliation,
    ReconcileStatus,
    ReconcileSummary,
    UpdateReport,
    run_update,
    summarize_reconciliation,
)
from maestro.worktree import WorktreeError, WorktreeManager


def _repo_root() -> Path:
    """Return the git toplevel of the current working directory.

    Maestro is run from inside the repository it drives; the working tree's
    git root is that repository.

    Raises:
        ConfigError: If the current directory is not inside a git repository.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as err:
        raise ConfigError(
            "maestro must be run from inside the git repository it drives; "
            "no git repository found at the current directory"
        ) from err
    return Path(result.stdout.strip())


def _load(args: argparse.Namespace) -> tuple[Config, RuntimePaths]:
    """Resolve the driven repo, load its config, and derive its runtime paths.

    The config path defaults to ``maestro.yml`` at the repo root; ``--config``
    overrides it (resolved relative to the current directory).

    Returns:
        The validated config and the sibling runtime-artifact paths.
    """
    repo_root = _repo_root()
    config_path = Path(args.config) if args.config else repo_root / "maestro.yml"
    config = load_config(config_path, repo_path=repo_root)
    return config, runtime_paths(repo_root)


def _setup_logging(console: Console) -> None:
    """Route log records through a themed rich handler."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        handlers=[RichHandler(console=console, show_path=False)],
    )


def _trigger(config: Config) -> StatusFieldTrigger:
    """Build the GitHub trigger source from configuration."""
    return StatusFieldTrigger(config.repo_slug, config.project_number, config.trigger_column)


def _worktree_manager(config: Config, paths: RuntimePaths) -> WorktreeManager:
    """Build the worktree manager for the configured target repo."""
    return WorktreeManager(
        config.repo_path, paths.worktrees_dir, worktree_setup=config.worktree_setup
    )


def _cmd_run(args: argparse.Namespace) -> int:
    """Handle ``maestro run`` — the poll/dispatch loop."""
    config, paths = _load(args)
    _setup_logging(Console(theme=load_theme(config.theme)))
    worktrees = _worktree_manager(config, paths)
    Loop(config, _trigger(config), worktrees, paths.logs_dir, paths.runs_log).run()
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Handle ``maestro status`` — recent worker activity, live with --watch."""
    config, paths = _load(args)
    console = Console(theme=load_theme(config.theme))
    if args.watch:
        watch(console, paths.logs_dir, config.repo_slug, gh_timeout=config.gh_timeout)
        return 0
    try:
        board = _trigger(config).poll()
    except GitHubError as err:
        print_board_unreachable(console, str(err))
    else:
        # Flag issues the scheduler holds back — for an open native blocker, or
        # for hitting the consecutive-failure cap. Only trigger-column, still-open
        # issues qualify, and a blocker outranks the cap, mirroring the
        # scheduler's own precedence so this view agrees with `once --dry-run`.
        failure_counts = consecutive_failures(read_runs(paths.runs_log))
        held: dict[int, str] = {}
        for issue in board.issues:
            if not issue.in_trigger_column or issue.number not in board.open_numbers:
                continue
            reason = blocked_by_reason(issue, board.open_numbers) or failure_cap_reason(
                issue, failure_counts, config.max_consecutive_failures
            )
            if reason is not None:
                held[issue.number] = reason
        print_board_section(
            console,
            board,
            config.trigger_column,
            config.blocked_column,
            config.review_column,
            held,
        )
    render_recent(console, paths.logs_dir, config.repo_slug, gh_timeout=config.gh_timeout)
    return 0


def _cmd_logs(args: argparse.Namespace) -> int:
    """Handle ``maestro logs`` — dump an issue's latest-run log.

    Prints the complete per-issue log to stdout, or writes it to ``--out PATH``.
    The log is dumped verbatim (not through the themed console) so its raw
    ``stream-json`` content is preserved byte-for-byte for piping or pasting.
    A missing log is reported with a clear message and a non-zero exit.
    """
    config, paths = _load(args)
    console = Console(theme=load_theme(config.theme))
    content = read_log(paths.logs_dir, args.issue)
    if content is None:
        console.print(
            f"No log found for issue #{args.issue}. It may not have been dispatched yet, "
            "or its logs were cleared (maestro clean --logs).",
            style="error",
        )
        return 1
    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8")
        console.print(f"Wrote issue #{args.issue} log to [issue]{out_path}[/issue].")
    else:
        sys.stdout.write(content)
    return 0


def _print_dry_run(console: Console, verdicts: list[DispatchVerdict]) -> None:
    """Print each board issue's dispatch verdict, dispatchable ones first."""
    if not verdicts:
        console.print("Dry run — no issues on the board.")
        return
    chosen = [v for v in verdicts if v.dispatch]
    skipped = [v for v in verdicts if not v.dispatch]
    if chosen:
        console.print(f"Dry run — would dispatch {len(chosen)} issue(s):")
        for verdict in chosen:
            console.print(
                f"  [issue]#{verdict.issue.number}[/issue]  {verdict.issue.title}  "
                f"[message]— {verdict.reason}[/message]"
            )
    else:
        console.print("Dry run — nothing would be dispatched.")
    if skipped:
        console.print(f"Skipped {len(skipped)} issue(s):")
        for verdict in skipped:
            console.print(
                f"  [issue]#{verdict.issue.number}[/issue]  {verdict.issue.title}  "
                f"[message]— {verdict.reason}[/message]"
            )


def _cmd_once(args: argparse.Namespace) -> int:
    """Handle ``maestro once`` — a single poll; --dry-run dispatches nothing."""
    config, paths = _load(args)
    console = Console(theme=load_theme(config.theme))
    trigger = _trigger(config)
    if args.dry_run:
        board = trigger.poll()
        failure_counts = consecutive_failures(read_runs(paths.runs_log))
        verdicts = explain_dispatch(
            board.issues,
            board.open_numbers,
            set(),
            config.max_concurrent,
            failure_counts,
            config.max_consecutive_failures,
        )
        _print_dry_run(console, verdicts)
        return 0
    _setup_logging(console)
    worktrees = _worktree_manager(config, paths)
    Loop(config, trigger, worktrees, paths.logs_dir, paths.runs_log).tick()
    return 0


def _print_clean_report(console: Console, report: CleanReport) -> None:
    """Print a human-readable summary of a clean operation."""
    console.print(f"Removed {len(report.worktrees_removed)} worktree(s).")
    console.print(f"Deleted {len(report.branches_deleted)} merged branch(es).")
    if report.logs_cleared:
        console.print("Cleared logs/.")
    if report.history_cleared:
        console.print("Truncated runs.jsonl.")


_LEVEL_MARK = {
    CheckLevel.PASS: ("[green]✓[/green]", "pass"),
    CheckLevel.FAIL: ("[error]✗[/error]", "fail"),
    CheckLevel.WARN: ("[yellow]![/yellow]", "warn"),
}


def _print_doctor_results(console: Console, results: list[CheckResult]) -> None:
    """Render preflight results one row per check."""
    for result in results:
        mark, _ = _LEVEL_MARK[result.level]
        console.print(
            f"  {mark}  [issue]{result.name}[/issue]  [message]{result.message}[/message]"
        )


def _cmd_doctor(args: argparse.Namespace) -> int:
    """Handle ``maestro doctor`` — read-only preflight checks."""
    config, _paths = _load(args)
    console = Console(theme=load_theme(config.theme))
    results = run_checks(config)
    _print_doctor_results(console, results)
    failures = sum(1 for r in results if r.level is CheckLevel.FAIL)
    warns = sum(1 for r in results if r.level is CheckLevel.WARN)
    console.print(f"\n{len(results)} check(s); " f"{failures} failure(s), {warns} warning(s).")
    return 1 if failures else 0


def _print_ci_setup(console: Console, report: SetupReport) -> None:
    """Render the CI-workflow outcome of a setup run."""
    workflow = f".github/workflows/{CI_WORKFLOW_FILENAME}"
    if not report.ci_managed:
        console.print("CI management is off (manage_ci: false); no workflow written.")
    elif report.ci_error:
        console.print(f"[error]Could not write CI workflow:[/error] {report.ci_error}")
    elif report.ci_written:
        console.print(
            f"Wrote CI skeleton to [issue]{workflow}[/issue]; commit it and configure a "
            "real test command to enable the worker's rework signal."
        )
    elif report.ci_already_present:
        console.print(f"CI workflow already present at [issue]{workflow}[/issue]; left untouched.")


def _print_setup_report(console: Console, report: SetupReport) -> None:
    """Render the setup report to the console."""
    if report.install is not None:
        console.print(f"[message]{report.install.summary}[/message]")
    _print_ci_setup(console, report)
    if report.aborted:
        console.print(f"[error]Setup aborted:[/error] {report.aborted}")
        return
    if report.priority_created:
        console.print(
            "Created Priority field with options " "[message]Critical, High, Normal, Low[/message]."
        )
    elif report.priority_already_present:
        console.print("Priority field already present; left untouched.")
    if report.status_present:
        console.print("Status field has every configured column.")
    else:
        console.print(
            "[error]Status field is missing option(s):[/error] "
            + ", ".join(repr(name) for name in report.status_missing)
        )
        console.print(
            "Add them manually: project board -> ⋯ -> Settings -> Fields -> "
            "Status -> Add option. Maestro setup never mutates the Status field."
        )
    console.print(
        "\nRun [issue]maestro doctor[/issue] to verify, then "
        "[issue]maestro once --dry-run[/issue]."
    )


def _cmd_setup(args: argparse.Namespace) -> int:
    """Handle ``maestro setup`` — narrowly scoped board bootstrap."""
    config, _paths = _load(args)
    console = Console(theme=load_theme(config.theme))
    report = run_setup(config)
    _print_setup_report(console, report)
    if report.aborted or report.ci_error or not report.status_present:
        return 1
    return 0


_RECONCILE_MARK = {
    ReconcileStatus.IN_SYNC: "[green]✓[/green]",
    ReconcileStatus.DRIFTED: "[yellow]![/yellow]",
    ReconcileStatus.MISSING: "[error]✗[/error]",
    ReconcileStatus.NO_TEMPLATE: "[message]·[/message]",
    ReconcileStatus.TEMPLATE_UNAVAILABLE: "[yellow]?[/yellow]",
    ReconcileStatus.INFORMATIONAL: "[message]i[/message]",
}


def _print_reconciliation(console: Console, item: DependencyReconciliation) -> None:
    """Render one dependency's reconciliation row and any new config keys."""
    mark = _RECONCILE_MARK[item.status]
    console.print(f"  {mark}  [issue]{item.name}[/issue]  [message]{item.detail}[/message]")
    for key in item.new_config_keys:
        console.print(
            f"        [yellow]new config key[/yellow] [issue]{key.name}[/issue] "
            f"[message](default: {key.default})[/message]"
        )


def _print_update_report(console: Console, report: UpdateReport) -> None:
    """Render the upgrade outcome and the editable-dependency reconciliation."""
    console.print(f"[message]{report.install.summary}[/message]")
    upgrade = report.upgrade
    style = "error" if upgrade.attempted and not upgrade.ok else "message"
    console.print(f"[{style}]{upgrade.detail}[/{style}]")
    if upgrade.attempted and not upgrade.ok:
        for command in upgrade.commands:
            if not command.ok and command.stderr.strip():
                console.print(
                    f"  [error]uv {' '.join(command.args)}[/error]: {command.stderr.strip()}"
                )
    console.print("\nEditable-dependency reconciliation:")
    for item in report.reconciliations:
        _print_reconciliation(console, item)
    _print_reconcile_summary(console, summarize_reconciliation(report.reconciliations))


def _print_reconcile_summary(console: Console, summary: ReconcileSummary) -> None:
    """Render the reconciliation roll-up, never claiming sync for a no-op.

    "All editable dependencies are in sync." prints only when something was
    actually compared and matched and nothing drifted, went missing, or could not
    be verified. Anything that could not be compared yields a WARNING summarizing
    which and why, and a run that compared nothing at all is reported as a no-op —
    so a vacuous reconciliation can never read as a clean bill of health.
    """
    if summary.all_in_sync:
        console.print("\nAll editable dependencies are in sync.")
        _print_informational_note(console, summary)
        return
    if summary.unverifiable:
        noun = "dependency" if len(summary.unverifiable) == 1 else "dependencies"
        console.print(
            f"\n[error]WARNING: could not verify {len(summary.unverifiable)} {noun} — "
            "not claiming sync.[/error]"
        )
        for item in summary.unverifiable:
            console.print(
                f"  [error]?[/error]  [issue]{item.name}[/issue]  "
                f"[message]{item.detail}[/message]"
            )
    if summary.needs_attention:
        console.print(
            "\nNothing was overwritten. Review the flagged file(s) above and reconcile by hand."
        )
    if summary.nothing_compared:
        console.print(
            "\n[error]Nothing was compared[/error] — no dependency ships a packaged "
            "template to check against, so this is a no-op, not a clean bill of health."
        )
    _print_informational_note(console, summary)


def _print_informational_note(console: Console, summary: ReconcileSummary) -> None:
    """Render a quiet note for dependencies that differ from their template by design.

    The worker skill is an implementation of a model-agnostic contract, so it
    differs from the shipped template permanently. That is not drift and needs no
    action, so it is surfaced as a muted note — never a warning — even alongside an
    overall "in sync" roll-up.
    """
    if not summary.informational:
        return
    noun = "dependency" if len(summary.informational) == 1 else "dependencies"
    names = ", ".join(item.name for item in summary.informational)
    console.print(
        f"\n[message]Informational: {len(summary.informational)} {noun} ({names}) "
        "differ from the shipped template by design — not drift, nothing to "
        "reconcile.[/message]"
    )


def _cmd_update(args: argparse.Namespace) -> int:
    """Handle ``maestro update`` — reinstall the tool and reconcile deps."""
    config, _paths = _load(args)
    console = Console(theme=load_theme(config.theme))
    report = run_update(config)
    _print_update_report(console, report)
    return 1 if report.upgrade.attempted and not report.upgrade.ok else 0


def _print_add_report(console: Console, report: AddReport) -> None:
    """Render the outcome of an ``maestro add`` run."""
    if report.aborted:
        console.print(f"[error]Could not add issue #{report.issue}:[/error] {report.aborted}")
        return
    console.print(
        f"Added [issue]#{report.issue}[/issue] to the board in "
        f"[message]{report.column!r}[/message]. "
        "Move its card to the trigger column when it is ready to dispatch."
    )


def _cmd_add(args: argparse.Namespace) -> int:
    """Handle ``maestro add`` — add an existing issue to the board in one step."""
    config, _paths = _load(args)
    console = Console(theme=load_theme(config.theme))
    report = add_issue_to_board(config, args.issue, status=args.status)
    _print_add_report(console, report)
    return 1 if report.aborted else 0


def _cmd_clean(args: argparse.Namespace) -> int:
    """Handle ``maestro clean`` — prune worktrees, branches, and optionally logs."""
    config, paths = _load(args)
    worktrees = _worktree_manager(config, paths)
    report = clean(
        worktrees,
        paths.logs_dir,
        paths.runs_log,
        clear_logs=args.logs or args.all,
        clear_history=args.history or args.all,
    )
    _print_clean_report(Console(theme=load_theme(config.theme)), report)
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the Maestro argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="maestro",
        description="Watch a GitHub Projects board and dispatch coding agents.",
    )
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="path to the config file (default: maestro.yml at the repo root)",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run the poll/dispatch loop")
    run.set_defaults(func=_cmd_run)

    status = sub.add_parser("status", help="show worker status")
    status.add_argument("--watch", action="store_true", help="live-refreshing view")
    status.set_defaults(func=_cmd_status)

    once = sub.add_parser("once", help="run a single poll cycle")
    once.add_argument("--dry-run", action="store_true", help="print decisions; dispatch nothing")
    once.set_defaults(func=_cmd_once)

    logs = sub.add_parser("logs", help="dump an issue's latest-run worker log")
    logs.add_argument("issue", type=int, help="issue number whose log to dump")
    logs.add_argument("--out", default=None, help="write the log to this file instead of stdout")
    logs.set_defaults(func=_cmd_logs)

    add_cmd = sub.add_parser("add", help="add an existing issue to the board")
    add_cmd.add_argument("issue", type=int, help="issue number to add to the board")
    add_cmd.add_argument(
        "--status",
        default=None,
        help="Status column to place the card in (default: the board's leftmost column)",
    )
    add_cmd.set_defaults(func=_cmd_add)

    clean_cmd = sub.add_parser("clean", help="prune worktrees and stale branches")
    clean_cmd.add_argument("--logs", action="store_true", help="also clear logs/")
    clean_cmd.add_argument("--history", action="store_true", help="also truncate runs.jsonl")
    clean_cmd.add_argument("--all", action="store_true", help="clean everything")
    clean_cmd.set_defaults(func=_cmd_clean)

    doctor = sub.add_parser("doctor", help="read-only preflight checks")
    doctor.set_defaults(func=_cmd_doctor)

    setup_cmd = sub.add_parser("setup", help="bootstrap a board's Priority field")
    setup_cmd.set_defaults(func=_cmd_setup)

    update_cmd = sub.add_parser(
        "update", help="reinstall the Maestro tool and reconcile editable deps"
    )
    update_cmd.set_defaults(func=_cmd_update)

    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and dispatch to the selected subcommand.

    Args:
        argv: Argument list, defaulting to ``sys.argv[1:]``.

    Returns:
        The process exit code.
    """
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        exit_code: int = args.func(args)
    except (ConfigError, GitHubError, ThemeError, WorktreeError) as err:
        print(f"error: {err}", file=sys.stderr)
        return 1
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
