---
name: cc-work
description: Autonomously work GitHub issue N end-to-end — branch, implement, verify, commit, push, open PR with Closes #N. Detects fresh/rework/resume mode automatically. Posts a structured blocked-question comment + moves card to Blocked if it hits genuine ambiguity. Operator merges the PR manually after review. Takes N (issue number) as required argument.
---

# cc-work

Autonomously run GitHub issue `N` from start to merge-ready PR. One operator invocation, one PR. Follow these steps exactly.

> **Canonical contract.** This skill implements the worker contract defined in this repo at `templates/worker-skill.md`. That file is the source of truth; any drift here is a bug.

## 0. Read these standing rules first

- **Blocked-question discipline.** If you hit genuine ambiguity that the issue body doesn't resolve, use the [Blocked sub-flow](#blocked-sub-flow) — never guess. The question comment must use **explicit answer slots** (A/B, fill-in, multiple choice), not open-ended prose. The bar is *genuine ambiguity*, not "this is hard."
- **Never invoke `AskUserQuestion`.** Any operator-facing decision MUST go through the [Blocked sub-flow](#blocked-sub-flow) — a `[BLOCKED]` comment on the issue, card moved to Blocked, non-zero exit. Headless `claude -p` (Maestro's dispatch mode) cannot render `AskUserQuestion` at all, so its appearance in a worker run is by definition a contract violation.
- **Never use `dangerouslyDisableSandbox: true`.** A sandbox refusal indicates a real environment problem — treat it as a blocker (Blocked sub-flow), never as something to work around.
- **End the turn silently after pushing and opening the PR.** No "want me to watch CI?" or "should I merge it?" prompts. The operator reviews and merges manually when satisfied.
- **Plain-checkbox subtasks.** Issue subtasks are `- [ ] description` lines. Work them in order; tick each via `gh issue edit` as you finish, so the issue body stays a live progress board.
- **One commit per run.** Don't amend prior commits; if rework is needed, push a new commit on top.
- **Dependency ordering is native-only.** Maestro decides which issues may run in parallel from GitHub's native "blocked by" relationship — never from body text or task-list references. Assume any issue you're handed has no open native blocker. If the body claims this issue depends on unfinished work that is *not* captured as a native "blocked by" relationship, don't race ahead — treat it as genuine ambiguity and use the [Blocked sub-flow](#blocked-sub-flow).
- **Honor this repo's coding standards.** Maestro's `CLAUDE.md` is binding: 100-col lines, full type hints, Google-style docstrings, `X | None` over `Optional`, no screen I/O in `core/`, and the architecture invariants (authority ends at PR-open, scheduler is pure, etc.). A change that breaks an invariant is a regression, not a feature.

## 1. Validate arguments

`N` is required and must be a positive integer. If missing or non-integer, stop with `Missing or invalid issue number. Usage: /cc-work N`.

## 2. Fetch issue state

```bash
gh issue view N --json title,body,state,comments
```

- If `state != "OPEN"`, stop: `Issue N is closed. Cannot work a closed issue.`
- Parse the title and body. The body's `## Subtasks` section is the work checklist; the `## Acceptance criteria` section is the verification target.

## 3. Derive branch slug

From the issue title:
1. Strip any leading template prefix matching `^\[[A-Z][a-z]*\]\s*` (e.g. `[Feature] `, `[Bug] `, `[Chore] `).
2. Lowercase.
3. Replace runs of non-alphanumeric chars with a single hyphen.
4. Strip leading/trailing hyphens.
5. Truncate to 40 chars at the last hyphen-bounded word boundary.

Branch name: `issue/<N>-<slug>`. Deterministic.

## 4. Guard the working tree

```bash
git status --porcelain --untracked-files=no
```

`--untracked-files=no` keeps operator-local untracked files (scratch notes, etc.) from tripping the guard — they won't roll into commits unless explicitly added.

If output is non-empty AND the current branch is not the expected `issue/N-<slug>` branch, stop with: `Working tree has tracked uncommitted changes on an unrelated branch. Stash or commit them first, then re-invoke /cc-work N.`

If the working tree is dirty on the *correct* `issue/N-<slug>` branch (resume scenarios), continue — those changes are presumed to be yours and will roll into the next commit.

## 5. Sync repo state

Ensure local main is current and dead local branches are cleaned up before doing any work.

```bash
git fetch origin --prune

current=$(git rev-parse --abbrev-ref HEAD)
# Skip the main checkout inside a linked worktree (Maestro dispatches /cc-work
# into one): git-dir and git-common-dir are equal only in the primary tree.
if [ "$current" != "issue/N-<slug>" ] && \
   [ "$(git rev-parse --git-dir)" = "$(git rev-parse --git-common-dir)" ]; then
  git checkout main
  git pull --ff-only
fi

# Force-delete local branches whose upstream is gone (merged + remote-deleted)
git for-each-ref refs/heads --format '%(refname:short) %(upstream:track)' \
  | awk '$2 == "[gone]" && $1 != "main" {print $1}' \
  | xargs -r git branch -D
```

The checkout-main step is skipped if we're already on the expected issue branch (resume/rework cases — the branch's own pull happens later in §8.1 / §9.1), or if we're running inside a linked worktree, where `main` lives in another tree and §7.1 cuts the branch straight from `origin/main`. Branch cleanup always runs.

