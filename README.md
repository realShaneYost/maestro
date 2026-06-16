<div align="center">

# Maestro

*Dispatches coding agents to work GitHub issues autonomously*

[![CI](https://github.com/realShaneYost/maestro/actions/workflows/ci.yml/badge.svg)](https://github.com/realShaneYost/maestro/actions/workflows/ci.yml)

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE) [![Python](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/) ![Status](https://img.shields.io/badge/status-pre--alpha-orange.svg)

</div>

Maestro watches a GitHub Projects board and dispatches coding agents to work issues autonomously. Move a card into the trigger column; Maestro spawns a worker in an isolated git worktree, the worker opens a pull request, and you review and merge. It is a plain program, not a long-running AI session — install it once, then run it from inside any repo you want it to drive. The design and invariants are in [`docs/how-it-works.md`](docs/how-it-works.md).

## Prerequisites

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (or [`pipx`](https://pipx.pypa.io/)) to install Maestro
- [`gh`](https://cli.github.com/) — you authenticate it in step 2 below
- `git`
- A coding-agent CLI on your `PATH` (e.g. `claude`)

## Install

Two paths to the same result. **Manual install** is the canonical step-by-step. **Automatic install** hands those same steps to Claude so it does the legwork in your repo — it runs the Manual steps, it does not duplicate them, so there is one source of truth.

### Automatic install

Open Claude Code in the repo you want Maestro to drive and paste this prompt:

> You are setting up Maestro to drive **this** repository. Follow the **Manual install** section of Maestro's README (<https://github.com/realShaneYost/maestro#manual-install>), executing each step for this repo. Rules:
>
> - Run the shell commands yourself, but **stop and confirm with me before anything that creates or mutates GitHub resources** — committing `maestro.yml`, running `maestro setup`, or touching the board.
> - Some steps can only be done in a browser (creating the Projects V2 board, adding Status options, setting branch protection). Don't attempt those — collect them into a short checklist and hand it back to me with the exact values to enter.
> - When you finish, run `maestro doctor` and report what passed and what I still need to do by hand.

Because the prompt points at the Manual section rather than repeating its steps, the two never drift.

### Manual install

1. **Install the tool** (once per machine). Maestro lives in its own isolated environment, so it never pollutes a project's dependencies:

   ```
   uv tool install git+https://github.com/realShaneYost/maestro
   ```

   Prefer pipx? `pipx install git+https://github.com/realShaneYost/maestro` works the same way. For local development from a clone, `uv tool install .` inside it. Confirm with `maestro doctor`, which reports the version and whether you are running the installed tool or a source checkout.

2. **Authenticate `gh` with the `project` scope.** Maestro shells out to `gh` and stores no credentials of its own:

   ```
   gh auth login --hostname github.com --git-protocol https --web --scopes project
   ```

   The `project` scope is the one new users most often miss — it lets Maestro read the board and move cards. Already logged in? Add it without redoing the flow: `gh auth refresh -s project --hostname github.com`.

3. **Create the Projects V2 board** (browser). It needs a **Status** single-select field with your trigger, review, and blocked columns, plus a **Priority** field. A suggested layout and the exact field requirements are in [`docs/usage.md`](docs/usage.md#board-layout).

4. **Write `maestro.yml`** at the root of the driven repo. It holds only board facts — no machine-specific paths — and is meant to be committed:

   ```
   cd /path/to/your-project
   cp /path/to/maestro/maestro/templates/maestro.example.yml maestro.yml
   ```

   Fill in at least `repo_slug`, `project_number`, and `trigger_column`. The full key reference is in [`docs/usage.md`](docs/usage.md#configuration-reference).

5. **Bootstrap the board:**

   ```
   maestro setup
   ```

   This creates the Priority field if absent and checks the Status field for your configured columns, printing any you must add by hand. It is idempotent and never mutates the Status field.

6. **Install the worker skill** in the driven repo. Copy [`maestro/templates/worker-skill.md`](maestro/templates/worker-skill.md) to `.claude/skills/cc-work/SKILL.md` (the path matches `worker_prompt`) and adapt its quality-gate commands and column names to your project. See [`docs/usage.md`](docs/usage.md#install-the-worker-skill).

7. **Verify, then run:**

   ```
   maestro doctor          # read-only preflight checks
   maestro once --dry-run  # one poll cycle that dispatches nothing
   maestro run             # start the poll/dispatch loop
   ```

   Run `maestro run` in one terminal and check on it with `maestro status` from another. The full command set and recovery steps are in [`docs/usage.md`](docs/usage.md).

## Writing issues Maestro can work

Maestro needs no GitHub issue templates. A workable issue is just two Markdown sections in its body — the worker reads the first as its checklist and the second as its verification target:

```markdown
## Subtasks

- [ ] First thing to do
- [ ] Second thing to do

## Acceptance criteria

- What must be true when this is done.
- An explicit verification command, if one applies.
```

The worker ticks each `- [ ] ` subtask as it lands, so the issue body stays a live progress board. Order matters: subtasks are worked top to bottom. Declare any cross-issue ordering as GitHub's native **"Blocked by"** relationship — body text is invisible to the scheduler. See [`docs/usage.md`](docs/usage.md) for the board and dependency details.

## Learn more

- [`docs/how-it-works.md`](docs/how-it-works.md) — the design, the dispatch loop, and the invariants.
- [`docs/usage.md`](docs/usage.md) — the configuration reference, full command set, board layout, and recovery.
