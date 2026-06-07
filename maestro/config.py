"""Load and validate Maestro configuration from a YAML file."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_SLUG_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")

# repo_path is not a config key: Maestro is run from inside the driven repo and
# infers it from the git root, which keeps maestro.yml free of machine-specific
# paths so it can be committed to the driven repo.
_REQUIRED_KEYS = ("repo_slug", "project_number", "trigger_column")

_DEFAULTS: dict[str, Any] = {
    "max_concurrent": 2,
    "poll_interval": 60,
    "worker_prompt": "/cc-work {issue}",
    "theme": "nord",
    "worktree_setup": None,
    "review_column": "In Review",
    "blocked_column": "Blocked",
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
        review_column: Status-field value a worker parks its card in when it
            opens its PR. Reaching this column is how Maestro classifies a
            run as ``done``.
        blocked_column: Status-field value a worker parks its card in when it
            cannot proceed. Reaching this column is how Maestro classifies a
            run as ``blocked``.
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
    review_column: str
    blocked_column: str


def load_config(path: Path, *, repo_path: Path) -> Config:
    """Load and validate Maestro config from ``path``.

    Args:
        path: Path to a ``maestro.yml`` file.
        repo_path: Absolute path to the driven git repository, supplied by the
            caller (the CLI infers it from the git root). Any ``repo_path`` key
            in the file is ignored in its favour.

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

    return _build_config({**_DEFAULTS, **raw, "repo_path": str(repo_path)})


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

    review_column = str(values["review_column"])
    if not review_column.strip():
        raise ConfigError("review_column must not be empty")

    blocked_column = str(values["blocked_column"])
    if not blocked_column.strip():
        raise ConfigError("blocked_column must not be empty")

    columns = {
        "trigger_column": trigger_column,
        "review_column": review_column,
        "blocked_column": blocked_column,
    }
    if len(set(columns.values())) != len(columns):
        raise ConfigError(
            "trigger_column, review_column, and blocked_column must be three distinct values"
        )

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
        review_column=review_column,
        blocked_column=blocked_column,
    )


def _positive_int(values: dict[str, Any], key: str) -> int:
    """Return ``values[key]`` as a positive int or raise :class:`ConfigError`."""
    raw = values[key]
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise ConfigError(f"{key} must be an integer: {raw!r}")
    if raw <= 0:
        raise ConfigError(f"{key} must be a positive integer: {raw}")
    return raw


@dataclass(frozen=True)
class RuntimePaths:
    """Filesystem locations for a driven repo's Maestro runtime artifacts.

    Everything lives in a sibling directory next to the driven repo
    (``<repo>.maestro/``), so worktrees, logs, and the run log never pollute
    the repo's working tree and a worktree is never nested inside it.

    Attributes:
        root: The sibling runtime directory itself.
        worktrees_dir: Directory under which per-issue worktrees are created.
        logs_dir: Directory for per-issue worker logs.
        runs_log: Path to the append-only run log.
    """

    root: Path
    worktrees_dir: Path
    logs_dir: Path
    runs_log: Path


def runtime_paths(repo_path: Path) -> RuntimePaths:
    """Derive the runtime-artifact paths for a driven repo.

    Args:
        repo_path: Absolute path to the driven git repository.

    Returns:
        Paths rooted at ``<repo>.maestro/`` — a sibling of the repo.
    """
    root = repo_path.parent / f"{repo_path.name}.maestro"
    return RuntimePaths(
        root=root,
        worktrees_dir=root / "worktrees",
        logs_dir=root / "logs",
        runs_log=root / "runs.jsonl",
    )
