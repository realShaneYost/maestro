"""Tests for the Maestro command-line parser."""

from __future__ import annotations

from maestro.cli import build_parser


def test_parser_accepts_all_subcommands():
    parser = build_parser()
    for command in ("run", "status", "once", "clean", "doctor", "setup", "update"):
        assert parser.parse_args([command]).command == command


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
