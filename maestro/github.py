"""GitHub Projects V2 trigger source.

:class:`StatusFieldTrigger` polls a GitHub Projects V2 board and reports it as a
:class:`~maestro.core.models.BoardState`. Each poll makes two read-only GraphQL
calls through the ``gh`` CLI:

* a tailored project-board query — every board item with its issue number,
  title, Status-field column, and Priority-field value.
* an open-issues query — every open issue in the repository with its native
  "Blocked by" dependencies, yielding the open-issue set and the dependency map.

Both queries are hand-written to request only the fields Maestro needs, which
keeps their GraphQL rate-limit cost low — a single poll spends roughly two
points (see the ``rateLimit`` block returned by the board query). Maestro
stores no credentials; ``gh`` supplies authentication.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Callable
from typing import Any

from maestro.core.models import BoardState, Issue

logger = logging.getLogger(__name__)

# Runs a gh command (the args after "gh") and returns its stdout. Injectable so
# tests can supply canned responses; the default really runs gh.
GhRunner = Callable[[list[str]], str]

# The project board, its items, and the Priority field's option list.
# `repositoryOwner` + the `ProjectV2Owner` inline fragment resolves whether the
# owner is a user or an organisation. `fieldValueByName` reads a single-select
# field directly, avoiding the costly `fieldValues` connection. The
# `priorityField` block fetches the Priority field's option list in declared
# order — that order *is* the scheduling rank (index 0 dispatches first), so
# the option names themselves carry no meaning. NOTE: `items(first: 100)` does
# not paginate; a board with more than 100 items would need cursor pagination.
_BOARD_QUERY = """
query($login: String!, $number: Int!) {
  repositoryOwner(login: $login) {
    ... on ProjectV2Owner {
      projectV2(number: $number) {
        priorityField: field(name: "Priority") {
          ... on ProjectV2SingleSelectField { options { name } }
        }
        items(first: 100) {
          nodes {
            content {
              __typename
              ... on Issue { number title }
            }
            status: fieldValueByName(name: "Status") {
              ... on ProjectV2ItemFieldSingleSelectValue { name }
            }
            priority: fieldValueByName(name: "Priority") {
              ... on ProjectV2ItemFieldSingleSelectValue { name }
            }
          }
        }
      }
    }
  }
  rateLimit { cost remaining }
}
"""

# Just the Status field's option list, fetched on its own when `maestro doctor`
# wants to verify the field is configured without paying for a full board read.
_STATUS_FIELD_QUERY = """
query($login: String!, $number: Int!) {
  repositoryOwner(login: $login) {
    ... on ProjectV2Owner {
      projectV2(number: $number) {
        statusField: field(name: "Status") {
          ... on ProjectV2SingleSelectField { options { name } }
        }
      }
    }
  }
}
"""

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


def _parse_priority_rank_map(payload: dict[str, Any]) -> dict[str, int]:
    """Extract the Priority field's option order as a ``{name: index}`` map.

    The map's values are the option positions in the field's defined option
    list: index 0 is the highest priority. A missing Priority field, or a
    payload whose ``priorityField`` block is absent or unshaped, yields an
    empty map — every issue then has ``priority`` ``None``, and the scheduler
    sorts them last.

    Args:
        payload: The decoded JSON from the project-board query.

    Returns:
        A ``{option-name: index}`` map, possibly empty.
    """
    try:
        owner = payload["data"]["repositoryOwner"]
        field = owner["projectV2"].get("priorityField") if isinstance(owner, dict) else None
    except (KeyError, TypeError, AttributeError):
        return {}
    if not isinstance(field, dict):
        return {}
    options = field.get("options")
    if not isinstance(options, list):
        return {}
    rank: dict[str, int] = {}
    for index, option in enumerate(options):
        if isinstance(option, dict):
            name = option.get("name")
            if isinstance(name, str):
                rank[name] = index
    return rank


def _parse_priority(value: object, rank: dict[str, int]) -> int | None:
    """Look up a selected Priority option's rank in ``rank``.

    Args:
        value: The selected option name from a ``priority`` field-value node,
            or ``None`` when the field is unset on the card.
        rank: The ``{option-name: index}`` map built by
            :func:`_parse_priority_rank_map`.

    Returns:
        The option's index in the field's defined option list (0 dispatches
        first), or ``None`` when the field is unset, the selected option is
        absent from the rank map, or the Priority field itself is missing
        (an empty ``rank``).
    """
    if not isinstance(value, str):
        return None
    return rank.get(value)


def _parse_status_field_options(payload: dict[str, Any]) -> list[str] | None:
    """Extract the Status field's option names from the standalone query.

    Args:
        payload: The decoded JSON from :data:`_STATUS_FIELD_QUERY`.

    Returns:
        The ordered option names, or ``None`` when the Status field does not
        exist on the project board.
    """
    try:
        owner = payload["data"]["repositoryOwner"]
        field = owner["projectV2"].get("statusField") if isinstance(owner, dict) else None
    except (KeyError, TypeError, AttributeError):
        return None
    if not isinstance(field, dict):
        return None
    options = field.get("options")
    if not isinstance(options, list):
        return None
    return [option["name"] for option in options if isinstance(option, dict) and "name" in option]


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


def _parse_board(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract the board's item nodes from the project-board GraphQL payload.

    Args:
        payload: The decoded JSON from the project-board query.

    Returns:
        The raw item nodes, each carrying its content and field values.

    Raises:
        GitHubError: If the payload is not shaped as expected — for example
            when the owner login or project number does not resolve.
    """
    try:
        owner = payload["data"]["repositoryOwner"]
        nodes = owner["projectV2"]["items"]["nodes"]
    except (KeyError, TypeError) as err:
        raise GitHubError(f"Unexpected project board response: {payload!r}") from err
    if not isinstance(nodes, list):
        raise GitHubError(f"Unexpected project board response: {payload!r}")
    return nodes


