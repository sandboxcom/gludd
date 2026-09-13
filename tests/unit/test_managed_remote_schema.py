"""Regression tests for the extracted managed remote schema boundary."""

from __future__ import annotations

from typing import cast

import pytest

from general_ludd.self_improve import managed_remote_schema
from general_ludd.self_improve.codex_comparison import COMPACT_PROPOSAL_PROTOCOL_V4
from general_ludd.self_improve.managed_remote_codec import (
    MANAGED_PROPOSAL_BATCH_PROTOCOL,
    _managed_response_schema,
)


def test_remote_codec_reexports_the_canonical_schema_contract() -> None:
    """Remote workers and existing callers share one schema implementation."""
    assert _managed_response_schema is managed_remote_schema.managed_response_schema
    assert (
        MANAGED_PROPOSAL_BATCH_PROTOCOL
        == managed_remote_schema.MANAGED_PROPOSAL_BATCH_PROTOCOL
    )


def test_compact_schema_confines_coordinates_to_each_exact_ordinal() -> None:
    """One shard cannot select coordinates exposed only by another shard."""
    schema = managed_remote_schema.managed_response_schema(
        proposal_protocol=COMPACT_PROPOSAL_PROTOCOL_V4,
        protocol_digest="0" * 64,
        expected_count=2,
        focus_paths=("src/one.py", "tests/unit/test_one.py"),
        editable_ranges=(((1, 3),), ((20, 22), (40, 41))),
    )

    root = cast(dict[str, object], schema["properties"])
    proposals = cast(dict[str, object], root["proposals"])
    ordinal_schemas = cast(dict[str, object], proposals["properties"])
    first = cast(dict[str, object], ordinal_schemas["0"])
    second = cast(dict[str, object], ordinal_schemas["1"])
    first_properties = cast(dict[str, object], first["properties"])
    second_properties = cast(dict[str, object], second["properties"])
    first_edits = cast(dict[str, object], first_properties["e"])
    second_edits = cast(dict[str, object], second_properties["e"])
    first_edit = cast(dict[str, object], first_edits["items"])
    second_edit = cast(dict[str, object], second_edits["items"])
    first_coordinates = cast(dict[str, object], first_edit["properties"])
    second_coordinates = cast(dict[str, object], second_edit["properties"])

    assert first_coordinates["s"] == {"type": "integer", "enum": [1, 2, 3]}
    assert second_coordinates["s"] == {
        "type": "integer",
        "enum": [20, 21, 22, 40, 41],
    }
    assert first_coordinates["n"] == {"type": "integer", "minimum": 0, "maximum": 2}
    assert second_coordinates["n"] == {"type": "integer", "minimum": 0, "maximum": 2}


@pytest.mark.parametrize(
    ("schema", "focus_paths", "message"),
    [
        ({"properties": []}, (), "root properties"),
        (
            {"properties": {"protocol": [], "proposals": {}}},
            (),
            "transport properties",
        ),
        (
            {"properties": {"protocol": {}, "proposals": {}}},
            (),
            "item is invalid",
        ),
        (
            {
                "properties": {
                    "protocol": {},
                    "proposals": {"items": {"properties": []}},
                }
            },
            ("src/example.py",),
            "compact proposal schema properties",
        ),
    ],
)
def test_managed_schema_rejects_invalid_upstream_transport_shapes(
    monkeypatch: pytest.MonkeyPatch,
    schema: dict[str, object],
    focus_paths: tuple[str, ...],
    message: str,
) -> None:
    """Provider schema drift fails closed at each structural boundary."""
    def factory(**_kwargs: object) -> dict[str, object]:
        return schema

    monkeypatch.setattr(managed_remote_schema, "proposal_batch_json_schema", factory)
    with pytest.raises(ValueError, match=message):
        managed_remote_schema.managed_response_schema(
            proposal_protocol="self-improve-compact-v4",
            protocol_digest="0" * 64,
            expected_count=1,
            focus_paths=focus_paths,
        )
