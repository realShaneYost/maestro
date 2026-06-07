# Maestro operator runbook

The linear setup checklist for a fresh Maestro install pointed at a fresh repository. Each step is copy-pasteable top to bottom. After the last step, `maestro run` is safe to start.

## 1. Authenticate `gh` with the `project` scope

```
gh auth status
gh auth refresh -s project
```

Maestro never stores credentials — it shells out to `gh` and trusts whatever `gh auth status` reports. The `project` scope is required to read and (during `maestro setup`) create Projects V2 fields.

## 2. Write `maestro.yml`

Copy the template and fill it in:

```
cp maestro.example.yml maestro.yml
```

Every key:

| Key | Default | What it does |
|---|---|---|
| `repo_path` | required | Absolute path to the driven git repository. |
| `repo_slug` | required | GitHub `owner/name` slug for that repository. |
| `project_number` | required | The Projects V2 board number. |
| `trigger_column` | required | Status-field value that means "dispatch this issue". |
| `review_column` | `"In Review"` | Status-field value the worker parks the card in after opening its PR. **Reaching this column is how Maestro classifies a run as `done`.** |
| `blocked_column` | `"Blocked"` | Status-field value the worker parks the card in when it cannot proceed. **Reaching this column is how Maestro classifies a run as `blocked`.** |
| `max_concurrent` | `2` | Hard ceiling on simultaneously running workers. |
| `poll_interval` | `60` | Seconds between board polls. |
| `worker_prompt` | `"/cc-work {issue}"` | Prompt template handed to each worker. Must contain `{issue}`. |
| `theme` | `"nord"` | Theme for `maestro status` — `nord`, `dark`, or `light`. |
| `worktree_setup` | `null` | Shell command run inside each fresh worktree to provision its environment. **Set this for any Python project whose workers change package code** — see [`github-setup.md`](github-setup.md). |

`trigger_column`, `review_column`, and `blocked_column` must be three **distinct** values; Maestro rejects the config otherwise.

## 3. Bootstrap the board

```
maestro setup
```

`maestro setup` is narrowly scoped and safe:

- It ensures a **Priority** single-select field exists on the board. If absent, it creates one with options `Critical`, `High`, `Normal`, `Low` (top-to-bottom order is the rank — see *Priority* below). If a Priority field already exists, **`setup` leaves it completely untouched** — no rename, no reorder, no added options.
- It checks the **Status** field has the configured `trigger_column`, `review_column`, and `blocked_column`. It **never** mutates the Status field — if any column is missing, it prints exactly which option names to add by hand.

Running `setup` twice changes nothing the second time.

> `maestro setup` is the **one deliberate exception** to invariant #1 ("Maestro's authority ends at PR-open"). It is a bootstrap-only command that may create a Priority field. The `run` loop itself never writes to GitHub, and nothing — anywhere, ever — merges a PR.

## 4. Add any missing Status columns

If `setup` listed any missing Status options, add them manually:

1. Open the project board.
2. **⋯ → Settings → Fields → Status**.
3. Add option, named exactly as Maestro expects.

The reference column layout is `Backlog`, `In Progress`, `In Review`, `Blocked`, `Done`. The names are configurable; only the names you use in `maestro.yml` and your worker skill must match the names on the board.

## 5. Install the worker skill in the driven repo

In the driven repository, install the skill the orchestrator will invoke. For Claude Code:

```
<repo_path>/.claude/skills/<name>/SKILL.md
```

where `<name>` is the first token after the leading `/` in `worker_prompt` (default `cc-work`). [`templates/worker-skill.md`](../templates/worker-skill.md) is the contract; copy it across and adapt the quality-gate command list and column names to the driven repo.

The skill's column names **must match** `trigger_column`, `review_column`, and `blocked_column` from `maestro.yml`, and the live board, exactly.

## 6. Verify with `maestro doctor`

```
maestro doctor
```

Doctor mutates nothing. It runs preflight checks and exits non-zero if any **fail**; warnings do not affect the exit code. Failures are actionable — fix them, re-run.

## 7. Final gate: `maestro once --dry-run`

```
maestro once --dry-run
```

A single poll cycle that dispatches nothing. The output lists each board issue and what the scheduler would do with it ("would dispatch", "skipped — already in flight", "skipped — blocked by open issue(s): #71", …). If the result looks right, the live `maestro run` will behave the same way.

## Priority

The Priority single-select field's options can be named anything — `Critical`, `High`, `Normal`, `Low` is the bootstrap default, but `P0`/`P1`/`P2`, `Now`/`Next`/`Later`, or `🚨`/`⚡`/`📦` all work identically.

**The rank is the option's POSITION in the field's defined option list. Index 0 dispatches first.** The names carry no meaning.

> **The option order on the Priority field's settings screen is load-bearing.** Reordering it silently changes the dispatch order. Be deliberate about it.

### Migrating an existing `P0`/`P1`/`P2` field

If the board already has a `P0`/`P1`/`P2` Priority field, **rename the options in place** — do not delete and recreate:

1. Project board → **⋯ → Settings → Fields → Priority**.
2. Pencil-icon each option in turn: `P0 → Critical`, `P1 → High`, `P2 → Normal`.
3. **Add option** → `Low` (at the bottom).

Renaming preserves option IDs, so every existing card keeps its Priority value. Delete-and-recreate loses every card's value.

This rename is purely cosmetic — Maestro never relied on the `P0`/`P1`/`P2` names. The index-based rank (`P0 = 0 = "Critical"`) is unchanged by the rename, so there is no flag day: the rename can happen before or after the code lands.

## Known limitations

- Maestro's board query and open-issues query both use `first: 100` without cursor pagination. Boards larger than 100 items, or repositories with more than 100 open issues, are silently truncated.
- The Priority field's option order is scheduling semantics. Reordering it changes which issues dispatch first.
- A worker that exits without parking its card is classified as `failed`, even on exit code 0. That is intentional: the column is the source of truth, not the exit code.
