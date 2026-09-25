"""Rollback workflow boundary for FreeLLMAPI admitted artifacts.

The workflow tracks blue/green generations, performs atomic switch-back, and
enforces protected-artifact rules so that a rollback cannot displace an
artifact that the current runtime depends on.
"""

from __future__ import annotations

import hashlib
import json

import pytest

from general_ludd.models.freellmapi_provenance_bundle import (
    FREELLMAPI_PROVENANCE_BUNDLE_SCHEMA_VERSION,
    FreeLLMAPIProvenanceBundle,
    validate_provenance_bundle,
)
from general_ludd.models.freellmapi_rollback_workflow import (
    FREELLMAPI_ROLLBACK_SCHEMA_VERSION,
    FreeLLMAPIRollbackError,
    FreeLLMAPIRollbackFault,
    FreeLLMAPIRollbackGeneration,
    FreeLLMAPIRollbackState,
    activate_generation,
    create_rollback_state,
    is_rollback_safe,
    protect_artifact,
    rollback,
    stage_artifact,
)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode("ascii")


def _bundle(evidence_suffix: str, abi_version: int = 1) -> FreeLLMAPIProvenanceBundle:
    bundle = f"console.log('{evidence_suffix}')".encode()
    manifest = {
        "schema_version": FREELLMAPI_PROVENANCE_BUNDLE_SCHEMA_VERSION,
        "abi_version": abi_version,
        "bundle_sha256": _digest(bundle),
        "upstream_commit": "780a7d8d6dcbc818eb10ec17da210635b569ae22",
        "source": "server/src/services/scoring.ts",
        "upstream": "https://github.com/tashfeenahmed/freellmapi",
        "license": "MIT",
        "exports": ["expectedReliability"],
    }
    source_map = b'{"version":3}'
    license_bundle = b"MIT License\nCopyright (c) 2026"
    sbom = _canonical_bytes({"components": []})
    build_evidence = _canonical_bytes({"gate": "freellmapi-upstream-build-v1", "decision": "upstream_build_verified"})
    provenance = _canonical_bytes({"attestation": "keyless-ci"})
    raw = {
        "bundle": bundle,
        "manifest": _canonical_bytes(manifest),
        "source_map": source_map,
        "license_bundle": license_bundle,
        "sbom": sbom,
        "build_evidence": build_evidence,
        "provenance_attestation": provenance,
    }
    return validate_provenance_bundle(raw)


def test_api_surface_is_minimal_and_public() -> None:
    import general_ludd.models.freellmapi_rollback_workflow as module

    assert sorted(module.__all__) == sorted(
        [
            "FREELLMAPI_ROLLBACK_SCHEMA_VERSION",
            "FreeLLMAPIRollbackError",
            "FreeLLMAPIRollbackFault",
            "FreeLLMAPIRollbackGeneration",
            "FreeLLMAPIRollbackState",
            "activate_generation",
            "create_rollback_state",
            "is_rollback_safe",
            "protect_artifact",
            "rollback",
            "stage_artifact",
        ]
    )


def test_schema_version_is_positive_integer() -> None:
    assert isinstance(FREELLMAPI_ROLLBACK_SCHEMA_VERSION, int)
    assert FREELLMAPI_ROLLBACK_SCHEMA_VERSION >= 1


def test_generations_are_distinct() -> None:
    assert FreeLLMAPIRollbackGeneration.BLUE.value == "blue"
    assert FreeLLMAPIRollbackGeneration.GREEN.value == "green"
    assert len(set(FreeLLMAPIRollbackGeneration)) == 2


def test_create_state_has_no_active_generation() -> None:
    state = create_rollback_state()

    assert isinstance(state, FreeLLMAPIRollbackState)
    assert state.active_generation is None
    assert state.blue_evidence_id is None
    assert state.green_evidence_id is None
    assert state.previous_evidence_id is None
    assert state.protected_evidence_ids == frozenset()


def test_stage_artifact_records_evidence_id_per_generation() -> None:
    state = create_rollback_state()
    blue_bundle = _bundle("blue")
    green_bundle = _bundle("green")

    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.BLUE, blue_bundle)
    assert state.blue_evidence_id == blue_bundle.evidence_id
    assert state.green_evidence_id is None

    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.GREEN, green_bundle)
    assert state.green_evidence_id == green_bundle.evidence_id
    assert state.previous_evidence_id is None


def test_activate_generation_sets_active_and_preserves_previous() -> None:
    state = create_rollback_state()
    blue_bundle = _bundle("blue")
    green_bundle = _bundle("green")

    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.BLUE, blue_bundle)
    state = activate_generation(state, FreeLLMAPIRollbackGeneration.BLUE)
    assert state.active_generation == FreeLLMAPIRollbackGeneration.BLUE
    assert state.previous_evidence_id is None

    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.GREEN, green_bundle)
    state = activate_generation(state, FreeLLMAPIRollbackGeneration.GREEN)
    assert state.active_generation == FreeLLMAPIRollbackGeneration.GREEN
    assert state.previous_evidence_id == blue_bundle.evidence_id


def test_activate_missing_generation_fails_closed() -> None:
    state = create_rollback_state()
    with pytest.raises(FreeLLMAPIRollbackError) as exc:
        activate_generation(state, FreeLLMAPIRollbackGeneration.BLUE)
    assert exc.value.fault == FreeLLMAPIRollbackFault.MISSING_GENERATION


