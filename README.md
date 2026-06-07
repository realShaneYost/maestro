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

Maestro is a standalone tool — install it once, then run it from inside any repo you want it to drive. Use [`pipx`](https://pipx.pypa.io/) (or [`uv tool`](https://docs.astral.sh/uv/)) so it lives in its own isolated environment and never pollutes a project's dependencies:

```
pipx install git+https://github.com/realShaneYost/maestro
# or, for local development from a clone:
git clone <this-repo> && cd maestro && uv tool install .
```

## Configure

Each driven repo carries its own config at its root. Copy the template in:

```
cd /path/to/your-project
cp /path/to/maestro/maestro.example.yml maestro.yml
```

Edit `maestro.yml` — at minimum `repo_slug`, `project_number`, and `trigger_column`. There is no `repo_path`: Maestro infers it from the git root when run from inside the repo, so the file holds only board facts and is meant to be committed. See [`docs/usage.md`](docs/usage.md) for the board settings Maestro expects and the full setup checklist.

## Use

Run every command **from inside the driven repo** — Maestro reads `./maestro.yml` and infers the repo from the git root:

```
cd /path/to/your-project
maestro setup               # bootstrap the board's Priority field (idempotent)
maestro doctor              # read-only preflight checks
maestro run                 # start the poll/dispatch loop
maestro status              # snapshot of the board and recent activity
maestro status --watch      # live-refreshing worker-log view
maestro once --dry-run      # one poll; explain each dispatch decision
maestro clean               # prune finished worktrees and stale branches
```

Worktrees, logs, and the run log live in a sibling directory, `../your-project.maestro/`, so they never touch the repo's working tree. Run `maestro run` in one terminal; check on it with `maestro status` from another. First-time setup and recovery are both in [`docs/usage.md`](docs/usage.md); the design is in [`docs/how-it-works.md`](docs/how-it-works.md).
