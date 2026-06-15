"""Tests for ``maestro.install`` — install-source introspection."""

from __future__ import annotations

from pathlib import Path

from maestro.install import (
    InstallSource,
    classify_source,
    detect_install,
)


def test_classify_uv_tool():
    path = Path("/home/u/.local/share/uv/tools/maestro/lib/python3.11/site-packages/maestro")
    assert classify_source(path) is InstallSource.UV_TOOL


def test_classify_pipx():
    path = Path("/home/u/.local/pipx/venvs/maestro/lib/python3.11/site-packages/maestro")
    assert classify_source(path) is InstallSource.PIPX


def test_classify_source_checkout(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'maestro'\n")
    (tmp_path / ".git").mkdir()
    package_dir = tmp_path / "maestro"
    package_dir.mkdir()
    assert classify_source(package_dir) is InstallSource.SOURCE


def test_classify_unknown_without_markers(tmp_path: Path):
    package_dir = tmp_path / "somewhere" / "maestro"
    package_dir.mkdir(parents=True)
    assert classify_source(package_dir) is InstallSource.UNKNOWN


def test_detect_install_reports_version_and_source():
    path = Path("/home/u/.local/share/uv/tools/maestro/lib/python3.11/site-packages/maestro")
    info = detect_install(package_dir=path, version="9.9.9")
    assert info.version == "9.9.9"
    assert info.source is InstallSource.UV_TOOL
    assert info.location == path


def test_is_isolated_tool_true_for_uv_and_pipx():
    uv = detect_install(package_dir=Path("/x/uv/tools/maestro"), version="1.0")
    pipx = detect_install(package_dir=Path("/x/pipx/venvs/maestro"), version="1.0")
    assert uv.is_isolated_tool is True
    assert pipx.is_isolated_tool is True


def test_is_isolated_tool_false_for_source(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("x")
    (tmp_path / ".git").mkdir()
    package_dir = tmp_path / "maestro"
    package_dir.mkdir()
    info = detect_install(package_dir=package_dir, version="1.0")
    assert info.source is InstallSource.SOURCE
    assert info.is_isolated_tool is False


def test_summary_mentions_version_and_install_source():
    info = detect_install(package_dir=Path("/x/uv/tools/maestro/lib/maestro"), version="2.3.4")
    assert "2.3.4" in info.summary
    assert "uv tool" in info.summary


def test_summary_flags_source_checkout(tmp_path: Path):
    (tmp_path / "pyproject.toml").write_text("x")
    (tmp_path / ".git").mkdir()
    package_dir = tmp_path / "maestro"
    package_dir.mkdir()
    info = detect_install(package_dir=package_dir, version="2.3.4")
    assert "source checkout" in info.summary
