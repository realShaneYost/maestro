# Worker skill template

This is the contract Maestro expects of a *worker skill* — the skill it invokes, once per issue, in the repository it drives. Copy this file into the driven repository's skill system (for Claude Code, `.claude/skills/cc-work/SKILL.md`) and adapt the repo-specific parts: the quality-check commands in step 5 and the project board's column names in the fresh, rework, and blocked flows.

It is written as plain instructions, with no dependency on any one agent's features, so it works with Claude out of the box and ports to any capable coding agent.

## Contract

Maestro invokes the worker with one input — an **issue number** — via the configured `worker_prompt` (default `/cc-work {issue}`). When the worker starts:

- The current working directory is a **prepared git worktree**: a detached checkout at the tip of `origin/main`. The worker creates its own branch. It must not assume `main` is checked out — `main` lives in the repository's primary worktree, and `git checkout main` will fail here.
- The worker runs the issue end to end and **opens a pull request**. It must **never merge** — a human reviews and merges.
- The worker process exits **only after every piece of durable state is parked in GitHub** — the PR is opened, any comment is posted, and the issue's project card has been moved to the correct column. Maestro classifies the run by *which* column the card ends up in, so leaving the card in the trigger column is a contract violation that Maestro records as `failed` regardless of exit code.
- The column names this skill uses for parking (the trigger, review, and blocked columns) **must match the orchestrator's configured `trigger_column`, `review_column`, and `blocked_column` exactly, and must match the live board**. A rename has to happen in all three places at once.

## Steps

1. **Read the issue.** Fetch issue `N`'s title, body, and comments. If the issue is closed, stop. Treat the body's task list as the work checklist and its acceptance criteria as the verification target.

2. **Create a branch.** From inside the worktree, branch off current `origin/main` — for example: `git fetch origin` then `git checkout -b issue/<N>-<slug> origin/main`. This form works inside a worktree; `git checkout main` does not.

3. **Decide the mode.** **A pull request already exists for this issue's branch → rework.** Otherwise → fresh work. *Detection keys on PR existence, not card position.* That is the self-healing invariant: a worker that dies between PR-open and card-move is recovered on the next dispatch, because the next worker still sees the PR and resumes in rework mode. **Do not change this detection rule.**

4. **Implement.** Make the changes, following the repository's existing conventions and standards. On **rework**, address the PR's review comments, inline diff comments, any failed CI logs, and any new issue comments.

5. **Verify.** Run the repository's quality gates — formatter, linter, type-checker, tests — and fix anything that fails. *Adapt this command list to the driven repository.* If the gates will not run because the environment itself is broken (missing tools or extras), that is a blocker — see *When genuinely blocked* — not a bootstrap task.

6. **Commit and push.** Make one commit per run and push the branch.

7. **Park the work in GitHub, then exit.** The exit code reports process health (`0` = exited after parking cleanly, non-zero = blocked or failed); the **card's resulting column** is the semantic outcome Maestro classifies the run by (review column → `done`, blocked column → `blocked`, still in trigger column → `failed`). The sub-flow depends on the mode:

   **Fresh flow:**
   1. Open the pull request with a short summary and a `Closes #N` line.
   2. Move the issue's project card to the **review column** (`review_column` in `maestro.yml`).
   3. **Exit 0.** Do not merge. Do not wait on CI.

   **Rework flow:**
   1. Push the new commit (already done in step 6).
   2. Move the issue's project card to the **review column** — pushing the commit is **not** enough on its own, because Maestro classifies the run by the card's resulting column.
   3. **Exit 0.** Do not merge.

   The order is contract: finalize GitHub state (PR, card) *before* the process exits.

## When genuinely blocked

The worker must escalate to the operator — never guess, never improvise — when:

- The issue does not resolve a real fork between two reasonable paths.
- The prepared environment is unusable (for example, the worktree's venv is missing tools or extras needed to verify the work). That is an operator-side configuration problem, typically in `worktree_setup`; do not bootstrap it yourself or escalate sandbox permissions to work around it.

In either case:

1. **Push any work-in-progress that is worth resuming from.** If the working tree contains verified, intentional changes (typical for a decision blocker mid-implementation), commit them on the issue branch with a message like `WIP: blocked on operator decision (see issue #N)` and push (`-u` on first push). If the changes are unverified or absent — typical for an **environment blocker** where verification never ran — skip the commit; pushing half-baked code there is worse than pushing nothing, and the blocker comment carries the resumption context. Under Maestro the worktree is discarded after a blocked exit, so what isn't pushed is gone.
2. **Post a comment on the issue.** For a **decision blocker**, use **explicit answer slots** — labelled `A:` / `B:` options, each with a one-line implication — never open-ended prose. For an **environment blocker**, describe what is missing and what needs to be installed or configured.
3. **Move the issue's project card to the blocked column** (`blocked_column` in `maestro.yml`).
4. **Exit with a non-zero status** (exit `2` is the convention).

The operator answers (or fixes the environment) and moves the card back into the trigger column; Maestro re-dispatches the issue, the worker reads the new comments and any pushed WIP, and resumes from there.

## What the worker must never do

- Never merge, never force-push, never edit a closed issue.
- Never assume `main` is checked out — always work from `origin/main`.
- Never wait on CI or ask whether to merge — open the PR, move the card to the review column, and exit.
- Never use interactive tools like `AskUserQuestion`. Workers run headless (`claude -p ...`), so interactive prompts have no UI and go nowhere; operator-facing decisions go on the issue, per *When genuinely blocked*.
- Never escalate sandbox permissions (for example, `dangerouslyDisableSandbox`) to work around a broken environment. Call it out as a blocker instead.
