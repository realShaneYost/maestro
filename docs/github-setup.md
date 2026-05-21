# GitHub setup

Maestro depends on a specific GitHub configuration. If you rename a column or change a field, update both `maestro.yml` and this document — a drifted setting will break dispatch.

## Authentication

Maestro stores no credentials. It shells out to `gh` and `git`, both of which must already be authenticated on the machine:

- `gh auth login` — one-time; stores a token in the OS keychain.
- `git` push uses your existing SSH key or credential helper.

No keys live in the repository, in `maestro.yml`, or in logs.

## Projects V2 board

- A Projects V2 board whose number you set as `project_number` in `maestro.yml`.
- A **Status** single-select field. One of its options is the **trigger column** (`trigger_column` in config) — moving an issue's card there is the dispatch signal.
- A **Priority** single-select field (e.g. `P0` / `P1` / `P2`). The scheduler uses it to order issues when more are ready than `max_concurrent`.
- Suggested columns: `Backlog`, `In Progress` (trigger), `In Review`, `Blocked`, `Done`.

## Issue dependencies

Maestro reads GitHub's native **"Blocked by"** issue relationships. An issue is held back from dispatch while any issue it is blocked by is still open. Set these in the issue sidebar when you create dependent issues.

## Branch protection

- `main` should require a pull request before merging.
- Required reviewers may be `0` for a solo workflow — Maestro never merges, so the human still reviews and merges by hand.
- Enabling "automatically delete head branches" keeps stale branches from accumulating; `maestro clean` also handles them.

## Worker skill

The repository Maestro drives must provide a worker skill matching the configured `worker_prompt` (default `/cc-work {issue}`). It must take an issue number, work within a prepared git worktree, and open a PR. See `templates/worker-skill.md`.
