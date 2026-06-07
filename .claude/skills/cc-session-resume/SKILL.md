---
name: cc-session-resume
description: Locate the most recent session bookmark in tmp/sessions/ and ingest it to resume cross-session context. Invoked as the first message of a new Claude Code session after the prior one saved with /cc-session-save.
---

# cc-session-resume

Resume from the most recent session bookmark. The operator invokes this as the first message of a new session. Follow these steps exactly.

## 1. Locate the bookmark

Run this in one Bash call. Anchors at the git root so cwd does not matter, bails loudly if no bookmark exists.

```bash
ROOT=$(git rev-parse --show-toplevel) || { echo "Not in a git repo"; exit 1; }
BOOKMARK=$(command ls -t "$ROOT"/tmp/sessions/*.md 2>/dev/null | head -1)
if [ -z "$BOOKMARK" ]; then
  echo "No session bookmarks found in tmp/sessions/. Did you run /cc-session-save in a prior session?"
  exit 1
fi
echo "Most recent bookmark: $BOOKMARK"
```

Note `command ls` — bypasses any shell alias of `ls` (for example to `eza`) that would otherwise break `-t` ordering.

## 2. Read the bookmark

Use the Read tool on the path printed by step 1. Read it fully. Every line is intentional.

## 3. Acknowledge and orient

Output a short response to the operator:

> Resumed from `tmp/sessions/<filename>.md` (<yyyy-mm-dd hh:mm>).
>
> **Where we are:** <paste the one-sentence active mental model from the bookmark>
>
> **Next concrete step:** <paste the one-paragraph next-step from the bookmark>
>
> Ready when you are.

Then **stop**. Wait for the operator. Do not auto-launch into the next step — the operator may want to redirect, adjust, or do something different. The bookmark gives you context; the operator gives you direction.

## 4. If the bookmark seems stale

If the bookmark is more than a week old, or `git status` shows substantial uncommitted changes that don't match what the bookmark describes, flag this in your response. The bookmark may not reflect the current state and re-reading docs (`CLAUDE.md`, `docs/how-it-works.md`, `docs/usage.md`) may be more reliable.
