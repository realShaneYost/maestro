"""Tests for worker command construction and process spawning."""

from __future__ import annotations

import sys

from maestro.dispatch import build_worker_command, spawn_worker


def test_build_worker_command_renders_issue():
    assert build_worker_command("/cc-work {issue}", 42) == [
        "claude",
        "-p",
        "/cc-work 42",
    ]


def test_build_worker_command_supports_richer_templates():
    cmd = build_worker_command("work issue #{issue} now", 7)
    assert cmd[-1] == "work issue #7 now"


def test_spawn_worker_runs_and_captures_output(tmp_path):
    log_path = tmp_path / "logs" / "issue-1.log"
    command = [sys.executable, "-c", "print('hello from worker')"]
    process = spawn_worker(command, cwd=tmp_path, log_path=log_path)
    assert process.wait(timeout=10) == 0
    assert "hello from worker" in log_path.read_text(encoding="utf-8")


def test_spawn_worker_appends_to_existing_log(tmp_path):
    log_path = tmp_path / "issue-2.log"
    log_path.write_text("previous run\n", encoding="utf-8")
    command = [sys.executable, "-c", "print('second run')"]
    spawn_worker(command, cwd=tmp_path, log_path=log_path).wait(timeout=10)
    content = log_path.read_text(encoding="utf-8")
    assert "previous run" in content
    assert "second run" in content
