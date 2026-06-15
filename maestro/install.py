"""Introspect how the running Maestro was installed.

Distinguishes an isolated tool install (``uv tool install`` / ``pipx``) from a
source checkout (an editable install or ``python -m maestro`` inside a clone)
and reports the package version. Both ``maestro doctor`` and ``maestro setup``
surface this so an operator is never confused about which Maestro they ran.

The detection is pure: it reads the package's own filesystem location and
classifies it, shelling out to nothing. The location is injectable so the
classifier can be exercised without staging real installs.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass
from pathlib import Path

from maestro import __version__


class InstallSource(enum.Enum):
    """Where the running ``maestro`` package was imported from."""

    UV_TOOL = "uv tool"
    PIPX = "pipx"
    SOURCE = "source checkout"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class InstallInfo:
    """How the running Maestro was installed.

    Attributes:
        version: The package version string (``maestro.__version__``).
        source: Classified install source.
        location: Filesystem path the ``maestro`` package was imported from.
    """

    version: str
    source: InstallSource
    location: Path

    @property
    def is_isolated_tool(self) -> bool:
        """Whether Maestro runs from an isolated tool install (uv tool / pipx)."""
        return self.source in (InstallSource.UV_TOOL, InstallSource.PIPX)

    @property
    def summary(self) -> str:
        """A one-line description of the version and install source."""
        if self.source is InstallSource.SOURCE:
            return (
                f"maestro {self.version} running from a source checkout at "
                f"{self.location} (not an isolated tool install)"
            )
        if self.source is InstallSource.UNKNOWN:
            return (
                f"maestro {self.version} running from {self.location} "
                "(install source unrecognized)"
            )
        return f"maestro {self.version} installed via {self.source.value} at {self.location}"


def _has_adjacent(parts: tuple[str, ...], first: str, second: str) -> bool:
    """Return whether ``first`` is immediately followed by ``second`` in ``parts``."""
    return any(parts[i] == first and parts[i + 1] == second for i in range(len(parts) - 1))


def classify_source(package_dir: Path) -> InstallSource:
    """Classify how the package at ``package_dir`` was installed.

    Args:
        package_dir: Directory the ``maestro`` package was imported from.

    Returns:
        The matched :class:`InstallSource`. A path under ``uv/tools`` is a uv
        tool, one under ``pipx`` is a pipx install, one whose parent is a repo
        checkout (``pyproject.toml`` beside ``.git``) is a source checkout, and
        anything else is unknown.
    """
    parts = package_dir.parts
    if _has_adjacent(parts, "uv", "tools"):
        return InstallSource.UV_TOOL
    if "pipx" in parts:
        return InstallSource.PIPX
    repo_root = package_dir.parent
    if (repo_root / "pyproject.toml").is_file() and (repo_root / ".git").exists():
        return InstallSource.SOURCE
    return InstallSource.UNKNOWN


def detect_install(package_dir: Path | None = None, version: str = __version__) -> InstallInfo:
    """Detect how the running Maestro was installed.

    Args:
        package_dir: Package directory to classify; defaults to this package's
            own location. Injectable for tests.
        version: Version string to report; defaults to ``maestro.__version__``.

    Returns:
        An :class:`InstallInfo` describing the version and install source.
    """
    location = package_dir if package_dir is not None else Path(__file__).resolve().parent
    return InstallInfo(version=version, source=classify_source(location), location=location)
