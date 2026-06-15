"""Tests for the user-editable dependency manifest."""

from __future__ import annotations

import pytest

from maestro.manifest import MANIFEST, PACKAGE_ROOT, WORKER_SKILL, Dependency, read_template


def test_manifest_enumerates_the_three_locations():
    locations = {dep.location for dep in MANIFEST}
    assert ".claude/skills/cc-work/SKILL.md" in locations
    assert "maestro.yml" in locations
    assert ".github/workflows" in locations


def test_manifest_names_are_unique():
    names = [dep.name for dep in MANIFEST]
    assert len(names) == len(set(names))


def test_worker_skill_entry_uses_the_shared_constant():
    assert any(dep.name == WORKER_SKILL for dep in MANIFEST)


def test_destination_joins_repo_path(tmp_path):
    dep = next(dep for dep in MANIFEST if dep.location == "maestro.yml")
    assert dep.destination(tmp_path) == tmp_path / "maestro.yml"


def test_is_present_file(tmp_path):
    dep = next(dep for dep in MANIFEST if dep.location == "maestro.yml")
    assert dep.is_present(tmp_path) is False
    (tmp_path / "maestro.yml").write_text("repo_slug: x", encoding="utf-8")
    assert dep.is_present(tmp_path) is True


def test_is_present_directory_requires_non_empty(tmp_path):
    dep = next(dep for dep in MANIFEST if dep.is_directory)
    workflows = tmp_path / ".github" / "workflows"
    workflows.mkdir(parents=True)
    assert dep.is_present(tmp_path) is False
    (workflows / "ci.yml").write_text("on: push", encoding="utf-8")
    assert dep.is_present(tmp_path) is True


def test_template_path_none_when_no_template():
    dep = next(dep for dep in MANIFEST if dep.template is None)
    assert dep.template_path() is None


def test_shipped_templates_resolve_and_exist():
    # Every templated entry must point at a real file in the source tree, so the
    # future ``maestro update`` can read it. PACKAGE_ROOT is the repo root here.
    for dep in MANIFEST:
        if dep.template is None:
            continue
        path = dep.template_path()
        assert path is not None
        assert path.is_file(), f"{dep.name} template missing at {path}"


def test_read_template_returns_content():
    worker_skill = next(dep for dep in MANIFEST if dep.name == WORKER_SKILL)
    content = read_template(worker_skill)
    assert "Worker skill template" in content


def test_read_template_raises_without_template():
    dep = Dependency(
        name="no-template",
        description="x",
        location="x",
        is_directory=False,
        required=False,
        template=None,
    )
    with pytest.raises(ValueError, match="no source template"):
        read_template(dep)


def test_package_root_holds_the_templates():
    assert (PACKAGE_ROOT / "templates" / "worker-skill.md").is_file()
    assert (PACKAGE_ROOT / "maestro.example.yml").is_file()
