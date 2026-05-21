"""Command-line entry point for Maestro.

This module wires up argument parsing and dispatches to subcommand handlers for
``run``, ``status``, ``once``, and ``clean``.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from rich.console import Console
from rich.logging import RichHandler

from maestro.clean import CleanReport, clean
from maestro.config import Config, ConfigError, load_config
from maestro.core.models import Issue
from maestro.core.scheduler import dispatchable_issues
from maestro.github import GitHubError, StatusFieldTrigger
from maestro.loop import Loop
from maestro.status import render_once, watch
from maestro.theme import ThemeError, load_theme
from maestro.worktree import WorktreeError, WorktreeManager

# Directories and files Maestro uses for runtime artifacts, relative to the
# working directory where the command is invoked.
_LOGS_DIR = Path("logs")
_WORKTREES_DIR = Path("worktrees")
_RUNS_LOG = Path("runs.jsonl")


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


def _cmd_run(args: argparse.Namespace) -> int:
    """Handle ``maestro run`` — the poll/dispatch loop."""
    config = load_config(Path(args.config))
    _setup_logging(Console(theme=load_theme(config.theme)))
    worktrees = WorktreeManager(config.repo_path, _WORKTREES_DIR)
    Loop(config, _trigger(config), worktrees, _LOGS_DIR, _RUNS_LOG).run()
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    """Handle ``maestro status`` — a snapshot, or a live view with --watch."""
    config = load_config(Path(args.config))
    console = Console(theme=load_theme(config.theme))
    worktrees = WorktreeManager(config.repo_path, _WORKTREES_DIR)
    if args.watch:
        watch(console, worktrees, _LOGS_DIR, config.max_concurrent)
    else:
        render_once(console, worktrees, _LOGS_DIR, config.max_concurrent)
    return 0


def _print_dry_run(console: Console, issues: list[Issue]) -> None:
    """Print the issues a dry-run poll would dispatch."""
    if not issues:
        console.print("Dry run — nothing would be dispatched.")
        return
    console.print(f"Dry run — would dispatch {len(issues)} issue(s):")
    for issue in issues:
        console.print(f"  [running]#{issue.number}[/running]  {issue.title}")


def _cmd_once(args: argparse.Namespace) -> int:
    """Handle ``maestro once`` — a single poll; --dry-run dispatches nothing."""
    config = load_config(Path(args.config))
    console = Console(theme=load_theme(config.theme))
    trigger = _trigger(config)
    if args.dry_run:
        board = trigger.poll()
        ready = dispatchable_issues(board.issues, board.open_numbers, set(), config.max_concurrent)
        _print_dry_run(console, ready)
        return 0
    _setup_logging(console)
    worktrees = WorktreeManager(config.repo_path, _WORKTREES_DIR)
    Loop(config, trigger, worktrees, _LOGS_DIR, _RUNS_LOG).tick()
    return 0


def _print_clean_report(console: Console, report: CleanReport) -> None:
    """Print a human-readable summary of a clean operation."""
    console.print(f"Removed {len(report.worktrees_removed)} worktree(s).")
    console.print(f"Deleted {len(report.branches_deleted)} merged branch(es).")
    if report.logs_cleared:
        console.print("Cleared logs/.")
    if report.history_cleared:
        console.print("Truncated runs.jsonl.")


def _cmd_clean(args: argparse.Namespace) -> int:
    """Handle ``maestro clean`` — prune worktrees, branches, and optionally logs."""
    config = load_config(Path(args.config))
    worktrees = WorktreeManager(config.repo_path, _WORKTREES_DIR)
    report = clean(
        worktrees,
        _LOGS_DIR,
        _RUNS_LOG,
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
    parser.add_argument("-c", "--config", default="maestro.yml", help="path to the config file")
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