## 6. Detect mode

Determine which of four modes you're in by reading repo + GitHub state:

```bash
# Does the branch exist locally or on origin?
git rev-parse --verify --quiet "issue/N-<slug>" || git ls-remote --heads origin "issue/N-<slug>"

# Does an open PR exist for this branch?
gh pr list --head "issue/N-<slug>" --state open --json number,state

# If a PR exists, what's its CI status and latest review state?
gh pr checks <pr-number>
gh pr view <pr-number> --json reviews,comments

# What's the last comment activity on the issue?
gh issue view N --json comments --jq '.comments[-3:]'
```

Use this table to pick the mode:

Evaluate rows top-to-bottom; first match wins.

| Branch exists? | Open PR exists? | New signal on PR or issue | → Mode |
|---|---|---|---|
| No | No | (n/a) | **Fresh** — go to §7 |
| Yes | (any) | Latest issue comment is operator reply to a `[BLOCKED]` comment from you | **Resume** — go to §9 |
| Yes | Yes | Failed CI checks OR new PR review comments since last commit | **Rework** — go to §8 |
| Yes | Yes | None of the above (clean PR sitting waiting) | **Weird state** — go to §10 |
| Yes | No | No blocked-reply signal | **Weird state** — go to §10 |

## 7. Fresh mode

### 7.1 Cut the branch

```bash
git checkout -b "issue/N-<slug>" origin/main
```

