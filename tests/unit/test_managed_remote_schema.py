"""Regression tests for the extracted managed remote schema boundary."""

from __future__ import annotations

import pytest

from general_ludd.self_improve import managed_remote_schema
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
