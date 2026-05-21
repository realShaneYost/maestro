"""GitHub Projects V2 trigger source.

:class:`StatusFieldTrigger` polls a GitHub Projects V2 board and reports it as a
:class:`~maestro.core.models.BoardState`. Each poll makes two read-only calls
through the ``gh`` CLI:

* ``gh project item-list`` — the board items, each with its issue number,
  title, Status-field column, and Priority-field value.
* ``gh api graphql`` — every open issue in the repository with its native
  "Blocked by" dependencies, yielding the open-issue set and the dependency map.

Maestro stores no credentials; ``gh`` supplies authentication.
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from typing import Any

from maestro.core.models import BoardState, Issue

# Runs a gh command (the args after "gh") and returns its stdout. Injectable so
# tests can supply canned responses; the default really runs gh.
GhRunner = Callable[[list[str]], str]

# Open issues and their dependencies. NOTE: `first: 100` does not paginate; a
# repository with more than 100 open issues would need cursor pagination.
_OPEN_ISSUES_QUERY = """
query($owner: String!, $name: String!) {
  repository(owner: $owner, name: $name) {
    issues(first: 100, states: [OPEN]) {
      nodes {
        number
        blockedBy(first: 50) { nodes { number } }
      }
    }
  }
}
"""


class GitHubError(RuntimeError):
    """Raised when a GitHub call fails or returns unexpected data."""


def _run_gh(args: list[str]) -> str:
    """Run ``gh`` with ``args`` and return its stdout.

    Raises:
        GitHubError: If gh exits with a non-zero status.
    """
    try:
        result = subprocess.run(["gh", *args], capture_output=True, text=True, check=True)
    except subprocess.CalledProcessError as err:
        raise GitHubError(f"gh {' '.join(args)} failed: {err.stderr.strip()}") from err
    return result.stdout


def _parse_priority(value: object) -> int | None:
    """Map a Priority-field value such as ``P1`` to a sort key.

    Lower is higher priority: ``P0`` becomes 0, ``P1`` becomes 1, and so on.
    Anything that is not ``P<digits>`` (including an unset field) yields
    ``None``, which the scheduler treats as lowest priority.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if len(text) >= 2 and text[0] in {"P", "p"} and text[1:].isdigit():
        return int(text[1:])
    return None


def _parse_open_issues(payload: dict[str, Any]) -> tuple[set[int], dict[int, frozenset[int]]]:
    """Parse the open-issues GraphQL payload.

    Args:
        payload: The decoded JSON from the open-issues query.

    Returns:
        A pair ``(open_numbers, blocked_by)``: every open issue number, and a
        map from an issue number to the set of issue numbers it is blocked by.

    Raises:
        GitHubError: If the payload is not shaped as expected.
    """
    try:
        nodes = payload["data"]["repository"]["issues"]["nodes"]
    except (KeyError, TypeError) as err:
        raise GitHubError(f"Unexpected open-issues response: {payload!r}") from err
    open_numbers: set[int] = set()
    blocked_by: dict[int, frozenset[int]] = {}
    for node in nodes:
        number = node["number"]
        open_numbers.add(number)
        blockers = node.get("blockedBy", {}).get("nodes", [])
        blocked_by[number] = frozenset(blocker["number"] for blocker in blockers)
    return open_numbers, blocked_by


def _build_issues(
    items: list[dict[str, Any]],
    trigger_column: str,
    blocked_by: dict[int, frozenset[int]],
) -> list[Issue]:
    """Build :class:`Issue` records from raw board items.

    Only items whose content is a GitHub issue are kept; pull requests and
    draft items on the board are skipped.
    """
    issues: list[Issue] = []
    for item in items:
        content = item.get("content", {})
        if content.get("type") != "Issue":
            continue
        number = content["number"]
        issues.append(
            Issue(
                number=number,
                title=content.get("title", ""),
                priority=_parse_priority(item.get("priority")),
                blocked_by=blocked_by.get(number, frozenset()),
                in_trigger_column=item.get("status") == trigger_column,
            )
        )
    return issues


def _decode_items(raw: str) -> list[dict[str, Any]]:
    """Decode the JSON returned by ``gh project item-list``."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as err:
        raise GitHubError(f"Invalid project item-list response: {raw!r}") from err
    items = data.get("items", []) if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise GitHubError(f"Unexpected project item-list response: {data!r}")
    return items


class StatusFieldTrigger:
    """Polls a GitHub Projects V2 board for the current board state."""

    def __init__(
        self,
        repo_slug: str,
        project_number: int,
        trigger_column: str,
        runner: GhRunner = _run_gh,
    ) -> None:
        """Initialise the trigger.

        Args:
            repo_slug: GitHub ``owner/name`` slug of the driven repository.
            project_number: Projects V2 board number.
            trigger_column: Status-field value that means "dispatch this issue".
            runner: Callable that runs gh; injectable for tests.
        """
        self._repo_slug = repo_slug
        self._project_number = project_number
        self._trigger_column = trigger_column
        self._run = runner

    def poll(self) -> BoardState:
        """Return the board's current state.

        Raises:
            GitHubError: If a gh call fails or returns unexpected data.
        """
        items = self._board_items()
        open_numbers, blocked_by = self._open_issues()
        issues = _build_issues(items, self._trigger_column, blocked_by)
        return BoardState(issues=issues, open_numbers=open_numbers)

    def _board_items(self) -> list[dict[str, Any]]:
        """Fetch the raw project board items via ``gh project item-list``."""
        owner = self._repo_slug.split("/", 1)[0]
        raw = self._run(
            [
                "project",
                "item-list",
                str(self._project_number),
                "--owner",
                owner,
                "--format",
                "json",
                "--limit",
                "1000",
            ]
        )
        return _decode_items(raw)

    def _open_issues(self) -> tuple[set[int], dict[int, frozenset[int]]]:
        """Fetch open issues and their dependencies via the GraphQL API."""
        owner, name = self._repo_slug.split("/", 1)
        raw = self._run(
            [
                "api",
                "graphql",
                "-f",
                f"query={_OPEN_ISSUES_QUERY}",
                "-f",
                f"owner={owner}",
                "-f",
                f"name={name}",
            ]
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as err:
            raise GitHubError(f"Invalid GraphQL response: {raw!r}") from err
        return _parse_open_issues(payload)