(`origin/main` is already up to date from §5's `git fetch origin --prune`.)

### 7.2 Implement subtasks

For each unchecked subtask in the issue body's `## Subtasks` section, **in order**:

1. Implement it. Use Read/Edit/Write tools for file changes. Use Bash for `gh`, `git`, `mkdir`, and inspection commands (`find`, `grep`, `ls`, `cd`).
2. If you hit genuine ambiguity → [Blocked sub-flow](#blocked-sub-flow). Stop the run.
3. Tick the subtask: see [Ticking a subtask](#ticking-a-subtask).

When all subtasks are checked, go to §7.3.

### 7.3 Verify

Maestro is a Python package with a fully automated gate. The worktree is provisioned with its own virtualenv at `.venv` (Maestro's `worktree_setup` runs `uv venv .venv && uv pip install -e '.[dev]'`), so **activate it first** — running the gate against the wrong interpreter is a contract violation.

```bash
source .venv/bin/activate
```

If `.venv/bin/activate` is missing or the gate tools are absent, that is an **environment blocker** (operator-side `worktree_setup` problem) — go to the [Blocked sub-flow](#blocked-sub-flow). Do not `pip install` tools yourself or run the gate against a global interpreter.

The gate is exactly the one in `CLAUDE.md`, run in order. Fix anything each step flags and re-run until all four are clean:

```bash
black .
ruff check .
mypy --strict maestro/
pytest
```

If the issue's `## Acceptance criteria` names an explicit verification command, run that too — it is part of the gate. The bar is: **all four gate commands pass, every new/changed line carries type hints and a Google-style docstring where the standards require one, and the acceptance criteria are met.** Re-run the full gate after any fix; a green gate on stale code proves nothing.

### 7.4 Commit, push, open PR

See [Ship sub-procedure](#ship-sub-procedure). Then **end the turn**.

## 8. Rework mode

### 8.1 Check out the branch

```bash
git checkout "issue/N-<slug>"
git pull --ff-only
```

### 8.2 Gather rework context

```bash
# PR number
PR=$(gh pr list --head "issue/N-<slug>" --state open --json number --jq '.[0].number')

# Review comments
gh pr view "$PR" --json reviews,comments

# Per-line review comments (different endpoint)
gh api "repos/{owner}/{repo}/pulls/$PR/comments"

# Failed CI logs
gh pr checks "$PR"
gh run view <run-id> --log-failed
```

Summarize the findings to chat in a tight block: `Rework signals on PR #<n>:` followed by a bulleted list of comments and failed-check excerpts. No fluff.

### 8.3 Address each item

Edit files to resolve each comment / CI failure. If a comment is itself ambiguous → [Blocked sub-flow](#blocked-sub-flow).

### 8.4 Re-verify

Same as §7.3.

### 8.5 Commit and push

```bash
git add <specific paths>
git commit -m "Address PR review feedback"  # or "Fix CI failures", etc.
git push  # branch is already tracking; no -u needed
```

Pushing the new commit re-triggers CI on the PR. Then move the card — see [Move card to In Review sub-procedure](#move-card-to-in-review-sub-procedure) — so the issue leaves the trigger column. **End the turn**: the operator will re-review and merge when satisfied.

## 9. Resume mode

### 9.1 Check out the branch

```bash
git checkout "issue/N-<slug>"
# Pull only if the branch has an upstream — skip for local-only branches
# left behind by a Fresh-mode block (no PR opened yet).
if git rev-parse --abbrev-ref --symbolic-full-name @{upstream} >/dev/null 2>&1; then
  git pull --ff-only
fi
```

### 9.2 Read the blocked exchange

Pull the most recent `[BLOCKED]` comment from yourself, and every comment from the operator that follows it:

```bash
gh issue view N --json comments --jq '.comments | map(select(.author.login != "github-actions[bot]")) | .[-5:]'
```

Identify:
- The question(s) you asked
- The operator's answer(s)

### 9.3 Continue work

Resume from where you blocked, applying the operator's direction. Continue ticking subtasks (§7.2). If new ambiguity arises → [Blocked sub-flow](#blocked-sub-flow) again.

When all subtasks complete, verify (§7.3) and then either:
- If no PR exists yet → [Ship sub-procedure](#ship-sub-procedure).
- If a PR already exists → commit + push only (§8.5).

**End the turn** after pushing.

## 10. Weird state

You're in this section because the state didn't match any of the three normal modes. Don't guess. Output a single block to chat:

> Invoked `/cc-work N` but state doesn't match a normal mode:
> - Branch `issue/N-<slug>`: <exists locally / exists on origin / does not exist>
> - Open PR: <#<n> / none>
> - Latest signal: <description, e.g. "PR sitting clean — no CI failures, no new review comments, no blocked-question reply">
>
> What did you want?

Then **stop**. Wait for chat direction.

---

## Ticking a subtask

After finishing a subtask, flip its checkbox in the issue body:

1. Fetch the body to a tmp file (`mkdir -p tmp` first if needed):
   ```bash
   gh issue view N --json body --jq '.body' > tmp/issue-N-body.md
   ```
2. Use the Edit tool to replace `- [ ] <description>` with `- [x] <description>`. The match must be exact — full line including the leading `- [ ] `.
3. Guard the file isn't empty or unflipped, then push back and clean up:
   ```bash
   test -s tmp/issue-N-body.md && grep -q "^- \[x\]" tmp/issue-N-body.md && \
     gh issue edit N --body-file tmp/issue-N-body.md && rm tmp/issue-N-body.md
   ```

Paths must be repo-relative (`tmp/...`).

## Blocked sub-flow

Trigger when you hit genuine ambiguity — *not* when a task is hard or you're uncertain about implementation details you can decide yourself. Bar: the issue body does not resolve a fork between two reasonable paths, and getting it wrong would require rework. An unusable environment (a gate tool the issue's verification needs is missing) is also a blocker.

### 1. Compose the comment

Use explicit answer slots. Good shape:

> **[BLOCKED]** Need a decision before I can continue.
>
> Subtask: <which one>
>
> The issue specifies <X> but not <Y>. I'm picking between:
>
> - **A:** <option, with one-line implication>
> - **B:** <option, with one-line implication>
>
> Reply with A or B (or describe a third path).

For an **environment blocker**, describe what is missing and what needs to be installed or configured, rather than offering A/B options.

Bad shape (never do this):
> What should this function return? Should I make it a generator or a list? Or something else?

### 2. Post the comment

Write the comment to `tmp/blocked-N.md` first via Write (creating `tmp/` with `mkdir -p tmp` if needed), then post:

```bash
gh issue comment N --body-file tmp/blocked-N.md
```

Repo-relative path required.

### 3. Commit and push any work in progress

Blocked runs must be resumable from GitHub state alone — under Maestro the worktree is discarded after a blocked exit, so anything left uncommitted is lost. For a **decision blocker** with real work done, commit and push it. For an **environment blocker** where nothing was verified, skip this — there is no trustworthy work to preserve.

```bash
if [ -n "$(git status --porcelain)" ]; then
  git add <specific paths — not -A>
  git commit -m "WIP: blocked on operator decision (see issue #N)"
  # First push for this branch needs -u; subsequent pushes don't.
  if [ -z "$(git ls-remote --heads origin issue/N-<slug>)" ]; then
    git push -u origin "issue/N-<slug>"
  else
    git push
  fi
fi
```

### 4. Move the project card to Blocked

Discover IDs (do this once per run; cache them mentally for re-use):

```bash
PROJ_NUM=$(gh project list --owner "@me" --format json --jq '.projects[] | select(.title=="Maestro") | .number')
ITEM_ID=$(gh project item-list "$PROJ_NUM" --owner "@me" --format json --limit 100 --jq '.items[] | select(.content.number==N) | .id')
FIELD_ID=$(gh project field-list "$PROJ_NUM" --owner "@me" --format json --jq '.fields[] | select(.name=="Status") | .id')
BLOCKED_OPT_ID=$(gh project field-list "$PROJ_NUM" --owner "@me" --format json --jq '.fields[] | select(.name=="Status") | .options[] | select(.name=="Blocked") | .id')
PROJ_ID=$(gh project list --owner "@me" --format json --jq '.projects[] | select(.title=="Maestro") | .id')

gh project item-edit --id "$ITEM_ID" --field-id "$FIELD_ID" --single-select-option-id "$BLOCKED_OPT_ID" --project-id "$PROJ_ID"
```

(Substitute the literal issue number for `N` in the ITEM_ID extraction line.)

### 5. Stop the run with a non-zero exit code

Output to chat (one line, no fluff):

> Posted [BLOCKED] question on issue #N. Card moved to Blocked. Re-run `/cc-work N` after replying.

Then **end the turn with a non-zero exit code** — for example, by issuing a final `Bash` call that exits non-zero:

```bash
exit 2
```

The exit code reports **process health**, not the run's classification: `0` means the process exited cleanly after parking its work, non-zero means it blocked or failed. What the orchestrator actually reads to classify the run is the card's resulting **Status** column — `In Review` → done, `Blocked` → blocked, still `In Progress` → crashed. Keep exit `2` here so a blocked run still exits non-zero; any non-zero value suffices.

## Ship sub-procedure

(Fresh mode finale, also called from Resume mode when no PR exists yet.)

### 1. Stage and commit

```bash
git add <specific paths — not -A>
git commit -m "$(cat <<'EOF'
<one-line subject describing the change>

<optional body: 1-2 sentences on the why>

Closes #N
EOF
)"
```

Subject style: imperative, sentence case, no trailing period, under 70 chars.

### 2. Push (first push for this branch)

```bash
# Detect first push by checking origin
if [ -z "$(git ls-remote --heads origin issue/N-<slug>)" ]; then
  git push -u origin "issue/N-<slug>"
else
  git push
fi
```

### 3. Open the PR

Write the PR body to `tmp/pr-body.md` via the Write tool first (creating `tmp/` with `mkdir -p tmp` if needed). Template:

```markdown
## Summary

<1-3 sentences: what this PR does and why.>

Closes #N

## Changes

- <bullet>
- <bullet>

## How to verify

- [ ] <verification step>
- [ ] <verification step>
```

Then create the PR and clean up:

```bash
gh pr create --title "<title — usually the issue title sans [Chore] prefix>" --body-file tmp/pr-body.md
rm tmp/pr-body.md
```

### 4. Move the card to In Review

See [Move card to In Review sub-procedure](#move-card-to-in-review-sub-procedure). Moving the card out of the trigger column keeps an automated dispatcher from re-picking an issue whose work is already parked in the PR.

### 5. End the turn

Output one line: `Opened PR #<n>. Review and merge when ready.` Then stop. Do not ask if the operator wants you to watch CI. Do not summarize the diff. Do not propose follow-ups. The operator merges manually (via the GitHub UI or `gh pr merge --admin`) after reviewing. The turn is over.

## Move card to In Review sub-procedure

> **Contract surface — do not rename casually.** The literal strings `Maestro` (project title), `Status`, `In Review`, and `Blocked` used in this card-move procedure must match the Maestro config's `trigger_column` / `review_column` / `blocked_column` and the live project board exactly. If a column is renamed, update all three places: this skill, the Maestro config (`maestro.yml`), and the board.

Called at the end of a successful run — after a PR is opened (Ship §4) or a rework commit is pushed (§8.5) — to move the issue's card from the trigger column into `In Review`. This keeps an automated dispatcher from re-picking an issue whose work is already parked in a PR.

Discover IDs (reuse any already discovered earlier in the run):

```bash
PROJ_NUM=$(gh project list --owner "@me" --format json --jq '.projects[] | select(.title=="Maestro") | .number')
ITEM_ID=$(gh project item-list "$PROJ_NUM" --owner "@me" --format json --limit 100 --jq '.items[] | select(.content.number==N) | .id')
FIELD_ID=$(gh project field-list "$PROJ_NUM" --owner "@me" --format json --jq '.fields[] | select(.name=="Status") | .id')
REVIEW_OPT_ID=$(gh project field-list "$PROJ_NUM" --owner "@me" --format json --jq '.fields[] | select(.name=="Status") | .options[] | select(.name=="In Review") | .id')
PROJ_ID=$(gh project list --owner "@me" --format json --jq '.projects[] | select(.title=="Maestro") | .id')

gh project item-edit --id "$ITEM_ID" --field-id "$FIELD_ID" --single-select-option-id "$REVIEW_OPT_ID" --project-id "$PROJ_ID"
```

(Substitute the literal issue number for `N` in the ITEM_ID extraction line.)

---

## What this skill does NOT do

- Does **not** force-push.
- Does **not** edit closed issues.
- Does **not** invoke other skills.
- Does **not** invoke `AskUserQuestion` — operator-facing decisions go through the Blocked sub-flow.
- Does **not** use `dangerouslyDisableSandbox: true` to bypass environment problems — those are blockers.
- Does **not** rely on the exit code to classify the run — the exit code only reports process health (`0` = exited cleanly after parking work, non-zero = blocked or failed). The orchestrator classifies the run from the card's resulting **Status** column (`In Review` → done, `Blocked` → blocked, still `In Progress` → crashed). A blocked run still exits non-zero (exit `2`).
