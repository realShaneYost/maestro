"""Load and validate Maestro configuration from a YAML file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

_REQUIRED_KEYS = ("repo_path", "repo_slug", "project_number", "trigger_column")

_DEFAULTS: dict[str, Any] = {
    "max_concurrent": 2,
    "poll_interval": 60,
    "worker_prompt": "/cc-work {issue}",
    "theme": "nord",
    "worktree_setup": None,
}


class ConfigError(ValueError):
    """Raised when a Maestro config file is missing or invalid."""


@dataclass(frozen=True)
class Config:
    """Validated Maestro runtime configuration.

    Attributes:
        repo_path: Absolute path to the target git repository.
        repo_slug: GitHub ``owner/name`` slug for that repository.
        project_number: GitHub Projects V2 board number to poll.
        trigger_column: Status-field value that means "dispatch this issue".
        max_concurrent: Hard ceiling on simultaneously running workers.
        poll_interval: Seconds between board polls.
        worker_prompt: Prompt template handed to each worker; must contain the
            ``{issue}`` placeholder.
        theme: Name of the colour theme used by ``status``.
        worktree_setup: Optional shell command run inside each fresh worktree
            to provision its environment; ``None`` falls back to symlinking
            the target repo's ``.venv``.
    """

    repo_path: Path
    repo_slug: str
    project_number: int
    trigger_column: str
    max_concurrent: int
    poll_interval: int
    worker_prompt: str
    theme: str
    worktree_setup: str | None


def load_config(path: Path) -> Config:
    """Load and validate Maestro config from ``path``.

    Args:
        path: Path to a ``maestro.yml`` file.

    Returns:
        A fully validated :class:`Config`.

    Raises:
        ConfigError: If the file is missing, is not valid YAML, is not a
            mapping, omits a required key, or holds an invalid value.
    """
    if not path.is_file():
        raise ConfigError(f"Config file not found: {path}")

    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as err:
        raise ConfigError(f"Config file is not valid YAML: {path}") from err

    if not isinstance(raw, dict):
        raise ConfigError(f"Config file must be a YAML mapping: {path}")

    missing = [key for key in _REQUIRED_KEYS if key not in raw]
    if missing:
        raise ConfigError(f"Config is missing required key(s): {', '.join(missing)}")

    return _build_config({**_DEFAULTS, **raw})


def _build_config(values: dict[str, Any]) -> Config:
    """Coerce and validate a merged config mapping into a :class:`Config`."""
    repo_path = Path(str(values["repo_path"])).expanduser()
    if not repo_path.is_dir():
        raise ConfigError(f"repo_path is not a directory: {repo_path}")

    repo_slug = str(values["repo_slug"])
    if not _SLUG_RE.match(repo_slug):
        raise ConfigError(f"repo_slug must look like 'owner/name': {repo_slug!r}")

    trigger_column = str(values["trigger_column"])
    if not trigger_column.strip():
        raise ConfigError("trigger_column must not be empty")

    worker_prompt = str(values["worker_prompt"])
    if "{issue}" not in worker_prompt:
        raise ConfigError("worker_prompt must contain the '{issue}' placeholder")

    raw_setup = values["worktree_setup"]
    worktree_setup = None if raw_setup is None else str(raw_setup)
    if worktree_setup is not None and not worktree_setup.strip():
        raise ConfigError("worktree_setup must not be empty")

    return Config(
        repo_path=repo_path,
        repo_slug=repo_slug,
        project_number=_positive_int(values, "project_number"),
        trigger_column=trigger_column,
        max_concurrent=_positive_int(values, "max_concurrent"),
        poll_interval=_positive_int(values, "poll_interval"),
        worker_prompt=worker_prompt,
        theme=str(values["theme"]),
        worktree_setup=worktree_setup,
    )


def _positive_int(values: dict[str, Any], key: str) -> int:
    """Return ``values[key]`` as a positive int or raise :class:`ConfigError`."""
    raw = values[key]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ConfigError(f"{key} must be an integer: {raw!r}")
    if raw <= 0:
        raise ConfigError(f"{key} must be a positive integer: {raw}")
    return raw
