# How Maestro works

## The idea

Maestro watches a GitHub Projects board. When you move an issue's card into the trigger column, Maestro spawns a coding agent to do the work in an isolated git worktree. The agent opens a pull request and exits; a human reviews and merges. That's the whole loop.

It is a plain program, not an AI session — it holds no conversation, survives terminal restarts, and can be reasoned about like any other daemon. The thinking happens twice: once when a human writes the issue, and once when the worker implements it. Maestro itself does no reasoning — the part that decides *what to dispatch* is pure, deterministic code.

## Where you run it

Maestro is installed once on your machine and run from **inside** each repo it drives. There is no central install pointing at remote repos.

Running from inside the repo means:

- the repo is the current git root, which Maestro uses as the repo path — so `maestro.yml` holds only board facts (no machine-specific paths) and is committed to the repo;
- runtime artifacts (worktrees, per-issue logs, `runs.jsonl`) are written to a **sibling** directory, `../<repo>.maestro/`, keeping them out of the repo's working tree.

One install can drive many repos — each is just a different directory you run it from, with its own committed config.

## The loop (`maestro run`)

```
maestro  ──poll──▶  GitHub Projects board
   │
   ├─ schedule: pick the next dispatchable issue(s)
   │
   └─ dispatch ──▶ git worktree add ──▶ worker (claude -p) ──▶ opens PR ──▶ human reviews + merges
```

1. **Poll** the board via `gh api graphql`, once per interval. Only Maestro polls; workers never do.
2. **Schedule** (pure code, `core/scheduler.py`): an issue is dispatchable when its card is in the trigger column, the issue is open, it has no live worker, every issue it is "Blocked by" is closed, and it has not hit the consecutive-failure cap (`max_consecutive_failures`, derived from `runs.jsonl` and passed in — the scheduler stays pure). When more issues are ready than `max_concurrent`, the board's Priority field breaks the tie.
3. **Dispatch** up to `max_concurrent`: add a fresh worktree off `origin/main`, provision it (run `worktree_setup`, or fall back to symlinking the repo's `.venv`), spawn the worker prompt as a headless `claude` process, and capture its output to `logs/issue-N.log`.
4. **Track** in-flight workers — the set is reconstructable from `git worktree list`.
5. **Reap** on worker exit: append one line to `runs.jsonl`, then remove the worktree if the worker succeeded (keep it if it crashed, for debugging).

## How a run's outcome is decided

Maestro classifies a finished run by **which column the card ends up in**, not by the worker's exit code:

| Card lands in   | Outcome   | Worktree |
|-----------------|-----------|----------|
| review column   | `done`    | removed  |
| blocked column  | `blocked` | removed  |
| anywhere else   | `failed`  | kept for debugging |

A worker that exits cleanly but leaves its card in the trigger column is still `failed` — the column is the source of truth. The exit code is consulted only as a fallback, when the issue has closed and is gone from the board.

## Rework and blocked, for free

Workers are stateless; all durable state lives in GitHub. A worker finishes by moving its card out of the trigger column, so Maestro won't re-dispatch it. To get more work out of an issue, you just move its card back into the trigger column:

- **Rework:** leave review comments, move the card back. A fresh worker spawns and detects from PR state that this is a rework.
- **Blocked:** the worker posts a question and parks the card in the blocked column. You answer and move the card back; a fresh worker reads the new comments and resumes.

There is no resume protocol to maintain — re-dispatch *is* the recovery mechanism.

## The pieces

- `config.py` — loads and validates `maestro.yml`; derives the sibling runtime paths.
- `core/models.py` — the shared vocabulary: `Issue`, `WorkerState`, `RunRecord`.
- `core/scheduler.py` — the pure dispatch-decision function.
- `github.py` — every `gh` call: board polling, field lookups, the `setup` mutations.
- `cli.py` — argument parsing and subcommand dispatch; the thin renderer over `core`.

## Where the GitHub coupling lives

Maestro is generic in spirit but currently speaks GitHub. If you want to drive a different ticketing system, the seam is the **trigger source** — the thing that answers "which issues are ready, and what are their blockers and priority?". Today that is `StatusFieldTrigger`, which polls a Projects V2 board over GraphQL. The scheduler, worktree management, run log, and CLI all work in terms of the plain `Issue` model in `core/models.py` and don't know GitHub exists. A second backend means: a new trigger source producing `Issue`s, plus whatever that system needs in `github.py`'s place. The worker contract (open a PR, move the card) and the merge step stay GitHub-shaped for now.

## Invariants

These are hard rules; breaking one is a regression.

1. **Maestro's authority ends at PR-open.** It never merges, rebases, or force-pushes. Its only git writes are `worktree add` / `remove` / `prune`. The one deliberate exception is `maestro setup`, a bootstrap command that may create a Priority field; the `run` loop never writes to GitHub.
2. **`core/` never does screen I/O.** Core returns data; the CLI is a thin renderer. This keeps a future TUI cheap.
3. **The scheduler is pure, deterministic code — no language model, ever.**
4. **Workers are stateless and isolated** from one another. Each runs one issue in its own worktree and exits.
5. **Logs live outside worktrees** — in the sibling `../<repo>.maestro/` directory — and persist as the audit trail.
6. **`status --watch` is read-only.** It renders state; it never controls the loop.

## Known limitations

- The board and open-issues queries use GraphQL `first: 100` with no pagination. Boards over 100 items, or repos with over 100 open issues, are silently truncated.
- The Priority field's **option order** is the dispatch rank (index 0 first). Reordering the options on the board's settings screen silently changes which issues go first.
