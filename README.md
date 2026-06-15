<div align="center">

# Maestro

*Dispatches coding agents to work GitHub issues autonomously*

[![CI](https://github.com/realShaneYost/maestro/actions/workflows/ci.yml/badge.svg)](https://github.com/realShaneYost/maestro/actions/workflows/ci.yml)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE) [![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/) ![Status](https://img.shields.io/badge/status-pre--alpha-orange.svg)

</div>

Watches a GitHub Projects board and dispatches coding agents to work issues autonomously. Move a card into the trigger column; Maestro spawns a worker in an isolated git worktree, the worker opens a PR, you review and merge.

## Requirements

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (or [`pipx`](https://pipx.pypa.io/)) to install Maestro
- [`gh`](https://cli.github.com/), authenticated (`gh auth login`)
- [`gh`](https://cli.github.com/), authenticated with the `project` scope (see Prerequisites below)
- `git`
- A coding-agent CLI on your `PATH` (e.g. `claude`)

## Prerequisites

Maestro drives GitHub entirely through the `gh` CLI and stores no credentials of its own, so `gh` must be authenticated with the right scopes before you run anything. There is one blessed path — run it once:

```
gh auth login --hostname github.com --git-protocol https --web --scopes project
```

That grants the three scopes Maestro relies on:

- `repo` — read issues and open pull requests (included by default).
- `project` — read and write the Projects V2 board, so Maestro can move cards between columns. This is the scope new users most often miss.
- `workflow` — included by default; lets a worker touch `.github/workflows` when an issue calls for it.

Already logged in but missing the `project` scope? Add it without redoing the whole flow:

```
gh auth refresh -s project --hostname github.com
```

`maestro doctor` verifies both your auth status and the `project` scope, and prints the exact command above when either is missing. Maestro never authenticates for you — that stays a deliberate user action.

### Headless worker permissions

Maestro dispatches each worker as a headless `claude -p` process started with `--permission-mode bypassPermissions`. A headless worker has no human on hand to approve interactive permission prompts, so any stricter mode would stall it on its very first `gh` or `git` call. This is safe by construction: every worker runs in an isolated git worktree, never merges, and a human reviews each PR before it lands. You do not configure anything for this — it is the dispatch default — but if you point Maestro at a different worker CLI, that CLI must likewise run fully non-interactively. The dispatch model is described in [`docs/how-it-works.md`](docs/how-it-works.md).

## Install

Maestro is a standalone tool — install it once, then run it from inside any repo you want it to drive:

```
uv tool install git+https://github.com/realShaneYost/maestro
```

That puts `maestro` on your `PATH` in its own isolated environment, so it never pollutes a project's dependencies. (Prefer pipx? `pipx install git+https://github.com/realShaneYost/maestro` works the same way. For local development from a clone, `uv tool install .` inside it.)

Confirm the install — `maestro doctor` reports the version and whether you are running the installed tool or a source checkout:

```
maestro doctor
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

## Ordering dependent issues

Maestro dispatches every ready card in the trigger column in parallel. When one issue must land before another — issue #38 builds on a module issue #37 creates — you **must** declare the ordering as GitHub's native **"blocked by"** relationship. Maestro reads only that relationship; a dependency written as body text, a task-list reference, or a `Blocked by #37` line in the issue body is invisible to it, and both cards will be dispatched at once. Two workers then race on the same files and collide in a merge conflict.

To set the relationship, on the issue that must wait (the *dependent*, #38 in the example):

1. Open the issue on GitHub.
2. In the right-hand sidebar, find **Relationships** (older layouts label it **Development** → **Add relationship**).
3. Choose **Blocked by**, search for the blocking issue (#37), and add it.

The dependent now shows a "Blocked by #37" badge. While #37 is open, Maestro holds #38 back even when its card sits in the trigger column, and `maestro status` flags it inline — `#38  build the CLI  — skipped — blocked by open issue(s): #37`. The moment #37 closes, #38 becomes dispatchable on the next poll. Run `maestro once --dry-run` to preview the same decision before starting the loop.

Only the native relationship gates dispatch — sub-issues (`trackedIssues`) and free-text "blocked by" mentions do not.
