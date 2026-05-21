"""The trigger-source interface.

A trigger source answers one question — what is on the board right now? — and
returns it as a :class:`~maestro.core.models.BoardState`. Maestro v1 ships one
implementation that polls a GitHub Projects V2 board; this protocol keeps that
choice swappable.
"""

from __future__ import annotations

from typing import Protocol

from maestro.core.models import BoardState


class TriggerSource(Protocol):
    """Something that can report the current state of the project board."""

    def poll(self) -> BoardState:
        """Return the board's current state."""
