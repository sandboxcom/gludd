"""Portable fixed-property schemas for managed remote proposal workers."""

from __future__ import annotations

import copy
import json

from general_ludd.self_improve.codex_comparison import (
    COMPACT_PROPOSAL_PROTOCOL_V4,
    proposal_batch_json_schema,
    proposal_batch_response_instruction,
)

MANAGED_PROPOSAL_BATCH_PROTOCOL = "self-improve-managed-proposal-batch-v1"
MANAGED_PROPOSAL_RESPONSE_INSTRUCTION = (
    proposal_batch_response_instruction(COMPACT_PROPOSAL_PROTOCOL_V4)
    + "The proposals member is an object keyed by decimal prompt ordinal, starting "
    "at 0; emit every key exactly once and bind each value to that prompt's path."
)


def canonical_schema_json(schema: dict[str, object]) -> str:
    """Serialize one response schema into stable provider-neutral JSON."""
    return json.dumps(
        schema,
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )


def managed_response_instruction(proposal_protocol: str) -> str:
    """Describe the portable fixed-property response shape to every model."""
    return (
        proposal_batch_response_instruction(proposal_protocol)
        + "The proposals member is an object keyed by decimal prompt ordinal, "
        "starting at 0; emit every key exactly once and bind each value to that "
        "prompt's path."
    )


def managed_response_schema(
    *,
    proposal_protocol: str,
    protocol_digest: str,
    expected_count: int,
    focus_paths: tuple[str, ...] = (),
    editable_ranges: tuple[tuple[tuple[int, int], ...], ...] = (),
) -> dict[str, object]:
    """Use fixed object properties instead of non-portable array uniqueness."""
    schema = proposal_batch_json_schema(
        proposal_protocol=proposal_protocol,
        protocol_digest=protocol_digest,
        expected_count=expected_count,
        focus_paths=focus_paths,
        editable_ranges=editable_ranges,
    )
    properties = schema["properties"]
    if not isinstance(properties, dict):
        raise ValueError("proposal schema root properties are invalid")
    protocol = properties["protocol"]
    proposals = properties["proposals"]
    if not isinstance(protocol, dict) or not isinstance(proposals, dict):
        raise ValueError("proposal schema transport properties are invalid")
    item_schema = proposals.get("items")
    if not isinstance(item_schema, dict):
        raise ValueError("proposal schema item is invalid")
    ordinal_properties: dict[str, object] = {}
    ordinal_keys = tuple(str(ordinal) for ordinal in range(expected_count))
    for ordinal, key in enumerate(ordinal_keys):
        item = copy.deepcopy(item_schema)
        if focus_paths:
            item_properties = item.get("properties")
            if not isinstance(item_properties, dict):
                raise ValueError("compact proposal schema properties are invalid")
            item_properties["focus_path"] = {
                "const": focus_paths[ordinal],
                "type": "string",
            }
        ordinal_properties[key] = item
    protocol["const"] = MANAGED_PROPOSAL_BATCH_PROTOCOL
    properties["proposals"] = {
        "type": "object",
        "additionalProperties": False,
        "required": list(ordinal_keys),
        "properties": ordinal_properties,
    }
    return schema


__all__ = [
    "MANAGED_PROPOSAL_BATCH_PROTOCOL",
    "MANAGED_PROPOSAL_RESPONSE_INSTRUCTION",
    "canonical_schema_json",
    "managed_response_instruction",
    "managed_response_schema",
]
