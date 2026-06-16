"""Canonical manifest of Maestro's user-editable dependencies.

Maestro drops a handful of artifacts into every repo it drives, and each one is
pinned to a location its host tool dictates — they cannot be physically
consolidated into one folder:

* the **worker skill** at ``.claude/skills/cc-work/SKILL.md`` (Claude Code only
  discovers a skill at that path);
* the **config** ``maestro.yml`` at the repo root (Maestro reads ``./maestro.yml``
  when run from inside the repo); and
* the optional **CI workflow** under ``.github/workflows/`` (GitHub Actions only
  runs workflows from that directory).

This module is the single *logical* place that enumerates them. Each entry names
the dependency, the path it belongs at inside a driven repo, and the source
template that ships inside the installed Maestro package. ``maestro doctor``
reports presence against this list (see :mod:`maestro.doctor`), the README
documents the same three locations, and ``maestro update`` reconciles a repo's
copies against the packaged templates by walking the same manifest.

The reference templates are bundled *inside* the ``maestro`` package, under
``maestro/templates/`` (see ``pyproject.toml``'s ``package-data``), and are
resolved at runtime via :func:`importlib.resources.files` rather than a
filesystem path relative to the cwd or the source checkout. That makes them
resolvable after a plain ``uv tool install`` — the bug this design fixes — and
keeps them a read-only reference: they are never written into a driven repo and
never overwrite a user's editable copies.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path

# Root the bundled templates resolve against: the installed ``maestro`` package
# itself. ``resources.files`` returns a Traversable that works for a wheel
# install, an editable install, or a source checkout alike. Tests inject a
# ``Path`` in its place — ``Path`` satisfies the Traversable interface.
PACKAGE_FILES: Traversable = resources.files("maestro")


def _resolve(package_root: Traversable, relpath: str) -> Traversable:
    """Resolve a POSIX-relative template path against a package root.

    Joins one path segment at a time so the result works for any Traversable,
    not only those whose ``__truediv__`` splits embedded separators.

    Args:
        package_root: Root the template path is resolved against.
        relpath: POSIX-relative path to a bundled template (e.g.
            ``"templates/worker-skill.md"``).

    Returns:
        The resolved Traversable; not guaranteed to exist on disk.
    """
    target = package_root
    for part in relpath.split("/"):
        target = target / part
    return target


# Stable name of the worker-skill dependency. ``maestro doctor`` special-cases it
# so the presence check follows the configured ``worker_prompt`` (which may name
# a non-default skill or a non-Claude agent layout) rather than this canonical
# default path.
WORKER_SKILL = "worker skill"

# Stable name of the CI-workflow dependency. The manifest entry below models the
# *directory* presence (does the repo carry any GitHub Actions workflow); the
# helpers further down model the *specific* opt-in skeleton ``maestro setup``
# materializes when ``manage_ci`` is on. ``maestro doctor`` special-cases this
# name so it reports the managed-skeleton state instead of bare directory
# presence.
CI_WORKFLOW = "CI workflow"

# The optional CI skeleton Maestro can materialize. It is deliberately kept off
# the manifest entry's ``template`` (which stays ``None``) so ``maestro update``
# treats CI as a no-template, non-noisy dependency: an unconfigured repo that has
# opted out of CI should never be nagged about a "missing" workflow. The filename
# is namespaced so writing it never clobbers a workflow the user already keeps.
CI_WORKFLOW_TEMPLATE = "templates/ci-workflow.yml"
CI_WORKFLOW_FILENAME = "maestro-ci.yml"

# Package-relative POSIX path to the bundled example config template.
CONFIG_TEMPLATE = "templates/maestro.example.yml"

# Package-relative POSIX path to the bundled worker-skill template.
WORKER_SKILL_TEMPLATE = "templates/worker-skill.md"


@dataclass(frozen=True)
class Dependency:
    """One user-editable artifact Maestro expects in a driven repo.

    Attributes:
        name: Short, human-readable label that also identifies the entry.
        description: One-line explanation of what the dependency is and why its
            location is fixed.
        location: Repo-relative POSIX path where the artifact belongs; a file
            path unless ``is_directory`` is set.
        is_directory: Whether ``location`` names a directory (presence means the
            directory exists and holds at least one entry) rather than a file.
        required: Whether Maestro cannot drive the repo without it. Drives the
            severity ``maestro doctor`` assigns when the artifact is missing.
        template: Package-relative POSIX path to the source template the artifact
            derives from, or ``None`` when no template ships for it.
    """

    name: str
    description: str
    location: str
    is_directory: bool
    required: bool
    template: str | None

    def destination(self, repo_path: Path) -> Path:
        """Return the absolute path the artifact belongs at under ``repo_path``.

        Args:
            repo_path: Root of the driven repository.

        Returns:
            ``repo_path`` joined with this dependency's repo-relative location.
        """
        return repo_path / self.location

    def is_present(self, repo_path: Path) -> bool:
        """Report whether the artifact exists at its expected location.

        Args:
            repo_path: Root of the driven repository.

        Returns:
            For a file dependency, whether the file exists; for a directory
            dependency, whether the directory exists and is non-empty.
        """
        target = self.destination(repo_path)
        if self.is_directory:
            return target.is_dir() and any(target.iterdir())
        return target.is_file()

    def template_path(self, package_root: Traversable = PACKAGE_FILES) -> Traversable | None:
        """Return the bundled-template resource for this dependency.

        Args:
            package_root: Root the template path is resolved against; defaults to
                :data:`PACKAGE_FILES`. Injectable for tests.

        Returns:
            The resolved template resource, or ``None`` when no template ships
            for this dependency.
        """
        if self.template is None:
            return None
        return _resolve(package_root, self.template)


MANIFEST: tuple[Dependency, ...] = (
    Dependency(
        name=WORKER_SKILL,
        description=(
            "The cc-work worker skill — the worker contract Maestro dispatches. "
            "Claude Code only discovers it at this path."
        ),
        location=".claude/skills/cc-work/SKILL.md",
        is_directory=False,
        required=True,
        template=WORKER_SKILL_TEMPLATE,
    ),
    Dependency(
        name="config",
        description=(
            "maestro.yml board configuration, read from the repo root when "
            "Maestro is run from inside the repo it drives."
        ),
        location="maestro.yml",
        is_directory=False,
        required=True,
        template=CONFIG_TEMPLATE,
    ),
    Dependency(
        name=CI_WORKFLOW,
        description=(
            "Optional GitHub Actions workflow that runs the quality gate on "
            "worker PRs. GitHub only runs workflows from this directory."
        ),
        location=".github/workflows",
        is_directory=True,
        required=False,
        template=None,
    ),
)


def ci_template_path(package_root: Traversable = PACKAGE_FILES) -> Traversable:
    """Return the bundled CI skeleton template resource.

    Args:
        package_root: Root the template path is resolved against; defaults to
            :data:`PACKAGE_FILES`. Injectable for tests.

    Returns:
        The resolved template resource.
    """
    return _resolve(package_root, CI_WORKFLOW_TEMPLATE)


def ci_workflow_destination(repo_path: Path) -> Path:
    """Return where ``maestro setup`` materializes the CI skeleton in a repo.

    Args:
        repo_path: Root of the driven repository.

    Returns:
        ``<repo_path>/.github/workflows/<CI_WORKFLOW_FILENAME>`` — the namespaced
        file that ``maestro setup`` writes when ``manage_ci`` is enabled.
    """
    return repo_path / ".github" / "workflows" / CI_WORKFLOW_FILENAME


def read_ci_template(package_root: Traversable = PACKAGE_FILES) -> str:
    """Read the packaged CI skeleton template's text.

    Args:
        package_root: Root the template path is resolved against; defaults to
            :data:`PACKAGE_FILES`. Injectable for tests.

    Returns:
        The template's text content.

    Raises:
        OSError: If the template path does not exist or cannot be read.
    """
    return ci_template_path(package_root).read_text(encoding="utf-8")


def read_template(dependency: Dependency, package_root: Traversable = PACKAGE_FILES) -> str:
    """Read a dependency's source template from the installed package.

    This is the reconciliation primitive ``maestro update`` builds on: given a
    manifest entry, return the canonical content Maestro ships for it.

    Args:
        dependency: The manifest entry whose template to read.
        package_root: Root the template path is resolved against; defaults to
            :data:`PACKAGE_FILES`. Injectable for tests.

    Returns:
        The template's text content.

    Raises:
        ValueError: If the dependency ships no template.
        OSError: If the template path does not exist or cannot be read.
    """
    path = dependency.template_path(package_root)
    if path is None:
        raise ValueError(f"{dependency.name} has no source template")
    return path.read_text(encoding="utf-8")
