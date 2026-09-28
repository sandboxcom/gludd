"""Structural contract for the OpenJev/Laya operator runbook."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs/operations/OPENJEV_LAYA_ADAPTER_RUNBOOK.md"
FEATURE = ROOT / "docs/releases/v0.1.2-openjev-laya-adapter-contract.md"


def _runbook() -> str:
    return RUNBOOK.read_text(encoding="utf-8")


def _documented_fields(runbook: str, label: str) -> tuple[str, ...]:
    line = next(line for line in runbook.splitlines() if line.startswith(f"| {label} |"))
    return tuple(re.findall(r"`([^`]+)`", line))


def test_feature_contract_links_operator_runbook_and_authority_is_bounded() -> None:
    assert RUNBOOK.is_file()
    feature = FEATURE.read_text(encoding="utf-8")
    assert "../operations/OPENJEV_LAYA_ADAPTER_RUNBOOK.md" in feature

    runbook = _runbook()
    assert "Runtime admission status: NOT IMPLEMENTED" in runbook
    assert "Recovery execution status: NOT IMPLEMENTED" in runbook
    assert "Dry-run recovery modes are evidence, not callable mechanisms." in runbook


def test_runbook_covers_the_complete_operator_sequence_and_failures() -> None:
    runbook = _runbook()
    for heading in (
        "## Authority and Preconditions",
        "## Phase 1: Offline Discovery",
        "## Phase 2: Compatibility Check",
        "## Phase 3: Dry-Run Report and Verification",
        "## Phase 4: Durable Pre-Projection Handoff",
        "## Phase 5: Staged Admission (Future Gate)",
        "## Phase 6: Rollback and ZDD Handoff",
        "## Operator Decision Table",
        "## Observability and Evidence Retention",
        "## Resource and Safety Limits",
        "## Safe Upgrade and Revert Sequence",
    ):
        assert heading in runbook

    for signal in (
        "Digest mismatch",
        "Schema/version mismatch",
        "Identity reuse",
        "Redaction failure",
        "Interrupted handoff",
        "Disabled recovery",
    ):
        assert f"| {signal} |" in runbook


def test_runbook_pins_exact_evidence_fields_and_resource_bounds() -> None:
    runbook = _runbook()
    field_groups = {
        "Prepared document": ("version", "plan", "receipt"),
        "Compatibility evidence": (
            "schema_digest",
            "baseline_profile_digest",
            "candidate_profile_digest",
            "checker_status",
            "changed_pin_paths",
            "protected_sections_verified",
            "findings",
            "evidence_digest",
        ),
        "Update plan": (
            "version",
            "event_id",
            "recorded_at",
            "actor_digest",
            "baseline",
            "candidate",
            "compatibility_evidence",
            "rollback",
            "actions",
            "admission_enabled",
            "network_accessed",
            "runtime_changed",
            "plan_digest",
        ),
        "Update receipt": (
            "version",
            "event_id",
            "recorded_at",
            "actor_digest",
            "status",
            "plan_digest",
            "evidence_digest",
            "rollback_digest",
            "redacted_fields",
            "admission_enabled",
            "network_accessed",
            "runtime_changed",
            "receipt_digest",
        ),
        "Dry-run report": (
            "version",
            "schema_digest",
            "lanes",
            "redacted_fields",
            "admission_enabled",
            "admission_performed",
            "apply_performed",
            "external_mutation_performed",
            "model_invoked",
            "models_downloaded",
            "network_accessed",
            "persistence_performed",
            "recovery_performed",
            "runtime_changed",
            "runtime_invoked",
            "report_digest",
        ),
        "Dry-run lane": (
            "version",
            "lane_reference",
            "profile_reference",
            "compatibility",
            "provenance",
            "rollback",
            "recovery_modes",
            "stages",
            "batch_plan_digest",
            "fixture_manifest_digest",
            "normalization_record_digests",
            "normalization_kinds",
            "lane_digest",
        ),
        "Dry-run stage": (
            "version",
            "ordinal",
            "stage",
            "status",
            "predecessor_digest",
            "evidence_digest",
            "admission_performed",
            "apply_performed",
            "external_mutation_performed",
            "model_invoked",
            "models_downloaded",
            "network_accessed",
            "persistence_performed",
            "runtime_changed",
            "runtime_invoked",
            "stage_digest",
        ),
        "Pre-projection recovery receipt": (
            "version",
            "phase",
            "schema_digest",
            "lane_reference",
            "candidate_profile_digest",
            "prepared_update_digest",
            "update_receipt_digest",
            "rollback_digest",
            "rollback_target_state",
            "rollback_required",
            "admission_enabled",
            "admission_performed",
            "model_invoked",
            "models_downloaded",
            "network_accessed",
            "runtime_changed",
            "runtime_invoked",
            "recovery_digest",
        ),
        "Offline acceptance receipt": (
            "version",
            "schema_digest",
            "lanes",
            "redacted_fields",
            "admission_enabled",
            "admission_performed",
            "model_invoked",
            "models_downloaded",
            "network_accessed",
            "runtime_changed",
            "runtime_invoked",
            "receipt_digest",
        ),
    }
    for label, fields in field_groups.items():
        assert _documented_fields(runbook, label) == fields

    for bound in (
        "2 MiB local JSON input",
        "256 KiB prepared update",
        "16 lanes",
        "64 normalization fixtures",
        "1,024 planner requests",
        "64 planner profiles",
        "64 resident identities",
        "1 GiB aggregate request bytes",
        "32 levels / 65,536 nodes",
        "8 levels / 4,096 nodes / 256 KiB characters",
    ):
        assert bound in runbook
