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

5. **Verify.** Run the repository's quality gates — formatter, linter, type-checker, tests — and fix anything that fails. *Adapt this command list to the driven repository.* If the gates will not run because the environment itself is broken (missing tools or extras), that is a blocker — see *When genuinely blocked* — not a bootstrap task.

6. **Commit and push.** Make one commit per run and push the branch.

7. **Open the pull request** (fresh mode only), with a short summary and a `Closes #N` line. On rework, pushing the new commit is enough. Then **stop** — do not merge, and do not wait on CI.

## When genuinely blocked

The worker must escalate to the operator — never guess, never improvise — when:

- The issue does not resolve a real fork between two reasonable paths.
- The prepared environment is unusable (for example, the worktree's venv is missing tools or extras needed to verify the work). That is an operator-side configuration problem, typically in `worktree_setup`; do not bootstrap it yourself or escalate sandbox permissions to work around it.

In either case:

1. Post a comment on the issue. For a **decision blocker**, use **explicit answer slots** — labelled `A:` / `B:` options, each with a one-line implication — never open-ended prose. For an **environment blocker**, describe what is missing and what needs to be installed or configured.
2. Move the issue's project card to the **Blocked** column.
3. Stop with a **non-zero exit status**. A clean exit tells Maestro the run completed successfully and would be recorded as `done`; a non-zero exit records it as a failure, which is the truthful outcome for a blocker.

The operator answers (or fixes the environment) and moves the card back into the trigger column; Maestro re-dispatches the issue, and the worker resumes from the answer.

## What the worker must never do

- Never merge, never force-push, never edit a closed issue.
- Never assume `main` is checked out — always work from `origin/main`.
- Never wait on CI or ask whether to merge — open the PR and stop.
- Never use interactive tools like `AskUserQuestion`. Workers run headless (`claude -p ...`), so interactive prompts have no UI and go nowhere; operator-facing decisions go on the issue, per *When genuinely blocked*.
- Never escalate sandbox permissions (for example, `dangerouslyDisableSandbox`) to work around a broken environment. Call it out as a blocker instead.
