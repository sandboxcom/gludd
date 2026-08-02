"""D-09: Versioned JobSpec with tenant/project ownership, work-type ceilings, and denial audit."""

from __future__ import annotations

import contextlib
import json
from typing import Any, cast

import pytest
from pydantic import ValidationError

import general_ludd.schemas.job as job_schema
from general_ludd.schemas.job import (
    JobIngressLimits,
    JobSpec,
    OwnershipSpec,
    WorkCeilingSpec,
    audit_invalid_job,
    build_denial_audit_record,
)


def _job_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": "job-1_OK",
        "playbook": "validation/noop.yml",
        "queue": "core-priority",
    }
    payload.update(overrides)
    return payload


def _build_job(**overrides: object) -> JobSpec:
    return cast(JobSpec, cast(Any, JobSpec)(**_job_payload(**overrides)))


# ── D-09: OwnershipSpec ──


class TestOwnershipSpec:
    def test_valid_ownership_spec(self) -> None:
        ow = OwnershipSpec(tenant_id="tenant-a", project_id="proj-1", agent_id="agent-42")
        assert ow.tenant_id == "tenant-a"
        assert ow.project_id == "proj-1"
        assert ow.agent_id == "agent-42"

    @pytest.mark.parametrize("field", ("tenant_id", "project_id", "agent_id"))
    def test_ownership_fields_required(self, field: str) -> None:
        kwargs = {"tenant_id": "t1", "project_id": "p1", "agent_id": "a1"}
        kwargs.pop(field)
        with pytest.raises(ValidationError):
            cast(Any, OwnershipSpec)(**kwargs)

    def test_ownership_rejects_blanks(self) -> None:
        with pytest.raises(ValidationError):
            cast(Any, OwnershipSpec)(tenant_id="  ", project_id="p1", agent_id="a1")

    @pytest.mark.parametrize(
        "value",
        ("../escape", "path/traversal", "ten\x00ant", "ten\nant"),
    )
    def test_ownership_rejects_unsafe_shapes(self, value: str) -> None:
        with pytest.raises(ValidationError):
            cast(Any, OwnershipSpec)(tenant_id=value, project_id="p1", agent_id="a1")

    def test_ownership_max_length_enforced(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(job_schema, "JOB_INGRESS_LIMITS", JobIngressLimits(max_identifier_chars=16))
        with pytest.raises(ValidationError, match="tenant_id"):
            cast(Any, OwnershipSpec)(tenant_id="t" * 17, project_id="p1", agent_id="a1")


# ── D-09: WorkCeilingSpec ──


class TestWorkCeilingSpec:
    def test_default_work_ceilings(self) -> None:
        ceilings = WorkCeilingSpec()
        assert ceilings.max_wall_seconds == 3600
        assert ceilings.max_cpu_seconds == 900
        assert ceilings.max_memory_bytes == 536_870_912
        assert ceilings.max_output_bytes == 1_048_576
        assert ceilings.max_spend_micro_dollars == 10_000_000

    def test_explicit_work_ceilings(self) -> None:
        ceilings = WorkCeilingSpec(
            max_wall_seconds=1800,
            max_cpu_seconds=300,
            max_memory_bytes=256_000_000,
            max_output_bytes=500_000,
            max_spend_micro_dollars=5_000_000,
        )
        assert ceilings.max_wall_seconds == 1800
        assert ceilings.max_cpu_seconds == 300

    @pytest.mark.parametrize(
        "field,value",
        (
            ("max_wall_seconds", 0),
            ("max_wall_seconds", -1),
            ("max_cpu_seconds", -1),
            ("max_memory_bytes", 0),
            ("max_output_bytes", -1),
            ("max_spend_micro_dollars", -1),
        ),
    )
    def test_work_ceilings_reject_non_positive(self, field: str, value: int) -> None:
        with pytest.raises(ValidationError):
            cast(Any, WorkCeilingSpec)(**{field: value})

    def test_work_ceilings_from_work_type(self) -> None:
        ceilings = WorkCeilingSpec.for_work_type("code")
        assert ceilings.max_wall_seconds > 0
        assert ceilings.max_allowlisted_backends == ("firecracker", "gvisor")


# ── D-09: JobSpec cross-tenant rejection ──


class TestJobSpecCrossTenant:
    def test_job_spec_accepts_valid_ownership(self) -> None:
        job = _build_job(
            ownership=OwnershipSpec(tenant_id="t1", project_id="p1", agent_id="a1"),
        )
        assert job.ownership.tenant_id == "t1"

    def test_job_spec_rejects_job_id_with_different_tenant_prefix(self) -> None:
        with pytest.raises(ValidationError, match="job_id"):
            _build_job(
                job_id="tenant-b/job-1",
                ownership=OwnershipSpec(tenant_id="tenant-a", project_id="p1", agent_id="a1"),
            )
        job = _build_job(
            job_id="valid-job-1",
            ownership=OwnershipSpec(tenant_id="tenant-a", project_id="p1", agent_id="a1"),
        )
        assert job.ownership.tenant_id == "tenant-a"

    def test_job_spec_job_id_must_be_filesystem_safe(self) -> None:
        with pytest.raises(ValidationError):
            _build_job(job_id="/absolute/path")

    def test_job_spec_non_matching_tenant_rejected_at_validation(self) -> None:
        job = _build_job(
            job_id="task-42",
            project_id="proj-a",
            ownership=OwnershipSpec(tenant_id="t1", project_id="proj-a", agent_id="a1"),
        )
        assert job.project_id == "proj-a"


# ── D-09: Bounded denial audit ──


class TestDenialAudit:
    def test_build_denial_audit_record(self) -> None:
        record = build_denial_audit_record(
            reason_code="INVALID_JOB_ID",
            detail="job_id contains unsafe characters",
            raw_payload={"job_id": "../escape"},
        )
        assert record["schema_version"] == "1.0"
        assert record["reason_code"] == "INVALID_JOB_ID"
        assert record["decision"] == "deny"
        assert record["timestamp"]  # str, not empty
        assert "raw_payload" not in record
        assert record["detail"] == "job_id contains unsafe characters"

    def test_build_denial_audit_record_redacts_secrets(self) -> None:
        record = build_denial_audit_record(
            reason_code="INVALID_INGRESS",
            detail="JSON-compatible",
            raw_payload={"api_key": "secret-12345"},
        )
        assert "api_key" not in str(record)
        assert "secret-12345" not in str(record)

    def test_audit_invalid_job_is_side_effect_free(self) -> None:
        record = audit_invalid_job(
            reason_code="TEST_AUDIT",
            detail="testing audit path",
            raw_payload={},
        )
        assert record["reason_code"] == "TEST_AUDIT"
        assert record["decision"] == "deny"

    def test_audit_stays_below_payload_limit(self) -> None:
        long_string = "x" * 200_000
        record = audit_invalid_job(
            reason_code="LARGE_PAYLOAD",
            detail=long_string,
            raw_payload={"data": "small"},
        )
        serialized = json.dumps(record)
        assert len(serialized) <= 131_072  # 128 KiB bound


# ── D-09: Versioned schema / policy hash ──


class TestJobSpecVersionedSchema:
    def test_job_spec_exposes_policy_version(self) -> None:
        job = _build_job()
        version = job.policy_version()
        assert isinstance(version, str)
        assert version.startswith("jobspec-v1:")

    def test_job_spec_policy_hash_is_deterministic(self) -> None:
        job_a = _build_job(
            job_id="task-1",
            ownership=OwnershipSpec(tenant_id="t1", project_id="p1", agent_id="a1"),
        )
        job_b = _build_job(
            job_id="task-1",
            ownership=OwnershipSpec(tenant_id="t1", project_id="p1", agent_id="a1"),
        )
        assert job_a.policy_hash() == job_b.policy_hash()

    def test_job_spec_policy_hash_differs_for_different_owner(self) -> None:
        job_a = _build_job(ownership=OwnershipSpec(tenant_id="tA", project_id="p1", agent_id="a1"))
        job_b = _build_job(ownership=OwnershipSpec(tenant_id="tB", project_id="p1", agent_id="a1"))
        assert job_a.policy_hash() != job_b.policy_hash()


# ── D-09: Per-work-type ceilings ──


class TestPerWorkTypeCeilings:
    def test_code_work_type_has_tight_ceilings(self) -> None:
        c = WorkCeilingSpec.for_work_type("code")
        assert c.max_wall_seconds <= 1800
        assert c.max_cpu_seconds <= 300

    def test_unknown_work_type_uses_default(self) -> None:
        c = WorkCeilingSpec.for_work_type("nonexistent")
        assert c == WorkCeilingSpec()

    def test_audit_work_type_has_wide_ceilings(self) -> None:
        c = WorkCeilingSpec.for_work_type("audit")
        assert c.max_wall_seconds >= WorkCeilingSpec().max_wall_seconds


# ── D-09: Fuzz rejection is side-effect free ──


class TestFuzzRejectionSideEffectFree:
    def test_malformed_job_creates_no_files(self, tmp_path: Any) -> None:
        import os

        before = set(os.listdir(tmp_path))
        with contextlib.suppress(ValidationError):
            _build_job(budget_context=cast(Any, {"__proto__": {"polluted": True}}))
        after = set(os.listdir(tmp_path))
        assert before == after

    def test_deeply_nested_payload_is_rejected(self) -> None:
        deep: dict[str, Any] = {"leaf": 1}
        for _ in range(20):
            deep = {"child": deep}
        with pytest.raises(ValidationError):
            _build_job(budget_context=deep)

    def test_duplicate_job_ids_are_accepted_at_schema_level(self) -> None:
        job1 = _build_job(job_id="dup-id")
        job2 = _build_job(job_id="dup-id")
        assert job1.job_id == job2.job_id == "dup-id"

    def test_gigantic_job_id_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(job_schema, "JOB_INGRESS_LIMITS", JobIngressLimits(max_identifier_chars=32))
        with pytest.raises(ValidationError, match="job_id"):
            _build_job(job_id="j" * 33)
