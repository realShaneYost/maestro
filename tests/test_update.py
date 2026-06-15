"""Tests for ``maestro update`` — tool reinstall and dependency reconciliation."""

from __future__ import annotations

from pathlib import Path

from maestro.config import Config
from maestro.install import InstallInfo, InstallSource
from maestro.update import (
    ReconcileStatus,
    UvRunner,
    _installed_version,
    _new_config_keys,
    _upgrade_tool,
    reconcile,
    run_update,
)


def _config(repo_path: Path) -> Config:
    return Config(
        repo_path=repo_path,
        repo_slug="owner/repo",
        project_number=1,
        trigger_column="In Progress",
        max_concurrent=2,
        poll_interval=60,
        worker_prompt="/cc-work {issue}",
        theme="nord",
        worktree_setup=None,
        review_column="In Review",
        blocked_column="Blocked",
    )


def _install(source: InstallSource, version: str = "0.1.0") -> InstallInfo:
    return InstallInfo(version=version, source=source, location=Path("/x/uv/tools/maestro"))


def _uv_runner(
    *, reinstall_code: int = 0, list_version: str | None = "0.2.0", reinstall_stderr: str = ""
) -> tuple[UvRunner, list[list[str]]]:
    """Build a fake uv runner plus a log of the calls it received."""
    calls: list[list[str]] = []

    def run(args: list[str]) -> tuple[int, str, str]:
        calls.append(list(args))
        if args[:2] == ["cache", "clean"]:
            return 0, "", ""
        if args[:2] == ["tool", "upgrade"]:
            return reinstall_code, "", reinstall_stderr
        if args[:2] == ["tool", "list"]:
            if list_version is None:
                return 0, "ruff v0.5.0\n", ""
            return 0, f"maestro v{list_version}\n- maestro\n", ""
        return 0, "", ""

    return run, calls


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _stage_templates(package_root: Path, *, worker: str, config: str) -> None:
    _write(package_root / "templates" / "worker-skill.md", worker)
    _write(package_root / "templates" / "maestro.example.yml", config)


# --- version verification --------------------------------------------------


def test_installed_version_parses_tool_list():
    runner, _calls = _uv_runner(list_version="1.2.3")
    assert _installed_version(runner) == "1.2.3"


def test_installed_version_none_when_absent():
    runner, _calls = _uv_runner(list_version=None)
    assert _installed_version(runner) is None


def test_installed_version_none_when_uv_fails():
    def runner(args: list[str]) -> tuple[int, str, str]:
        return 1, "", "boom"

    assert _installed_version(runner) is None


# --- upgrade phase ---------------------------------------------------------


def test_upgrade_uv_tool_reports_version_change():
    runner, calls = _uv_runner(list_version="0.2.0")
    result = _upgrade_tool(_install(InstallSource.UV_TOOL, "0.1.0"), runner)
    assert result.attempted is True
    assert result.ok is True
    assert result.version_after == "0.2.0"
    assert "0.1.0 -> 0.2.0" in result.detail
    # Cache is cleaned and the reinstall bypasses it.
    assert ["cache", "clean", "maestro"] in calls
    assert any(c[:2] == ["tool", "upgrade"] and "--no-cache" in c for c in calls)


def test_upgrade_unchanged_version_is_surfaced_not_swallowed():
    """A same-version reinstall must not pass silently — the no-op footgun."""
    runner, _calls = _uv_runner(list_version="0.1.0")
    result = _upgrade_tool(_install(InstallSource.UV_TOOL, "0.1.0"), runner)
    assert result.ok is True
    assert result.version_after == "0.1.0"
    assert "unchanged" in result.detail
    assert "doctor" in result.detail


def test_upgrade_failure_reports_stderr():
    runner, _calls = _uv_runner(reinstall_code=1, reinstall_stderr="network down")
    result = _upgrade_tool(_install(InstallSource.UV_TOOL), runner)
    assert result.attempted is True
    assert result.ok is False
    assert "network down" in result.detail
    assert result.version_after is None


def test_upgrade_source_checkout_does_not_run_uv():
    runner, calls = _uv_runner()
    result = _upgrade_tool(_install(InstallSource.SOURCE), runner)
    assert result.attempted is False
    assert result.ok is True
    assert calls == []
    assert "source checkout" in result.detail


def test_upgrade_pipx_gives_manual_guidance():
    runner, calls = _uv_runner()
    result = _upgrade_tool(_install(InstallSource.PIPX), runner)
    assert result.attempted is False
    assert calls == []
    assert "pipx" in result.detail


