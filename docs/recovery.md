# Recovery

Maestro keeps no database — there is no global state to corrupt. The worst case after a crash or a weird run is leftover worktrees and branches on disk, plus project cards sitting in odd columns. Both are easy to fix.

## If a worker crashes or the loop wedges

1. Stop the loop (`Ctrl-C` the `maestro run` process).
2. Run `maestro clean` — removes orphaned worktrees and deletes local branches whose PR has merged (their upstream is gone).
3. For each issue that was mid-flight, move its card back into the trigger column. A fresh worker spawns; the worker skill reads git and PR state and resumes or redoes the work correctly. If the state is genuinely ambiguous, the worker posts a question and moves the card to Blocked rather than guessing.
4. If a branch is truly wedged, delete it manually, then move the card back to the trigger column — the next worker starts fresh.

### Outcome → worktree disposition

Maestro classifies a finished run by the column its card ends up in:

| Card column on reap | Outcome  | Worktree |
|---|---|---|
| `review_column`     | `done`    | removed |
| `blocked_column`    | `blocked` | removed (the worker pushes its WIP to the issue branch before exiting, so nothing of value lives in the worktree) |
| anywhere else       | `failed`  | kept for debugging |

The exit code is consulted only as a fallback when the issue has closed and is no longer on the board. A worker that exits cleanly without parking its card is still `failed` — see the worker-skill contract.

### Stale worktrees

A failed run leaves its worktree on disk by design. When the operator re-triggers the issue (moves the card back to the trigger column), the next dispatch **automatically discards the stale worktree** before creating the fresh one and logs a WARNING. You no longer have to run `maestro clean` between a failed run and a redispatch.

## Starting fully over

You never *have* to: re-dropping a card into the trigger column is enough, because the worker skill detects where it left off. A full reset is always available — move the cards back to Backlog — but it is rarely needed.

## Cleaning up artifacts

- `maestro clean` — prune finished worktrees and stale branches (safe default).
- `maestro clean --logs` — also clear `logs/`.
- `maestro clean --history` — also truncate `runs.jsonl`.
- `maestro clean --all` — everything.

`runs.jsonl` and `logs/` are your audit trail; the default `clean` leaves them untouched so a routine cleanup never discards history by surprise.
