"""Regression tests for typed ServiceNow diagnostic string wrappers."""

from __future__ import annotations

from typing import get_type_hints

from general_ludd.connectors.servicenow import _DisplayName, _DisplayText


def test_display_name_declares_typed_display_storage() -> None:
    """The diagnostic display field must be explicitly typed for mypy."""
    assert get_type_hints(_DisplayName)["_display"] is str


def test_display_text_declares_typed_display_storage() -> None:
    """The diagnostic display field must be explicitly typed for mypy."""
    assert get_type_hints(_DisplayText)["_display"] is str


def test_display_wrappers_preserve_canonical_and_diagnostic_values() -> None:
    name = _DisplayName("servicenow", "servicenow:dev12345")
    text = _DisplayText("short description", "INC001: short description")

    assert isinstance(name, str)
    assert name == "servicenow"
    assert str(name) == "servicenow:dev12345"
    assert isinstance(text, str)
    assert text == "short description"
    assert str(text) == "INC001: short description"
