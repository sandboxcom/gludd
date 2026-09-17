"""Core schema contracts for persisted self-improvement artifacts."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from general_ludd.schemas.self_improve_artifact import (
    MANAGED_SELF_IMPROVE_APPROVAL_POLICY,
    LegacySelfImproveArtifactKind,
    classify_legacy_self_improve_artifact,
    self_improve_artifact_digest,
)

_LEGACY_CONFIG = (
    '{"capability_required":"config_write","change_content":"x",'
    '"kind":"config","reason":"approved","target_paths":["a.yml"]}'
)
_LEGACY_NON_CONFIG = (
    '{"description":"d","kind":"code","project_id":"p",'
    '"schema_version":1,"title":"t","worktree_path":"/tmp/w"}'
)


def test_managed_approval_policy_is_canonical() -> None:
    assert MANAGED_SELF_IMPROVE_APPROVAL_POLICY == "managed_self_improve_plan"


@pytest.mark.parametrize("kind", ["config", "yaml"])
def test_classifies_exact_legacy_config_schema(kind: str) -> None:
    raw = _LEGACY_CONFIG.replace('"config"', f'"{kind}"')
    assert (
        classify_legacy_self_improve_artifact(raw)
        is LegacySelfImproveArtifactKind.CONFIG
    )


def test_classifies_exact_legacy_non_config_schema() -> None:
    assert (
        classify_legacy_self_improve_artifact(_LEGACY_NON_CONFIG)
        is LegacySelfImproveArtifactKind.NON_CONFIG
    )


@pytest.mark.parametrize(
    "raw",
    [None, 3, "", "{malformed", "[]", '{"kind":"code"}', '{"kind":"config"}'],
)
def test_unknown_or_malformed_legacy_artifact_fails_closed(raw: object) -> None:
    assert (
        classify_legacy_self_improve_artifact(raw)
        is LegacySelfImproveArtifactKind.UNKNOWN
    )


def test_artifact_digest_is_exact_sha256() -> None:
    assert self_improve_artifact_digest(_LEGACY_CONFIG) == hashlib.sha256(
        _LEGACY_CONFIG.encode("utf-8")
    ).hexdigest()


@pytest.mark.parametrize(
    "raw",
    [None, "", "x" * 1_048_577],
    ids=["non-text", "empty", "oversized"],
)
def test_artifact_digest_rejects_unbounded_or_non_text_input(raw: object) -> None:
    with pytest.raises(ValueError, match="bounded text"):
        self_improve_artifact_digest(raw)


def test_core_artifact_schema_is_in_canonical_self_improve_coverage() -> None:
    coverage_config = Path("config/coverage_self_improve.ini").read_text(
        encoding="utf-8"
    )
    assert "*/src/general_ludd/schemas/self_improve_artifact.py" in coverage_config
