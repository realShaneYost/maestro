# CLAUDE.md

Project-specific guidance for Claude Code working in the Maestro repository. Read it before generating, modifying, or reviewing code here.

## Project Overview

**Maestro** is a standalone orchestrator that watches a GitHub Projects V2 board and dispatches autonomous coding agents to work issues. Move an issue's card into the trigger column; Maestro spawns a worker (a coding-agent CLI in headless mode) inside an isolated git worktree, the worker runs the issue end-to-end and opens a pull request, and a human reviews and merges it.

Maestro is deliberately a plain program, not a long-running AI session. It was conceived alongside the Jubilee project but is generic — it drives any GitHub repository that supplies a compatible worker skill.

## Current Status

**v1 build complete.** Built in vertical slices, each with tests; `black`,
`ruff`, `mypy --strict`, and `pytest` all green. For a fresh setup, see
`docs/runbook.md`.

1. Foundation — config, core models, scheduler, CLI skeleton. *(done)*
2. Worktree management + worker dispatch. *(done)*
3. Run log + `status` / `status --watch`. *(done)*
4. `clean`. *(done)*
5. The `run` loop — trigger → scheduler → dispatch → run log. *(done)*
6. `templates/worker-skill.md`. *(done)*
7. GitHub trigger source (`StatusFieldTrigger`) — Projects V2 board polling. *(done)*
8. `run` / `once` wired to the trigger. *(done)*
9. Board-semantics upgrade — column-based outcome classification, Priority
   rank by option order, `review_column` / `blocked_column` config,
   self-explaining `--dry-run`, board summary in `maestro status`,
   `maestro doctor`, `maestro setup`, and stale-worktree auto-discard on
   dispatch. *(done)*

See `docs/architecture.md` for the full design.

## Tech Stack

| Component        | Choice                | Notes                                             |
|------------------|-----------------------|---------------------------------------------------|
| Language         | Python 3.11+          |                                                   |
| GitHub access    | `gh` CLI (subprocess) | REST + GraphQL; Maestro stores no credentials.    |
| Board polling    | Projects V2 (GraphQL) | via `gh api graphql`.                             |
| Worker isolation | git worktrees         | one worktree per dispatched issue.                |
| Terminal output  | `rich`                | tables, colour themes, the `status --watch` view. |
| Config           | YAML (`maestro.yml`)  | parsed with PyYAML.                               |

## Coding Standards

Strict PEP 8 plus the rules below.

### Formatting
- Line length: 100 columns, hard limit — code files only. Markdown is exempt; write each paragraph as one logical line.
- 4-space indentation, never tabs. UTF-8. No trailing whitespace. A final newline on every file.
- Double quotes preferred.

### Tooling
- Format with **Black** (`line-length = 100`).
- Lint with **Ruff** (`E, W, F, I, N, D, UP, B, SIM`; Google docstring convention).
- Type-check with **mypy** in `--strict` mode.
- `pyproject.toml` pins every setting. Gate before committing:
  `black . && ruff check . && mypy --strict maestro/ && pytest`.

### Types & Docstrings
- Type-hint every function signature and module-level variable.
- Prefer `X | None` over `Optional` (PEP 604); built-in generics over `typing` aliases (PEP 585).
- Every public module, class, and function gets a Google-style docstring (`Args:`, `Returns:`, `Raises:`).

### Error Handling & Logging
- Never bare `except:`. Catch specific exceptions; let unknown ones propagate. Chain with `raise ... from err`.
- Use the standard `logging` module — never `print` in library code. The CLI layer may write to stdout; `core/` must not.

### Imports
- Group stdlib, third-party, local — blank-line separated, alphabetical within groups. Absolute imports. No wildcards.

### Testing
- pytest, under `tests/`, mirroring the package. `test_<unit>.py` files, `test_<behavior>` functions.
- The scheduler and config are pure code — cover them thoroughly. The GitHub and subprocess layers are tested with fakes; no test may need network access or real credentials.

## Project Structure

```
maestro/
├── CLAUDE.md
├── README.md
├── pyproject.toml
├── maestro.example.yml
├── maestro/
│   ├── __main__.py        # python -m maestro
│   ├── cli.py             # argparse entry point
│   ├── config.py          # maestro.yml loading + validation
│   └── core/
│       ├── models.py      # Issue, WorkerState, RunRecord
│       └── scheduler.py   # pure dispatch-decision logic
├── docs/
├── templates/             # worker-skill template (model-agnostic)
└── tests/
```

## Architecture & Invariants

These are hard rules. Code that breaks one is a regression and must be rejected in review.

1. **Maestro's authority ends at PR-open.** It dispatches workers; they open PRs. It never merges, rebases, or force-pushes — a human reviews and merges. Its entire git surface is `worktree add` / `remove` / `prune`.
2. **`core/` never does screen I/O.** Core functions return data; the CLI and any future TUI are thin renderers over `core`. This is what keeps a TUI a cheap future add.
3. **The scheduler is pure, deterministic code — no language model, ever.** It reads structured inputs (trigger-column membership, declared blockers, priority) and returns a dispatch list. The reasoning was already spent when the issue was written.
4. **Workers are stateless and isolated.** Each runs one issue in its own worktree, knows nothing of sibling workers, and exits after opening its PR. All durable state lives in GitHub; re-dispatch (rework, resume-after-blocked) is just the card re-entering the trigger column.
5. **Logs live outside worktrees.** `logs/issue-N.log` and `runs.jsonl` persist as the audit trail; worktree removal never touches them.
6. **`status --watch` is read-only.** It renders state; it never controls the loop.

## Worker Contract

Maestro spawns a worker via a configurable prompt (default `/cc-work {issue}`). Any repository it drives must supply a worker skill that takes an issue number, works within a prepared git worktree, and opens a PR. `templates/worker-skill.md` documents this contract in model-agnostic prose — it works with Claude out of the box and is portable to any capable coding agent.
