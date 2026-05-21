<div align="center">

# Maestro

*Dispatches coding agents to work GitHub issues autonomously*

[![CI](https://github.com/realShaneYost/maestro/actions/workflows/ci.yml/badge.svg)](https://github.com/realShaneYost/maestro/actions/workflows/ci.yml)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE) [![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/) ![Status](https://img.shields.io/badge/status-pre--alpha-orange.svg)

</div>

Watches a GitHub Projects board and dispatches coding agents to work issues autonomously. Move a card into the trigger column; Maestro spawns a worker in an isolated git worktree, the worker opens a PR, you review and merge.

## Requirements

- Python 3.11+
- [`gh`](https://cli.github.com/), authenticated (`gh auth login`)
- `git`
- A coding-agent CLI on your `PATH` (e.g. `claude`)

## Install

```
git clone <this-repo> && cd maestro
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

## Configure

```
cp maestro.example.yml maestro.yml
```

Edit `maestro.yml` — at minimum `repo_path`, `repo_slug`, `project_number`, and `trigger_column`. See [`docs/github-setup.md`](docs/github-setup.md) for the GitHub board settings Maestro expects.

## Use

```
maestro run                 # start the poll/dispatch loop
maestro status              # snapshot of in-flight workers
maestro status --watch      # live-refreshing view
maestro once --dry-run      # one poll; show decisions, dispatch nothing
maestro clean               # prune finished worktrees and stale branches
```

Run `maestro run` in one terminal; check on it with `maestro status` from another. If something goes wrong, see [`docs/recovery.md`](docs/recovery.md).
