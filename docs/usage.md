# Using Maestro

The depth reference behind the [README](../README.md): the board layout Maestro expects, the full configuration key set, the command set, and how to recover when something goes sideways. **Install steps live in the README's [Manual install](../README.md#manual-install) section — they are not repeated here.** Maestro is installed once and run **from the root of each repo it drives**; it reads `./maestro.yml` and infers the repo from the git root, so every command below is run from inside the driven repo.

For the design behind all this, see [`how-it-works.md`](how-it-works.md).

Maestro stores no credentials. It shells out to `gh` and `git` and trusts whatever they are already authenticated as. No keys live in the repo, in `maestro.yml`, or in logs.

## Board layout

Maestro expects:

- A **Projects V2 board** whose number you set as `project_number`.
- A **Status** single-select field. Three of its options matter: the **trigger** column (the dispatch signal), the **review** column (where the worker parks the card after opening its PR), and the **blocked** column (where it parks the card when it can't proceed). Maestro classifies a run by the column the card ends in, so these names must match `maestro.yml`, the worker skill, and the board **exactly**.
- A **Priority** single-select field. Options can be named anything; **top-to-bottom order is the dispatch rank** (index 0 first). `maestro setup` creates this for you if it's missing.
- Suggested column layout: `Backlog`, `In Progress` (trigger), `In Review`, `Blocked`, `Done`.

**Issue dependencies:** Maestro reads GitHub's native **"Blocked by"** relationships. An issue is held back from dispatch while any issue it is blocked by is still open. Set these in the issue sidebar — body text and task-list references are invisible to the scheduler.

**Branch protection:** `main` should require a PR before merging. Required reviewers may be `0` for a solo workflow — Maestro never merges, so a human still reviews and merges by hand. Enabling "automatically delete head branches" keeps stale branches down (`maestro clean` also handles them).

**What `maestro setup` does:** it is narrowly scoped and idempotent. It ensures a **Priority** single-select field exists — creating one with options `Critical`, `High`, `Normal`, `Low` if absent, and leaving an existing one completely untouched — and checks the **Status** field has your configured trigger, review, and blocked columns. It **never** mutates the Status field; if a column is missing, it prints exactly which option names to add by hand (**⋯ → Settings → Fields → Status → Add option**). This is the one deliberate exception to "Maestro's authority ends at PR-open"; the `run` loop itself never writes to GitHub.

## Configuration reference

`maestro.yml` lives at the root of the driven repo. There is no `repo_path` — Maestro infers it from the git root, so the file holds only board facts and is meant to be **committed**. Copy the template in with `cp /path/to/maestro/maestro/templates/maestro.example.yml maestro.yml`, then fill it in:

| Key | Default | What it does |
|---|---|---|
| `repo_slug` | required | GitHub `owner/name` slug for this repository. |
| `project_number` | required | The Projects V2 board number. |
| `trigger_column` | required | Status value that means "dispatch this issue". |
| `review_column` | `"In Review"` | Status value the worker parks the card in after opening its PR. **Reaching it is how a run is classified `done`.** |
| `blocked_column` | `"Blocked"` | Status value the worker parks the card in when it can't proceed. **Reaching it is how a run is classified `blocked`.** |
| `max_concurrent` | `2` | Hard ceiling on simultaneously running workers. |
| `poll_interval` | `60` | Seconds between board polls. |
| `max_consecutive_failures` | `3` | How many times an issue may fail in a row before Maestro stops dispatching it. Any clean run resets the streak; the count is derived from `runs.jsonl`, so it survives a restart. See *Runaway-retry guard* below. |
| `worker_prompt` | `"/cc-work {issue}"` | Prompt template handed to each worker. Must contain `{issue}`. |
| `theme` | `"nord"` | Theme for `maestro status` — `nord`, `dark`, or `light`. |
| `worktree_setup` | `null` | Shell command run inside each fresh worktree to provision its environment. **Set this for any project whose workers change package code** — see *Worktree environment* below. |

`trigger_column`, `review_column`, and `blocked_column` must be three **distinct** values, or the config is rejected. Runtime artifacts (worktrees, logs, `runs.jsonl`) land in the sibling `../<repo>.maestro/` directory — nothing to gitignore.

## Install the worker skill

The driven repo must supply the skill named in `worker_prompt`. For Claude Code, that's `.claude/skills/<name>/SKILL.md`, where `<name>` is the first token after the `/` (default `cc-work`). [`maestro/templates/worker-skill.md`](../maestro/templates/worker-skill.md) is the contract — copy it across and adapt the quality-gate commands and column names. The skill's column names **must match** `maestro.yml` and the live board exactly.

## Command reference

Run all of these from inside the driven repo:

```
maestro setup               # bootstrap the board's Priority field (idempotent)
maestro doctor              # read-only preflight checks
maestro update              # reinstall the tool and report dependency drift
maestro add <issue>         # add an existing issue to the board in one step
maestro run                 # start the poll/dispatch loop
maestro status              # snapshot of the board and recent activity
maestro status --watch      # live-refreshing worker-log view
maestro once --dry-run      # one poll; explain each dispatch decision
maestro logs <issue>        # dump an issue's latest-run worker log
maestro clean               # prune finished worktrees and stale branches
```

Run `maestro run` in one terminal; check on it with `maestro status` from another. `maestro once --dry-run` lists each board issue and what the scheduler *would* do ("would dispatch", "skipped — already in flight", "skipped — blocked by open issue(s): #71", …) without dispatching anything; the live `run` behaves identically.

`maestro doctor` mutates nothing and exits non-zero on failure (warnings don't affect the exit code). It also reports the three editable dependencies Maestro keeps in each driven repo — the worker skill (`.claude/skills/cc-work/SKILL.md`), the config (`maestro.yml`), and the optional CI workflow (`.github/workflows/`) — and whether each is present. `maestro update` reinstalls the tool and reports drift against the shipped templates, including any new `maestro.yml` keys; it never overwrites your edits.

## Adding an issue to the board

A freshly created GitHub issue is not on the Projects board, and Maestro only sees cards already on it. `maestro add <issue>` puts an existing issue on the board in one step:

```
gh issue create --title "Fix the thing"   # creates the issue, off the board
maestro add 42                            # adds #42 to the board's leftmost column
maestro add 42 --status "Backlog"         # ...or target a named column
```

By default the card lands in the board's **leftmost Status column** (the conventional `Backlog`/`Todo` entry column), and the command reports which column it chose. It does not place the card in the trigger column — adding an issue should not dispatch it; you move it there when it is ready to work. Done by hand this is two `gh` calls, and the second (`gh project item-edit`) needs the Status option's node ID, which GitHub regenerates whenever a column is edited — a cached ID fails silently. `maestro add` reads the live option ID at write time, so it never hits a stale-ID failure. An unknown `--status` is rejected with the board's actual column names listed.

## Continuous integration (optional)

CI is an opt-in enhancement, not a requirement. Maestro is fully functional without it: workers open PRs, rework them off human review, and move their cards whether or not any checks run. The one thing CI buys you is an **automated rework signal** — when a worker's PR fails its checks, the worker's rework mode picks up the failures on the next dispatch and fixes them, on top of the human review it already responds to.

Because of that, CI ships **disabled by default**, gated behind the `manage_ci` key in `maestro.yml`. An unconfigured generic pipeline is worse than none: an enabled-but-empty workflow either lies green (false confidence) or reds every PR (a false rework loop), and both poison the very signal CI exists to provide. So enabling it is a deliberate act you take once a real test command is in place.

To enable it, set `manage_ci: true` and run `maestro setup`. Setup writes a minimal, language-agnostic skeleton to `.github/workflows/maestro-ci.yml` (you commit it) and never overwrites an existing file. The skeleton triggers on `workflow_dispatch` **only**, so it never auto-fires until you replace its placeholder step with your project's real build/test command and add the `pull_request:` / `push:` triggers you want — the file's header comment walks through both. With `manage_ci` left at its default `false`, `maestro setup` writes nothing and the repo is untouched; `maestro doctor` notes the present-but-disabled state with a one-line enable hint.

## Priority

The Priority field's options can be named anything — `Critical/High/Normal/Low`, `P0/P1/P2`, `Now/Next/Later`, all work the same. **The rank is the option's POSITION in the field's option list. Index 0 dispatches first.** The names carry no meaning.

> The option order on the Priority field's settings screen is load-bearing. Reordering it silently changes the dispatch order — be deliberate.

To migrate an existing `P0/P1/P2` field, **rename the options in place** (pencil icon: `P0 → Critical`, etc.) rather than delete-and-recreate. Renaming preserves option IDs, so every card keeps its value; delete-and-recreate loses them all. The index-based rank is unchanged by the rename.

## Worktree environment

Each worker runs in a throwaway git worktree. For any project whose workers change package code, set `worktree_setup` in `maestro.yml` to a command that builds an *isolated* environment inside the worktree — for a Python project with `uv`:

```yaml
worktree_setup: "uv venv .venv && uv pip install -e '.[dev]'"
```

Without it, Maestro falls back to symlinking the target repo's `.venv` — one venv shared across all workers, pinned by its editable install to the primary checkout, so a worker's tests would run against the wrong code.

## Recovery

Maestro keeps no database — there is no global state to corrupt. The worst case after a crash is leftover worktrees and branches on disk, plus cards sitting in odd columns. Both are easy to fix.

**If a worker crashes or the loop wedges:**

1. Stop the loop (`Ctrl-C` the `maestro run` process).
2. Run `maestro clean` — removes orphaned worktrees and deletes local branches whose PR has merged.
3. For each mid-flight issue, move its card back into the trigger column. A fresh worker reads git and PR state and resumes or redoes the work. If the state is genuinely ambiguous, the worker posts a question and moves the card to Blocked rather than guessing.
4. If a branch is truly wedged, delete it manually, then move the card back to the trigger column.

**Stale worktrees:** a failed run leaves its worktree on disk by design. The next time you re-trigger that issue, dispatch **automatically discards the stale worktree** first and logs a WARNING — you don't have to `maestro clean` between a failed run and a redispatch.

**Starting over:** you never *have* to — re-dropping a card into the trigger column is enough, because the worker detects where it left off. A full reset (move cards back to Backlog) is always available but rarely needed.

## Runaway-retry guard

A worker that *crashes* — exits without parking its card in the review or blocked column — leaves its card in the trigger column, so the scheduler would re-pick it on the next poll. Left unattended, a single stuck issue could redispatch indefinitely, each retry a full worker session burning tokens and compute. `max_consecutive_failures` (default `3`) caps that: once an issue records that many `failed` runs in a row, the scheduler stops dispatching it.

The streak is derived from `runs.jsonl`, not from in-memory session state, so it **survives a `maestro run` restart** — restarting the loop does not reset a stuck issue's count. Any clean run resets the streak to zero: a `done` (card parked in the review column) or a `blocked` (card parked in the blocked column) both count as clean, because both mean the worker behaved correctly. Only true crashes accrue.

A capped issue is surfaced, not silently dropped:

- `maestro run` logs a WARNING the first time it holds the issue back (`Holding issue #N — skipped — hit failure cap (3 consecutive failed runs; max_consecutive_failures=3)`).
- `maestro status` flags it inline in the board view, and `maestro once --dry-run` lists it among the skipped issues with the same reason.

Maestro does **not** move the card — the `run` loop never writes to GitHub. To clear the cap, investigate the failures (`maestro logs <issue>`), fix the underlying cause, then move the card out of and back into the trigger column; the next clean run resets the streak. Set `max_consecutive_failures` higher to tolerate more retries, or to `1` to stop after the first crash.

## Inspecting a run's full log

`maestro status --watch` summarises live activity, but when a run goes wrong you usually want the complete trace. `maestro logs <issue>` dumps the full per-issue log — Claude's raw `stream-json` output from that issue's most recent dispatch — straight to stdout, so you can read it or pipe it somewhere:

```
maestro logs 23              # print issue #23's latest-run log
maestro logs 23 --out run.log  # write it to a file instead
maestro logs 23 | pbcopy     # copy it to paste back to Claude for debugging
```

Each dispatch truncates the log file before the worker starts, so `logs` always shows the latest run. If the issue was never dispatched (or its logs were cleared with `maestro clean --logs`), the command says so and exits non-zero.

## Cleaning up artifacts

```
maestro clean             # prune finished worktrees and stale branches (safe default)
maestro clean --logs      # also clear logs/
maestro clean --history   # also truncate runs.jsonl
maestro clean --all       # everything
```

`runs.jsonl` and `logs/` (both under the sibling `../<repo>.maestro/`) are your audit trail; the default `clean` leaves them untouched so a routine cleanup never discards history by surprise.