def test_activate_same_generation_is_idempotent() -> None:
    state = create_rollback_state()
    blue_bundle = _bundle("blue")
    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.BLUE, blue_bundle)
    state = activate_generation(state, FreeLLMAPIRollbackGeneration.BLUE)
    state = activate_generation(state, FreeLLMAPIRollbackGeneration.BLUE)
    assert state.active_generation == FreeLLMAPIRollbackGeneration.BLUE
    assert state.previous_evidence_id is None


def test_rollback_switches_to_previous_generation() -> None:
    state = create_rollback_state()
    blue_bundle = _bundle("blue")
    green_bundle = _bundle("green")

    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.BLUE, blue_bundle)
    state = activate_generation(state, FreeLLMAPIRollbackGeneration.BLUE)
    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.GREEN, green_bundle)
    state = activate_generation(state, FreeLLMAPIRollbackGeneration.GREEN)

    state = rollback(state)
    assert state.active_generation == FreeLLMAPIRollbackGeneration.BLUE
    assert state.previous_evidence_id == green_bundle.evidence_id


def test_rollback_without_prior_generation_fails_closed() -> None:
    state = create_rollback_state()
    blue_bundle = _bundle("blue")
    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.BLUE, blue_bundle)
    state = activate_generation(state, FreeLLMAPIRollbackGeneration.BLUE)

    with pytest.raises(FreeLLMAPIRollbackError) as exc:
        rollback(state)
    assert exc.value.fault == FreeLLMAPIRollbackFault.NO_PRIOR_GENERATION


def test_rollback_without_active_generation_fails_closed() -> None:
    state = create_rollback_state()
    with pytest.raises(FreeLLMAPIRollbackError) as exc:
        rollback(state)
    assert exc.value.fault == FreeLLMAPIRollbackFault.NO_PRIOR_GENERATION


def test_protected_artifact_blocks_switch_away() -> None:
    state = create_rollback_state()
    blue_bundle = _bundle("blue")
    green_bundle = _bundle("green")

    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.BLUE, blue_bundle)
    state = activate_generation(state, FreeLLMAPIRollbackGeneration.BLUE)
    state = protect_artifact(state, blue_bundle.evidence_id)

    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.GREEN, green_bundle)
    assert is_rollback_safe(state, FreeLLMAPIRollbackGeneration.GREEN) is False

    with pytest.raises(FreeLLMAPIRollbackError) as exc:
        activate_generation(state, FreeLLMAPIRollbackGeneration.GREEN)
    assert exc.value.fault == FreeLLMAPIRollbackFault.PROTECTED_ARTIFACT


def test_protected_artifact_allows_reactivation_of_same_generation() -> None:
    state = create_rollback_state()
    blue_bundle = _bundle("blue")
    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.BLUE, blue_bundle)
    state = activate_generation(state, FreeLLMAPIRollbackGeneration.BLUE)
    state = protect_artifact(state, blue_bundle.evidence_id)

    assert is_rollback_safe(state, FreeLLMAPIRollbackGeneration.BLUE) is True
    state = activate_generation(state, FreeLLMAPIRollbackGeneration.BLUE)
    assert state.active_generation == FreeLLMAPIRollbackGeneration.BLUE


def test_unprotected_switch_is_safe() -> None:
    state = create_rollback_state()
    blue_bundle = _bundle("blue")
    green_bundle = _bundle("green")

    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.BLUE, blue_bundle)
    state = activate_generation(state, FreeLLMAPIRollbackGeneration.BLUE)
    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.GREEN, green_bundle)

    assert is_rollback_safe(state, FreeLLMAPIRollbackGeneration.GREEN) is True


def test_protect_unknown_evidence_id_fails_closed() -> None:
    state = create_rollback_state()
    with pytest.raises(FreeLLMAPIRollbackError) as exc:
        protect_artifact(state, "sha256:unknown")
    assert exc.value.fault == FreeLLMAPIRollbackFault.MISSING_GENERATION


def test_stage_artifact_fails_when_generation_mismatch() -> None:
    state = create_rollback_state()
    blue_bundle = _bundle("blue")
    with pytest.raises(FreeLLMAPIRollbackError) as exc:
        stage_artifact(state, "not-a-generation", blue_bundle)  # type: ignore[arg-type]
    assert exc.value.fault == FreeLLMAPIRollbackFault.SCHEMA


def test_state_str_is_content_free() -> None:
    state = create_rollback_state()
    blue_bundle = _bundle("blue")
    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.BLUE, blue_bundle)
    state = activate_generation(state, FreeLLMAPIRollbackGeneration.BLUE)
    text = str(state)

    assert "console.log" not in text
    assert "MIT License" not in text
    assert "keyless-ci" not in text
    assert blue_bundle.evidence_id in text
    assert FreeLLMAPIRollbackGeneration.BLUE.value in text


def test_rollback_sequence_records_both_generations() -> None:
    state = create_rollback_state()
    blue_bundle = _bundle("blue")
    green_bundle = _bundle("green")

    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.BLUE, blue_bundle)
    state = activate_generation(state, FreeLLMAPIRollbackGeneration.BLUE)
    state = stage_artifact(state, FreeLLMAPIRollbackGeneration.GREEN, green_bundle)
    state = activate_generation(state, FreeLLMAPIRollbackGeneration.GREEN)
    state = rollback(state)

    assert state.blue_evidence_id == blue_bundle.evidence_id
    assert state.green_evidence_id == green_bundle.evidence_id
