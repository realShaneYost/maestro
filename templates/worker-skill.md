# Worker skill template

This is the contract Maestro expects of a *worker skill* — the skill it invokes, once per issue, in the repository it drives. Copy this file into the driven repository's skill system (for Claude Code, `.claude/skills/cc-work/SKILL.md`) and adapt the repo-specific parts: the quality-check commands in step 5 and the project board's column names in the blocked flow.

It is written as plain instructions, with no dependency on any one agent's features, so it works with Claude out of the box and ports to any capable coding agent.

## Contract

Maestro invokes the worker with one input — an **issue number** — via the configured `worker_prompt` (default `/cc-work {issue}`). When the worker starts:

- The current working directory is a **prepared git worktree**: a detached checkout at the tip of `origin/main`. The worker creates its own branch. It must not assume `main` is checked out — `main` lives in the repository's primary worktree, and `git checkout main` will fail here.
- The worker runs the issue end to end and **opens a pull request**. It must **never merge** — a human reviews and merges.
- The worker process exits once its work is parked: a PR opened, or a blocked question posted. Maestro then records the run and removes the worktree.

## Steps

1. **Read the issue.** Fetch issue `N`'s title, body, and comments. If the issue is closed, stop. Treat the body's task list as the work checklist and its acceptance criteria as the verification target.

2. **Create a branch.** From inside the worktree, branch off current `origin/main` — for example: `git fetch origin` then `git checkout -b issue/<N>-<slug> origin/main`. This form works inside a worktree; `git checkout main` does not.

3. **Decide the mode.** If a pull request already exists for this issue's branch, this is a **rework**: read the PR's review comments, the inline diff comments, any failed CI logs, and any new issue comments, then address them. Otherwise it is **fresh** work: implement the issue from its checklist.

4. **Implement.** Make the changes, following the repository's existing conventions and standards.

5. **Verify.** Run the repository's quality gates — formatter, linter, type-checker, tests — and fix anything that fails. *Adapt this command list to the driven repository.*

6. **Commit and push.** Make one commit per run and push the branch.

7. **Open the pull request** (fresh mode only), with a short summary and a `Closes #N` line. On rework, pushing the new commit is enough. Then **stop** — do not merge, and do not wait on CI.

## When genuinely blocked

If the issue does not resolve a real fork between two reasonable paths, do not guess:

1. Post a comment on the issue with **explicit answer slots** — labelled `A:` / `B:` options, each with a one-line implication — never open-ended prose.
2. Move the issue's project card to the **Blocked** column.
3. Stop.

The operator answers and moves the card back into the trigger column; Maestro re-dispatches the issue, and the worker resumes from the answer.

## What the worker must never do

- Never merge, never force-push, never edit a closed issue.
- Never assume `main` is checked out — always work from `origin/main`.
- Never wait on CI or ask whether to merge — open the PR and stop.
