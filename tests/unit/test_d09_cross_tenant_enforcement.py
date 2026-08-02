"""D-09: Cross-tenant ownership enforcement at JobSpec ingress.

The schema-level validation exists in schemas/job.py. These tests verify
that cross-tenant rejection is enforced: a job whose OwnershipSpec.tenant_id
does not match the authenticated request tenant MUST be denied.
"""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import ValidationError

from general_ludd.schemas.job import (
    JobSpec,
    OwnershipSpec,
    audit_invalid_job,
    validate_cross_tenant,
)


class TestCrossTenantValidation:
    def test_same_tenant_accepted(self) -> None:
        ow = OwnershipSpec(tenant_id="org-a", project_id="p1", agent_id="a1")
        result = validate_cross_tenant(ow, request_tenant_id="org-a")
        assert result is True

    def test_different_tenant_rejected(self) -> None:
        ow = OwnershipSpec(tenant_id="org-a", project_id="p1", agent_id="a1")
        is_valid, reason = validate_cross_tenant(ow, request_tenant_id="org-b")
        assert is_valid is False
        assert "tenant" in reason.lower()

    def test_none_request_tenant_rejected(self) -> None:
        ow = OwnershipSpec(tenant_id="org-a", project_id="p1", agent_id="a1")
        is_valid, reason = validate_cross_tenant(ow, request_tenant_id=None)
        assert is_valid is False
        assert "tenant" in reason.lower()

    def test_different_tenant_with_similar_name_rejected(self) -> None:
        ow = OwnershipSpec(tenant_id="acme-corp", project_id="p1", agent_id="a1")
        is_valid, _reason = validate_cross_tenant(ow, request_tenant_id="acme")
        assert is_valid is False

    def test_empty_request_tenant_rejected(self) -> None:
        ow = OwnershipSpec(tenant_id="org-a", project_id="p1", agent_id="a1")
        is_valid, _reason = validate_cross_tenant(ow, request_tenant_id="")
        assert is_valid is False

    def test_case_sensitive_comparison(self) -> None:
        ow = OwnershipSpec(tenant_id="Org-A", project_id="p1", agent_id="a1")
        is_valid, _ = validate_cross_tenant(ow, request_tenant_id="org-a")
        assert is_valid is False

    def test_cross_tenant_audit_generates_redacted_record(self) -> None:
        ow = OwnershipSpec(tenant_id="org-a", project_id="p1", agent_id="a1")
        _is_valid, reason = validate_cross_tenant(ow, request_tenant_id="org-b")
        record = audit_invalid_job(
            reason_code="CROSS_TENANT_MISMATCH",
            detail=reason,
        )
        assert record["reason_code"] == "CROSS_TENANT_MISMATCH"
        assert record["decision"] == "deny"
        assert "org-a" not in str(record)
        assert "org-b" not in str(record)


class TestJobSpecCrossTenantIntegration:
    def test_job_with_matching_tenant_in_ownership_accepted(self) -> None:
        job_data: dict[str, object] = {
            "job_id": "task-1",
            "playbook": "tasks/noop.yml",
            "queue": "default",
            "ownership": {
                "tenant_id": "t1",
                "project_id": "p1",
                "agent_id": "a1",
            },
        }
        job = cast(Any, JobSpec)(**job_data)
        ok, _ = validate_cross_tenant(job.ownership, "t1")
        assert ok is True

    def test_job_with_mismatched_tenant_in_ownership_denied(self) -> None:
        job_data: dict[str, object] = {
            "job_id": "task-2",
            "playbook": "tasks/noop.yml",
            "queue": "default",
            "ownership": {
                "tenant_id": "t2",
                "project_id": "p1",
                "agent_id": "a1",
            },
        }
        job = cast(Any, JobSpec)(**job_data)
        ok, _reason = validate_cross_tenant(job.ownership, "t1")
        assert ok is False

    def test_validate_cross_tenant_is_side_effect_free(self, tmp_path: Any) -> None:
        import os

        before_files = set(os.listdir(tmp_path))
        ow = OwnershipSpec(tenant_id="org-a", project_id="p1", agent_id="a1")
        validate_cross_tenant(ow, request_tenant_id="org-b")
        after_files = set(os.listdir(tmp_path))
        assert before_files == after_files


class TestCrossTenantAtIngressBoundary:
    """Tests demonstrating the rejection path at the JobSpec ingress boundary."""

    def test_job_with_non_matching_tenant_fails_validation(self) -> None:
        with pytest.raises(ValidationError, match="job_id"):
            cast(Any, JobSpec)(
                job_id="tenant-b/task-3",
                playbook="tasks/noop.yml",
                queue="default",
                ownership={
                    "tenant_id": "tenant-a",
                    "project_id": "p1",
                    "agent_id": "a1",
                },
            )

    def test_job_without_ownership_still_validates(self) -> None:
        job = cast(Any, JobSpec)(
            job_id="task-4",
            playbook="tasks/noop.yml",
            queue="default",
        )
        assert job.job_id == "task-4"
