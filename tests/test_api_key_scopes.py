"""Unit tests for API key scope parsing (no Flask)."""

from __future__ import annotations

from services.api_key_scopes import parse_scopes, scope_allowed


def test_parse_scopes_default_for_empty() -> None:
    assert parse_scopes(None) == {"read:me", "read:inventory"}
    assert parse_scopes("") == {"read:me", "read:inventory"}
    assert parse_scopes("  ") == {"read:me", "read:inventory"}


def test_parse_scopes_normalizes_case_and_whitespace() -> None:
    assert parse_scopes(" Read:Me , READ:inventory ") == {"read:me", "read:inventory"}


def test_scope_allowed_star() -> None:
    assert scope_allowed({"*"}, "read:inventory") is True
    assert scope_allowed({"all"}, "read:me") is True


def test_scope_allowed_explicit() -> None:
    assert scope_allowed({"read:me"}, "read:me") is True
    assert scope_allowed({"read:me"}, "read:inventory") is False
