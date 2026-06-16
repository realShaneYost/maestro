"""Tests for the Maestro command-line parser."""

from __future__ import annotations

import io

from rich.console import Console

from maestro.cli import _print_reconcile_summary, build_parser
from maestro.update import DependencyReconciliation, ReconcileStatus, summarize_reconciliation


def _render_summary(*reconciliations: DependencyReconciliation) -> str:
    console = Console(file=io.StringIO(), force_terminal=False, no_color=True, width=200)
    _print_reconcile_summary(console, summarize_reconciliation(reconciliations))
    return console.file.getvalue()


def test_parser_accepts_all_subcommands():
    parser = build_parser()
    for command in ("run", "status", "once", "clean", "doctor", "setup", "update"):
        assert parser.parse_args([command]).command == command


def test_add_subcommand_parses_issue_and_status():
    args = build_parser().parse_args(["add", "42", "--status", "Backlog"])
    assert args.command == "add"
    assert args.issue == 42
    assert args.status == "Backlog"


def test_add_status_defaults_to_none():
    args = build_parser().parse_args(["add", "7"])
    assert args.issue == 7
    assert args.status is None


def test_logs_subcommand_parses_issue_and_out():
    args = build_parser().parse_args(["logs", "42", "--out", "dump.log"])
    assert args.command == "logs"
    assert args.issue == 42
    assert args.out == "dump.log"


def test_logs_issue_defaults_out_to_none():
    assert build_parser().parse_args(["logs", "7"]).out is None


def test_status_watch_flag():
    assert build_parser().parse_args(["status", "--watch"]).watch is True


def test_once_dry_run_flag():
    assert build_parser().parse_args(["once", "--dry-run"]).dry_run is True


def test_clean_all_flag():
    assert build_parser().parse_args(["clean", "--all"]).all is True


def _recon(name: str, status: ReconcileStatus) -> DependencyReconciliation:
    return DependencyReconciliation(name, name, status, "cannot compare")


def test_update_summary_prints_in_sync_when_all_matched():
    out = _render_summary(
        _recon("worker skill", ReconcileStatus.IN_SYNC),
        _recon("config", ReconcileStatus.IN_SYNC),
        _recon("CI workflow", ReconcileStatus.NO_TEMPLATE),
    )
    assert "All editable dependencies are in sync." in out


def test_update_summary_uncomparable_warns_and_never_claims_sync():
    """The bug fix: an unverifiable dependency must warn, not print 'in sync'."""
    out = _render_summary(
        _recon("worker skill", ReconcileStatus.TEMPLATE_UNAVAILABLE),
        _recon("config", ReconcileStatus.TEMPLATE_UNAVAILABLE),
        _recon("CI workflow", ReconcileStatus.NO_TEMPLATE),
    )
    assert "in sync" not in out
    assert "WARNING" in out
    assert "could not verify 2 dependencies" in out


def test_update_summary_nothing_compared_is_distinct_from_clean():
    out = _render_summary(
        _recon("CI workflow", ReconcileStatus.NO_TEMPLATE),
        _recon("other", ReconcileStatus.NO_TEMPLATE),
    )
    assert "in sync" not in out
    assert "Nothing was compared" in out
