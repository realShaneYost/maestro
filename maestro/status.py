"""Worker status — a live, colour-coded view of what workers are doing.

Worker logs are newline-delimited JSON (Claude's ``stream-json`` format). This
module turns each event into one readable line — issue, time, and a short
message — and either prints a snapshot or streams new lines live. The issue
title is fetched once via ``gh`` and cached; everything else is read from the
per-issue log files.
"""

from __future__ import annotations

import json
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.text import Text


@dataclass(frozen=True)
class LogLine:
    """One rendered line of worker activity.

    Attributes:
        issue: The issue number whose worker emitted the event.
        message: A short human-readable summary of the event.
        is_error: Whether the event represents a failure.
    """

    issue: int
    message: str
    is_error: bool


def _issue_of(log_path: Path) -> int | None:
    """Return the issue number from a log filename like ``issue-58.log``."""
    name = log_path.stem
    if not name.startswith("issue-"):
        return None
    suffix = name[len("issue-") :]
    return int(suffix) if suffix.isdigit() else None


def _short_title(title: str, width: int = 15) -> str:
    """Truncate ``title`` to ``width`` characters, adding an ellipsis if cut."""
    clean = title.strip()
    if len(clean) <= width:
        return clean
    return clean[: width - 1] + "…"


def _fetch_title(repo_slug: str, issue: int, cache: dict[int, str]) -> str:
    """Return issue ``issue``'s title, fetched once via ``gh`` and cached.

    Best-effort: any failure (no network, gh error, unknown issue) yields an
    empty string, and the rendered line falls back to just the issue number.
    """
    if issue in cache:
        return cache[issue]
    title = ""
    try:
        result = subprocess.run(
            [
                "gh",
                "issue",
                "view",
                str(issue),
                "--repo",
                repo_slug,
                "--json",
                "title",
                "--jq",
                ".title",
            ],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        title = result.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        title = ""
    cache[issue] = title
    return title


def _format_duration(ms: float) -> str:
    """Render a millisecond duration as a short string such as ``4m49s``."""
    seconds = int(ms / 1000)
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m{seconds % 60:02d}s"


def _describe_tool(block: dict[str, Any]) -> str:
    """Describe a ``tool_use`` block — the tool and what it acted on."""
    name = str(block.get("name", "tool"))
    raw_input = block.get("input")
    args = raw_input if isinstance(raw_input, dict) else {}
    if name == "Bash":
        detail = args.get("description") or args.get("command") or ""
    else:
        detail = args.get("file_path") or ""
    lines = str(detail).strip().splitlines()
    return f"{name}: {lines[0]}" if lines else name


def _summarize(event: dict[str, Any]) -> str | None:
    """Summarise one stream-json event as a short line, or ``None`` to skip it.

    Tool-result (``user``) events are skipped — they are too noisy to show.
    """
    etype = event.get("type")
    if etype == "system":
        return f"session {event.get('subtype', 'event')}"
    if etype == "rate_limit_event":
        return "rate limit reached — waiting"
    if etype == "result":
        parts: list[str] = []
        duration = event.get("duration_ms")
        if isinstance(duration, (int, float)):
            parts.append(_format_duration(duration))
        turns = event.get("num_turns")
        if isinstance(turns, int):
            parts.append(f"{turns} turns")
        suffix = f" — {' · '.join(parts)}" if parts else ""
        return f"finished: {event.get('subtype', 'done')}{suffix}"
    if etype == "assistant":
        message = event.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        blocks = content if isinstance(content, list) else []
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                return _describe_tool(block)
        for block in blocks:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text", "")
                if isinstance(text, str) and text.strip():
                    return text.strip().splitlines()[0]
        return "thinking"
    return None


def _parse_event_line(issue: int, raw: str) -> LogLine | None:
    """Parse one raw stream-json log line into a :class:`LogLine`, or ``None``.

    A blank line yields ``None``. A line that is not JSON is shown verbatim and
    flagged as an error — a worker should emit only JSON, so plain text means
    something went wrong.
    """
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        event = json.loads(stripped)
    except json.JSONDecodeError:
        return LogLine(issue=issue, message=stripped, is_error=True)
    if not isinstance(event, dict):
        return None
    message = _summarize(event)
    if message is None:
        return None
    is_error = bool(event.get("is_error")) or str(event.get("subtype", "")).startswith("error")
    return LogLine(issue=issue, message=message, is_error=is_error)


def _render_line(line: LogLine, stamp: str, title: str) -> Text:
    """Build the rich :class:`~rich.text.Text` for one rendered log line."""
    label = f"#{line.issue}"
    if title:
        label += f" {_short_title(title)}"
    text = Text()
    text.append(label, style="issue")
    text.append("  ")
    text.append(stamp, style="timestamp")
    text.append("  ")
    text.append(line.message, style="error" if line.is_error else "message")
    return text


def recent_lines(logs_dir: Path, per_log: int = 20) -> list[LogLine]:
    """Return up to ``per_log`` recent rendered lines from each worker log.

    Args:
        logs_dir: Directory holding the per-issue log files.
        per_log: Maximum lines to take from the tail of each log.

    Returns:
        Rendered lines, grouped by issue (ascending), oldest first within each.
    """
    if not logs_dir.is_dir():
        return []
    lines: list[LogLine] = []
    for log_path in sorted(logs_dir.glob("issue-*.log")):
        issue = _issue_of(log_path)
        if issue is None:
            continue
        raw = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for entry in raw[-per_log:]:
            parsed = _parse_event_line(issue, entry)
            if parsed is not None:
                lines.append(parsed)
    return lines


def render_recent(console: Console, logs_dir: Path, repo_slug: str) -> None:
    """Print a one-shot snapshot of recent worker activity."""
    lines = recent_lines(logs_dir)
    if not lines:
        console.print("No worker activity.")
        return
    stamp = datetime.now().strftime("%H:%M:%S")
    titles: dict[int, str] = {}
    for line in lines:
        title = _fetch_title(repo_slug, line.issue, titles)
        console.print(_render_line(line, stamp, title))


def _drain(
    console: Console,
    log_path: Path,
    issue: int,
    offsets: dict[Path, int],
    repo_slug: str,
    titles: dict[int, str],
) -> None:
    """Render log lines appended to ``log_path`` since the last poll.

    The first time a log is seen its current end is recorded and nothing is
    printed — ``watch`` streams only activity from when it started.
    """
    data = log_path.read_bytes()
    if log_path not in offsets:
        offsets[log_path] = len(data)
        return
    if len(data) <= offsets[log_path]:
        return
    fresh = data[offsets[log_path] :].decode("utf-8", errors="replace")
    offsets[log_path] = len(data)
    stamp = datetime.now().strftime("%H:%M:%S")
    title = _fetch_title(repo_slug, issue, titles)
    for entry in fresh.splitlines():
        parsed = _parse_event_line(issue, entry)
        if parsed is not None:
            console.print(_render_line(parsed, stamp, title))


def watch(console: Console, logs_dir: Path, repo_slug: str, interval: float = 1.0) -> None:
    """Stream new worker-log activity to the console until interrupted."""
    console.print("Maestro — watching workers (Ctrl-C to stop)", style="header")
    offsets: dict[Path, int] = {}
    titles: dict[int, str] = {}
    try:
        while True:
            logs = sorted(logs_dir.glob("issue-*.log")) if logs_dir.is_dir() else []
            for log_path in logs:
                issue = _issue_of(log_path)
                if issue is not None:
                    _drain(console, log_path, issue, offsets, repo_slug, titles)
            time.sleep(interval)
    except KeyboardInterrupt:
        pass
