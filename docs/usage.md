# Using Maestro

A linear setup checklist, then the command reference and how to recover when something goes sideways. Maestro is installed once and run **from the root of each repo it drives** — it reads `./maestro.yml` and infers the repo from the git root. Every command below is run from inside the driven repo.

For the design behind all this, see [`how-it-works.md`](how-it-works.md).

## Requirements

- Python 3.11+
- [`gh`](https://cli.github.com/), authenticated (`gh auth login`)
- `git`
- A coding-agent CLI on your `PATH` (e.g. `claude`)

Maestro stores no credentials. It shells out to `gh` and `git` and trusts whatever they are already authenticated as. No keys live in the repo, in `maestro.yml`, or in logs.

## 1. Install Maestro (once per machine)

```
pipx install git+https://github.com/realShaneYost/maestro
```

Use [`pipx`](https://pipx.pypa.io/) (or `uv tool install`) so Maestro lives in its own isolated environment and never becomes a dependency of the repos it drives. For local development from a clone: `git clone <repo> && cd maestro && uv tool install .`.

## 2. Authenticate `gh` with the `project` scope

```
gh auth status
gh auth refresh -s project
```

The `project` scope is required to read the board and, during `maestro setup`, to create the Priority field.

## 3. Set up the Projects V2 board

Maestro expects:

- A **Projects V2 board** whose number you set as `project_number`.
- A **Status** single-select field. Three of its options matter: the **trigger** column (the dispatch signal), the **review** column (where the worker parks the card after opening its PR), and the **blocked** column (where it parks the card when it can't proceed). Maestro classifies a run by the column the card ends in, so these names must match `maestro.yml`, the worker skill, and the board **exactly**.
- A **Priority** single-select field. Options can be named anything; **top-to-bottom order is the dispatch rank** (index 0 first). `maestro setup` creates this for you if it's missing — see step 5.
- Suggested column layout: `Backlog`, `In Progress` (trigger), `In Review`, `Blocked`, `Done`.

**Issue dependencies:** Maestro reads GitHub's native **"Blocked by"** relationships. An issue is held back from dispatch while any issue it is blocked by is still open. Set these in the issue sidebar.

**Branch protection:** `main` should require a PR before merging. Required reviewers may be `0` for a solo workflow — Maestro never merges, so a human still reviews and merges by hand. Enabling "automatically delete head branches" keeps stale branches down (`maestro clean` also handles them).

## 4. Write `maestro.yml` at the repo root

Copy the template into the driven repo and fill it in. It holds only board facts (no machine-specific paths) and is meant to be **committed**:

```
cp /path/to/maestro/maestro.example.yml maestro.yml
```

There is no `repo_path` — Maestro infers it from the git root. The keys:

| Key | Default | What it does |
|---|---|---|
| `repo_slug` | required | GitHub `owner/name` slug for this repository. |
| `project_number` | required | The Projects V2 board number. |
| `trigger_column` | required | Status value that means "dispatch this issue". |
| `review_column` | `"In Review"` | Status value the worker parks the card in after opening its PR. **Reaching it is how a run is classified `done`.** |
| `blocked_column` | `"Blocked"` | Status value the worker parks the card in when it can't proceed. **Reaching it is how a run is classified `blocked`.** |
| `max_concurrent` | `2` | Hard ceiling on simultaneously running workers. |
| `poll_interval` | `60` | Seconds between board polls. |
| `worker_prompt` | `"/cc-work {issue}"` | Prompt template handed to each worker. Must contain `{issue}`. |
| `theme` | `"nord"` | Theme for `maestro status` — `nord`, `dark`, or `light`. |
| `worktree_setup` | `null` | Shell command run inside each fresh worktree to provision its environment. **Set this for any project whose workers change package code** — see *Worktree environment* below. |

`trigger_column`, `review_column`, and `blocked_column` must be three **distinct** values, or the config is rejected. Runtime artifacts (worktrees, logs, `runs.jsonl`) land in the sibling `../<repo>.maestro/` directory — nothing to gitignore.

## 5. Bootstrap the board

```
maestro setup
```

`setup` is narrowly scoped and idempotent:

- Ensures a **Priority** single-select field exists. If absent, it creates one with options `Critical`, `High`, `Normal`, `Low`. If one already exists, **`setup` leaves it completely untouched.**
- Checks the **Status** field has your configured trigger, review, and blocked columns. It **never** mutates the Status field — if a column is missing, it prints exactly which option names to add by hand.

Running it twice changes nothing the second time. This is the one deliberate exception to "Maestro's authority ends at PR-open"; the `run` loop itself never writes to GitHub.

If `setup` listed missing Status options, add them on the board: **⋯ → Settings → Fields → Status → Add option**, named exactly as Maestro expects.

## 6. Install the worker skill in the driven repo

The repo must supply the skill named in `worker_prompt`. For Claude Code, that's `.claude/skills/<name>/SKILL.md`, where `<name>` is the first token after the `/` (default `cc-work`). [`templates/worker-skill.md`](../templates/worker-skill.md) is the contract — copy it across and adapt the quality-gate commands and column names. The skill's column names **must match** `maestro.yml` and the live board exactly.

## 7. Verify, then dry-run

```
maestro doctor          # read-only preflight checks; exits non-zero on failure
maestro once --dry-run  # one poll cycle that dispatches nothing
```

`doctor` mutates nothing; fix any failures and re-run (warnings don't affect exit code). `once --dry-run` lists each board issue and what the scheduler *would* do ("would dispatch", "skipped — already in flight", "skipped — blocked by open issue(s): #71", …). If it looks right, the live `run` behaves identically.

## Command reference

Run all of these from inside the driven repo:

```
maestro setup               # bootstrap the board's Priority field (idempotent)
maestro doctor              # read-only preflight checks
maestro run                 # start the poll/dispatch loop
maestro status              # snapshot of the board and recent activity
maestro status --watch      # live-refreshing worker-log view
maestro once --dry-run      # one poll; explain each dispatch decision
maestro logs <issue>        # dump an issue's latest-run worker log
maestro clean               # prune finished worktrees and stale branches
```

Run `maestro run` in one terminal; check on it with `maestro status` from another.

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
