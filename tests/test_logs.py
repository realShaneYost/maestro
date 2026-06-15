"""Tests for locating a worker's latest-run log."""

from __future__ import annotations

from maestro.logs import log_path_for, read_log


def test_log_path_for_uses_issue_filename(tmp_path):
    assert log_path_for(tmp_path, 58) == tmp_path / "issue-58.log"


def test_read_log_returns_full_contents(tmp_path):
    content = '{"type":"system"}\n{"type":"result","subtype":"done"}\n'
    (tmp_path / "issue-12.log").write_text(content, encoding="utf-8")
    assert read_log(tmp_path, 12) == content


def test_read_log_missing_file_returns_none(tmp_path):
    assert read_log(tmp_path, 99) is None


def test_read_log_missing_dir_returns_none(tmp_path):
    assert read_log(tmp_path / "no-such-dir", 1) is None


def test_read_log_empty_file_returns_empty_string(tmp_path):
    (tmp_path / "issue-3.log").write_text("", encoding="utf-8")
    assert read_log(tmp_path, 3) == ""


def test_read_log_replaces_undecodable_bytes(tmp_path):
    (tmp_path / "issue-4.log").write_bytes(b"ok\xff\n")
    result = read_log(tmp_path, 4)
    assert result is not None
    assert result.startswith("ok")
