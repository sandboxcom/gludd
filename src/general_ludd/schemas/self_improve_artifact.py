"""Core persisted-artifact contracts shared with self-improvement workflows."""

from __future__ import annotations

import hashlib
import json
from enum import StrEnum
from typing import Final, cast

MANAGED_SELF_IMPROVE_APPROVAL_POLICY: Final = "managed_self_improve_plan"

_MAX_APPROVAL_ARTIFACT_BYTES: Final = 1_048_576
_LEGACY_CONFIG_FIELDS: Final = frozenset(
    {"capability_required", "change_content", "kind", "reason", "target_paths"}
)
_LEGACY_NON_CONFIG_FIELDS: Final = frozenset(
    {
        "description",
        "kind",
        "project_id",
        "schema_version",
        "title",
        "worktree_path",
    }
)


class LegacySelfImproveArtifactKind(StrEnum):
    """Non-executable discriminator for artifacts predating managed plans."""

    CONFIG = "legacy_config"
    NON_CONFIG = "legacy_non_config"
    UNKNOWN = "legacy_unknown"


def self_improve_artifact_digest(raw: object) -> str:
    """Return the SHA-256 binding for one bounded persisted approval artifact."""
    if (
        not isinstance(raw, str)
        or not raw
        or len(raw.encode("utf-8")) > _MAX_APPROVAL_ARTIFACT_BYTES
    ):
        raise ValueError("self-improve approval artifact must be bounded text")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def classify_legacy_self_improve_artifact(
    raw: object,
) -> LegacySelfImproveArtifactKind:
    """Classify only exact legacy schemas without granting execution authority."""
    if not isinstance(raw, str):
        return LegacySelfImproveArtifactKind.UNKNOWN
    try:
        loaded = json.loads(raw)
    except json.JSONDecodeError:
        return LegacySelfImproveArtifactKind.UNKNOWN
    if not isinstance(loaded, dict):
        return LegacySelfImproveArtifactKind.UNKNOWN
    value = cast(dict[str, object], loaded)
    fields = frozenset(value)
    if fields == _LEGACY_CONFIG_FIELDS and (
        value["kind"] in {"config", "yaml"}
        and all(
            isinstance(value[field], str)
            for field in ("capability_required", "change_content", "kind", "reason")
        )
        and isinstance(value["target_paths"], list)
        and all(isinstance(path, str) for path in value["target_paths"])
    ):
        return LegacySelfImproveArtifactKind.CONFIG
    if fields == _LEGACY_NON_CONFIG_FIELDS and (
        type(value["schema_version"]) is int
        and value["schema_version"] == 1
        and all(
            isinstance(value[field], str)
            for field in _LEGACY_NON_CONFIG_FIELDS - {"schema_version"}
        )
        and bool(value["project_id"])
        and bool(value["title"])
        and value["kind"] not in {"config", "yaml"}
    ):
        return LegacySelfImproveArtifactKind.NON_CONFIG
    return LegacySelfImproveArtifactKind.UNKNOWN


__all__ = [
    "MANAGED_SELF_IMPROVE_APPROVAL_POLICY",
    "LegacySelfImproveArtifactKind",
    "classify_legacy_self_improve_artifact",
    "self_improve_artifact_digest",
]
