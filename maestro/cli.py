"""Command-line entry point for Maestro.

This module wires up argument parsing and dispatches to subcommand handlers for
``run``, ``status``, ``once``, and ``clean``.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from maestro.clean import CleanReport, clean
from maestro.config import Config, ConfigError, RuntimePaths, load_config, runtime_paths
from maestro.core.scheduler import DispatchVerdict, blocked_by_reason, explain_dispatch
from maestro.doctor import CheckLevel, CheckResult, run_checks
from maestro.github import GitHubError, StatusFieldTrigger
from maestro.loop import Loop
from maestro.setup import SetupReport, run_setup
from maestro.status import print_board_section, print_board_unreachable, render_recent, watch
from maestro.theme import ThemeError, load_theme
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
        watch(console, paths.logs_dir, config.repo_slug)
        return 0
    try:
        board = _trigger(config).poll()
    except GitHubError as err:
        print_board_unreachable(console, str(err))
    else:
        # Flag issues the scheduler holds back for an open native blocker.
        # Only trigger-column, still-open issues qualify — the same precedence
        # the scheduler uses — so this view agrees with `once --dry-run`.
        held = {
            issue.number: reason
            for issue in board.issues
            if issue.in_trigger_column
            and issue.number in board.open_numbers
            and (reason := blocked_by_reason(issue, board.open_numbers)) is not None
        }
        print_board_section(
            console,
            board,
            config.trigger_column,
            config.blocked_column,
            config.review_column,
            held,
        )
    render_recent(console, paths.logs_dir, config.repo_slug)
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
        verdicts = explain_dispatch(board.issues, board.open_numbers, set(), config.max_concurrent)
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


def _print_setup_report(console: Console, report: SetupReport) -> None:
    """Render the setup report to the console."""
    if report.install is not None:
        console.print(f"[message]{report.install.summary}[/message]")
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
    if report.aborted or not report.status_present:
        return 1
    return 0


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

    clean_cmd = sub.add_parser("clean", help="prune worktrees and stale branches")
    clean_cmd.add_argument("--logs", action="store_true", help="also clear logs/")
    clean_cmd.add_argument("--history", action="store_true", help="also truncate runs.jsonl")
    clean_cmd.add_argument("--all", action="store_true", help="clean everything")
    clean_cmd.set_defaults(func=_cmd_clean)

    doctor = sub.add_parser("doctor", help="read-only preflight checks")
    doctor.set_defaults(func=_cmd_doctor)

    setup_cmd = sub.add_parser("setup", help="bootstrap a board's Priority field")
    setup_cmd.set_defaults(func=_cmd_setup)

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
