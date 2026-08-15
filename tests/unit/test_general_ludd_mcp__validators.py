"""Tests for the reusable MCP Pydantic constraints in mcp/_validators.py."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from general_ludd.mcp._validators import TrimmedNonEmptyStr


class NamedThing(BaseModel):
    name: TrimmedNonEmptyStr


class TestTrimmedNonEmptyStr:
    def test_valid_non_empty_string_passes(self) -> None:
        thing = NamedThing(name="widget")

        assert thing.name == "widget"

    def test_leading_whitespace_is_stripped(self) -> None:
        thing = NamedThing(name="   widget")

        assert thing.name == "widget"

    def test_trailing_whitespace_is_stripped(self) -> None:
        thing = NamedThing(name="widget   ")

        assert thing.name == "widget"

    def test_internal_whitespace_is_preserved(self) -> None:
        thing = NamedThing(name="left   right")

        assert thing.name == "left   right"

    def test_empty_string_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NamedThing(name="")

    def test_whitespace_only_string_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NamedThing(name="    ")

    def test_non_string_input_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            NamedThing(name=42)

    def test_stripped_value_round_trips_on_the_model(self) -> None:
        thing = NamedThing(name="  gizmo  ")

        assert thing.model_dump() == {"name": "gizmo"}
        assert NamedThing.model_validate(thing.model_dump()).name == "gizmo"