def _log_rate_limit(payload: dict[str, Any]) -> None:
    """Log the GraphQL cost and remaining budget reported by a board poll.

    Best-effort: a payload without a well-formed ``rateLimit`` block is simply
    not logged, so instrumentation never breaks a poll.
    """
    data = payload.get("data")
    rate = data.get("rateLimit") if isinstance(data, dict) else None
    if isinstance(rate, dict) and "cost" in rate and "remaining" in rate:
        logger.info(
            "Board poll cost %s GraphQL point(s); %s remaining this hour",
            rate["cost"],
            rate["remaining"],
        )


def _build_issues(
    nodes: list[dict[str, Any]],
    trigger_column: str,
    blocked_by: dict[int, frozenset[int]],
    priority_rank: dict[str, int],
) -> list[Issue]:
    """Build :class:`Issue` records from raw project-board item nodes.

    Only items whose content is a GitHub issue are kept; pull requests, draft
    items, and items with redacted content are skipped.

    Args:
        nodes: Item nodes from the project-board query.
        trigger_column: Status-field value that marks an issue for dispatch.
        blocked_by: Map from an issue number to the issues it is blocked by.
        priority_rank: Ordered Priority option names mapped to their index in
            the field's defined option list — built by
            :func:`_parse_priority_rank_map`. An empty map (missing Priority
            field) yields ``None`` for every issue's priority.

    Returns:
        One :class:`Issue` per board item whose content is a GitHub issue.
    """
    issues: list[Issue] = []
    for node in nodes:
        content = node.get("content") or {}
        if content.get("__typename") != "Issue":
            continue
        number = content["number"]
        status = node.get("status") or {}
        priority = node.get("priority") or {}
        status_name = status.get("name") if isinstance(status.get("name"), str) else None
        issues.append(
            Issue(
                number=number,
                title=content.get("title", ""),
                priority=_parse_priority(priority.get("name"), priority_rank),
                blocked_by=blocked_by.get(number, frozenset()),
                in_trigger_column=status_name == trigger_column,
                status_column=status_name,
            )
        )
    return issues


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
        nodes, priority_rank = self._board_items()
        open_numbers, blocked_by = self._open_issues()
        issues = _build_issues(nodes, self._trigger_column, blocked_by, priority_rank)
        return BoardState(issues=issues, open_numbers=open_numbers)

    def status_field_options(self) -> list[str] | None:
        """Return the Status field's option names, or ``None`` if the field is absent.

        Used by ``maestro doctor`` to verify the board's Status field has the
        column names the configuration refers to. Read-only and mutates nothing.

        Raises:
            GitHubError: If the gh call fails or returns invalid JSON.
        """
        owner = self._repo_slug.split("/", 1)[0]
        raw = self._run(
            [
                "api",
                "graphql",
                "-f",
                f"query={_STATUS_FIELD_QUERY}",
                "-f",
                f"login={owner}",
                "-F",
                f"number={self._project_number}",
            ]
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as err:
            raise GitHubError(f"Invalid Status field response: {raw!r}") from err
        return _parse_status_field_options(payload)

    def _board_items(self) -> tuple[list[dict[str, Any]], dict[str, int]]:
        """Fetch the project board's item nodes and Priority rank map."""
        owner = self._repo_slug.split("/", 1)[0]
        raw = self._run(
            [
                "api",
                "graphql",
                "-f",
                f"query={_BOARD_QUERY}",
                "-f",
                f"login={owner}",
                "-F",
                f"number={self._project_number}",
            ]
        )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as err:
            raise GitHubError(f"Invalid project board response: {raw!r}") from err
        _log_rate_limit(payload)
        return _parse_board(payload), _parse_priority_rank_map(payload)

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
