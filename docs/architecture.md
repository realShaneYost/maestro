# Maestro architecture

## What Maestro is

A standalone Python program that watches a GitHub Projects V2 board and dispatches autonomous coding agents to work issues. It is an orchestrator, not an AI session — it survives terminal restarts, holds no conversation context, and can be reasoned about like any other daemon-shaped tool.

```
maestro (the app)  ──poll──▶  GitHub Projects board
       │
       ├─ schedule: pick the next dispatchable issue(s)
       │
       └─ dispatch ──▶ git worktree add ──▶ worker (claude -p) ──▶ opens PR ──▶ human reviews + merges
```

## The loop (`maestro run`)

1. **Poll** the Projects V2 board via `gh api graphql` — one query per interval. Workers never poll; only the app does.
2. **Schedule** (pure code — see `core/scheduler.py`): an issue is dispatchable when its card is in the trigger column, the issue is open, it has no live worker, and every issue it is "Blocked by" is closed. When more issues are ready than `max_concurrent`, order by the Projects V2 Priority field.
3. **Dispatch** up to `max_concurrent`: `git worktree add` a fresh worktree off `origin/main`, provision its environment (run the configured `worktree_setup` command, or symlink the repo's `.venv` as a fallback), spawn the worker prompt (`/cc-work {issue}` by default) as a headless `claude` process, and capture its stdout to `logs/issue-N.log`.
4. **Track** in-flight workers; the set is reconstructable from `git worktree list`.
5. On worker exit: append one line to `runs.jsonl`, then remove the worktree if the worker succeeded (keep it if it crashed, for debugging).

## Components

- `config.py` — loads and validates `maestro.yml`.
- `core/models.py` — the shared vocabulary: `Issue`, `WorkerState`, `RunRecord`.
- `core/scheduler.py` — the pure dispatch-decision function.
- `cli.py` — argument parsing and subcommand dispatch.
- *(later slices)* a `TriggerSource` interface — `StatusFieldTrigger` (GraphQL) ships as the default; `LabelTrigger` (REST) is a drop-in alternative — plus worktree management, the run log, the `status` views, and `clean`.

## Re-entrancy (rework / blocked)

Workers are stateless; all durable state lives in GitHub. A worker opens its PR, moves its card to "In Review", and exits — the app will not re-dispatch it because it has left the trigger column. To rework, the human leaves normal review comments and moves the card back into the trigger column; a fresh worker spawns and the worker skill detects from PR state that this is a rework. A blocked worker posts a question, moves its card to "Blocked", and exits; answering and moving the card back re-dispatches it.

## Invariants

1. Maestro's authority ends at PR-open. It never merges. `maestro setup` is the one deliberate exception: a bootstrap-only command that may create a Priority field on the board. The `run` loop itself never writes to GitHub, and nothing — anywhere, ever — merges a PR.
2. `core/` never does screen I/O — the CLI and any future TUI are thin renderers.
3. The scheduler is pure, deterministic code — no language model.
4. Workers are stateless and isolated from one another.
5. Logs and `runs.jsonl` live outside worktrees and persist as the audit trail.
6. `status --watch` is read-only.

## Known limitations

- The board query and open-issues query both use GraphQL `first: 100` without cursor pagination. Boards larger than 100 items, or repositories with more than 100 open issues, are silently truncated.
- The Priority field's option order is scheduling semantics: index 0 dispatches first. Reordering the options on the Projects V2 settings screen silently changes the dispatch rank.

## Out of scope for v1

A GUI or interactive TUI (only `status --watch`); a background daemon / launchd wrapper; webhooks; any local model; the `LabelTrigger` implementation; anything that merges PRs; feedback analysis beyond the raw run log.