def test_upgrade_unknown_source_is_not_driven():
    runner, calls = _uv_runner()
    result = _upgrade_tool(_install(InstallSource.UNKNOWN), runner)
    assert result.attempted is False
    assert calls == []


# --- reconciliation --------------------------------------------------------


def test_reconcile_in_sync(tmp_path):
    package_root = tmp_path / "pkg"
    repo = tmp_path / "repo"
    _stage_templates(package_root, worker="skill body\n", config="repo_slug: x\n")
    _write(repo / ".claude" / "skills" / "cc-work" / "SKILL.md", "skill body\n")
    _write(repo / "maestro.yml", "repo_slug: x\n")
    (repo / ".github" / "workflows").mkdir(parents=True)

    results = {r.name: r for r in reconcile(repo, package_root)}
    assert results["worker skill"].status is ReconcileStatus.IN_SYNC
    assert results["config"].status is ReconcileStatus.IN_SYNC
    assert results["CI workflow"].status is ReconcileStatus.NO_TEMPLATE


def test_reconcile_drift_is_reported_not_overwritten(tmp_path):
    package_root = tmp_path / "pkg"
    repo = tmp_path / "repo"
    _stage_templates(package_root, worker="new upstream skill\n", config="repo_slug: x\n")
    skill = repo / ".claude" / "skills" / "cc-work" / "SKILL.md"
    _write(skill, "my edited skill\n")
    _write(repo / "maestro.yml", "repo_slug: x\n")

    results = {r.name: r for r in reconcile(repo, package_root)}
    assert results["worker skill"].status is ReconcileStatus.DRIFTED
    assert "will not overwrite" in results["worker skill"].detail
    # The user's copy is untouched.
    assert skill.read_text(encoding="utf-8") == "my edited skill\n"


def test_reconcile_missing_dependency(tmp_path):
    package_root = tmp_path / "pkg"
    repo = tmp_path / "repo"
    _stage_templates(package_root, worker="skill\n", config="repo_slug: x\n")
    _write(repo / "maestro.yml", "repo_slug: x\n")

    results = {r.name: r for r in reconcile(repo, package_root)}
    assert results["worker skill"].status is ReconcileStatus.MISSING


def test_reconcile_template_unavailable(tmp_path):
    """A pure-wheel install with no bundled templates degrades safely."""
    package_root = tmp_path / "pkg"  # nothing staged here
    repo = tmp_path / "repo"
    _write(repo / "maestro.yml", "repo_slug: x\n")

    results = {r.name: r for r in reconcile(repo, package_root)}
    assert results["config"].status is ReconcileStatus.TEMPLATE_UNAVAILABLE


def test_reconcile_surfaces_new_config_keys(tmp_path):
    package_root = tmp_path / "pkg"
    repo = tmp_path / "repo"
    template = "repo_slug: your-org/your-project\nproject_number: 1\nnew_key: surprise\n"
    _stage_templates(package_root, worker="skill\n", config=template)
    _write(repo / ".claude" / "skills" / "cc-work" / "SKILL.md", "skill\n")
    _write(repo / "maestro.yml", "repo_slug: mine\nproject_number: 7\n")

    results = {r.name: r for r in reconcile(repo, package_root)}
    config_result = results["config"]
    assert config_result.status is ReconcileStatus.DRIFTED
    keys = {k.name: k.default for k in config_result.new_config_keys}
    assert keys == {"new_key": "surprise"}


def test_new_config_keys_handles_unparsable_yaml():
    assert _new_config_keys("repo_slug: x\n", ": : not yaml\n") == ()


# --- end-to-end ------------------------------------------------------------


def test_run_update_combines_upgrade_and_reconcile(tmp_path):
    package_root = tmp_path / "pkg"
    repo = tmp_path / "repo"
    _stage_templates(package_root, worker="skill\n", config="repo_slug: x\n")
    _write(repo / ".claude" / "skills" / "cc-work" / "SKILL.md", "skill\n")
    _write(repo / "maestro.yml", "repo_slug: x\n")
    runner, _calls = _uv_runner(list_version="0.2.0")

    report = run_update(
        _config(repo),
        uv_runner=runner,
        install_info=_install(InstallSource.UV_TOOL, "0.1.0"),
        package_root=package_root,
    )
    assert report.upgrade.ok is True
    assert report.upgrade.version_after == "0.2.0"
    assert len(report.reconciliations) == 3
    assert all(
        r.status in (ReconcileStatus.IN_SYNC, ReconcileStatus.NO_TEMPLATE)
        for r in report.reconciliations
    )
