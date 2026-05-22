---
name: cc-session-save
description: Write a structured session bookmark to tmp/sessions/<timestamp>.md so the NEXT Claude session can resume context via /cc-session-resume. Captures decisions, file changes, open questions, and the next concrete step. The summary is optimized for future-Claude consumption, not for the operator to read.
---

# cc-session-save

Save a session bookmark for cross-session continuity. The operator invokes this at the end of a session before closing Claude Code. Follow these steps exactly.

## 1. Gather file-change context

Run this in one Bash call. Anchors at the git root and bootstraps the directory.

```bash
ROOT=$(git rev-parse --show-toplevel) || { echo "Not in a git repo"; exit 1; }
mkdir -p "$ROOT/tmp/sessions"
TS=$(date +"%Y-%m-%d-%H-%M")
echo "=== bookmark target path ==="
echo "$ROOT/tmp/sessions/$TS.md"
echo
echo "=== git status (uncommitted) ==="
(cd "$ROOT" && git status --short)
echo
echo "=== last 5 commits ==="
(cd "$ROOT" && git log --oneline -5)
echo
echo "=== files modified in last 24h (excluding noise) ==="
(cd "$ROOT" && find . -type f -mtime -1 \
  -not -path './.git/*' \
  -not -path './.venv/*' \
  -not -path './node_modules/*' \
  -not -path './maestro.egg-info/*' \
  -not -path './tmp/sessions/*' \
  2>/dev/null | head -40)
```

## 2. Inspect the conversation

Look through the conversation in your context window. Extract:

- **Decisions made this session** — architectural choices, design calls, anything non-obvious. Always include the *why* so future-Claude doesn't re-litigate.
- **Files modified or created** — cross-reference the git output from step 1. For each, one-line *why it was touched*.
- **Open questions / deferred items** — anything discussed but not resolved.
- **The next concrete step** — what's the very next action when the operator resumes.
- **Active mental model** — 2-3 sentences orienting future-Claude to where this session sits in the larger project arc.

## 3. Write the bookmark

Write to the bookmark target path printed by step 1 (the line under `=== bookmark target path ===`). Use the Write tool with that absolute path verbatim — no need to recompute the timestamp or run `date` again.

**This file is for future-Claude, not the operator.** Be dense, drop pleasantries, use file paths liberally. Target 300–500 words. Sections required:

```markdown
# Session bookmark — <yyyy-mm-dd hh:mm>

## Active mental model
<2-3 sentences orienting where this session sits in the larger arc>

## Decisions made this session
- <decision>. **Why:** <reason>. **Where it lives:** <file path or memory ref if persisted, "in conversation only" if not>
- ...

## Files modified or created
- `<path>` — <one-line why>
- ...

## Open questions / deferred items
- <item> — <current state>
- ...

## Next concrete step
<one paragraph: the very first action the operator and next-Claude should take when resuming>

## Pointers for next-Claude
- Key docs to skim: <paths>
- Key memory entries: <names from MEMORY.md>
- Anything else not in docs/memory worth knowing
```

If a section has nothing to record, write `(none this session)` rather than omitting it.

## 4. Confirm back to the operator

Output one short message:

> Session bookmark written to `tmp/sessions/<filename>.md`. To resume in a new session, invoke `/cc-session-resume` as your first message.

Do not paste the bookmark content into chat — it is optimized for machine consumption, not display.
