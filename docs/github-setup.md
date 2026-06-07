# GitHub setup

Maestro depends on a specific GitHub configuration. If you rename a column or change a field, update both `maestro.yml` and this document — a drifted setting will break dispatch.

## Authentication

Maestro stores no credentials. It shells out to `gh` and `git`, both of which must already be authenticated on the machine:

- `gh auth login` — one-time; stores a token in the OS keychain.
- `git` push uses your existing SSH key or credential helper.

No keys live in the repository, in `maestro.yml`, or in logs.

## Projects V2 board

- A Projects V2 board whose number you set as `project_number` in `maestro.yml`.
- A **Status** single-select field. Three of its options are referenced by Maestro: the **trigger** column (`trigger_column`) is the dispatch signal, the **review** column (`review_column`, default `In Review`) is where the worker parks the card after opening its PR, and the **blocked** column (`blocked_column`, default `Blocked`) is where the worker parks the card when it cannot proceed. Maestro classifies a finished run by the column the card ends up in, so the names in `maestro.yml`, in the worker skill, and on the board **must match exactly**.
- A **Priority** single-select field. The options can be named anything; **top-to-bottom order is the dispatch rank** (index 0 dispatches first). `Critical, High, Normal, Low` is the bootstrap default. `P0 / P1 / P2` keeps working unchanged because `P0` already sits at index 0.
- Suggested columns: `Backlog`, `In Progress` (trigger), `In Review`, `Blocked`, `Done`.
- The full operator setup checklist is in [`runbook.md`](runbook.md).

## Issue dependencies

Maestro reads GitHub's native **"Blocked by"** issue relationships. An issue is held back from dispatch while any issue it is blocked by is still open. Set these in the issue sidebar when you create dependent issues.

## Branch protection

- `main` should require a pull request before merging.
- Required reviewers may be `0` for a solo workflow — Maestro never merges, so the human still reviews and merges by hand.
- Enabling "automatically delete head branches" keeps stale branches from accumulating; `maestro clean` also handles them.

## Worker skill

The repository Maestro drives must provide a worker skill matching the configured `worker_prompt` (default `/cc-work {issue}`). It must take an issue number, work within a prepared git worktree, and open a PR. See `templates/worker-skill.md`.

## Worktree environment

Each worker runs in a throwaway git worktree. For a Python project whose workers change package code, set `worktree_setup` in `maestro.yml` to a command that builds an *isolated* virtualenv inside the worktree (for example, with `uv`). Without it, Maestro falls back to symlinking the target repo's `.venv` — one venv shared across all workers, pinned by its editable install to the primary checkout, so a worker's tests would run against the wrong code. See `maestro.example.yml` for the key.
