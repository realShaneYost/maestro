"""Colour themes for Maestro's terminal output.

A theme maps Maestro's stable semantic style names — worker states and log
levels — to concrete colours. Rendering code only ever refers to the semantic
names, so adding a theme never touches rendering code. Nord is the default.
"""

from __future__ import annotations

from rich.theme import Theme

# Every theme maps the same semantic style names to colours. Keep the key set
# identical across themes so any theme is a drop-in replacement for any other.
_THEMES: dict[str, dict[str, str]] = {
    "nord": {
        "running": "#88c0d0",
        "done": "#a3be8c",
        "blocked": "#ebcb8b",
        "failed": "#bf616a",
        "queued": "#4c566a",
        "header": "bold #eceff4",
        "debug": "#4c566a",
        "info": "#d8dee9",
        "warning": "#ebcb8b",
        "error": "bold #bf616a",
        "critical": "bold #bf616a",
    },
    "dark": {
        "running": "cyan",
        "done": "green",
        "blocked": "yellow",
        "failed": "red",
        "queued": "bright_black",
        "header": "bold white",
        "debug": "bright_black",
        "info": "white",
        "warning": "yellow",
        "error": "bold red",
        "critical": "bold red",
    },
    "light": {
        "running": "blue",
        "done": "green",
        "blocked": "dark_orange",
        "failed": "red",
        "queued": "grey50",
        "header": "bold black",
        "debug": "grey50",
        "info": "black",
        "warning": "dark_orange",
        "error": "bold red",
        "critical": "bold red",
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
        A rich Theme mapping Maestro's semantic style names to colours.

    Raises:
        ThemeError: If ``name`` is not a built-in theme.
    """
    if name not in _THEMES:
        raise ThemeError(f"Unknown theme {name!r}. Available: {', '.join(available_themes())}")
    return Theme(_THEMES[name])
