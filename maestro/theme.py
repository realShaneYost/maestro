"""Colour themes for Maestro's terminal output.

A theme maps Maestro's stable style names — the parts of a worker-log line —
to concrete colours. Rendering code refers only to the names, so adding a
theme never touches rendering code. Nord is the default.
"""

from __future__ import annotations

from rich.theme import Theme

# Every theme maps the same style names to colours. Keep the key set identical
# across themes so any theme is a drop-in replacement for any other.
_THEMES: dict[str, dict[str, str]] = {
    "nord": {
        "issue": "#88c0d0",
        "timestamp": "#81a1c1",
        "message": "#e5e9f0",
        "error": "bold #bf616a",
        "header": "bold #eceff4",
    },
    "dark": {
        "issue": "cyan",
        "timestamp": "blue",
        "message": "white",
        "error": "bold red",
        "header": "bold white",
    },
    "light": {
        "issue": "blue",
        "timestamp": "magenta",
        "message": "black",
        "error": "bold red",
        "header": "bold black",
    },
}

DEFAULT_THEME = "nord"


class ThemeError(ValueError):
    """Raised when an unknown theme name is requested."""


def available_themes() -> list[str]:
    """Return the sorted names of all built-in themes."""
    return sorted(_THEMES)


def load_theme(name: str) -> Theme:
    """Return the rich :class:`~rich.theme.Theme` for ``name``.

    Args:
        name: A built-in theme name (see :func:`available_themes`).

    Returns:
        A rich Theme mapping Maestro's style names to colours.

    Raises:
        ThemeError: If ``name`` is not a built-in theme.
    """
    if name not in _THEMES:
        raise ThemeError(f"Unknown theme {name!r}. Available: {', '.join(available_themes())}")
    return Theme(_THEMES[name])
