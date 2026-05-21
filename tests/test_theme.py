"""Tests for Maestro colour themes."""

from __future__ import annotations

import pytest

from maestro.theme import DEFAULT_THEME, ThemeError, available_themes, load_theme


def test_default_theme_is_available():
    assert DEFAULT_THEME in available_themes()


def test_load_theme_returns_theme_with_semantic_styles():
    theme = load_theme("nord")
    for style in ("running", "done", "blocked", "failed", "queued"):
        assert style in theme.styles


def test_all_themes_define_the_same_style_names():
    style_sets = [set(load_theme(name).styles) for name in available_themes()]
    assert all(names == style_sets[0] for names in style_sets)


def test_unknown_theme_raises():
    with pytest.raises(ThemeError, match="Unknown theme"):
        load_theme("solarized-banana")
