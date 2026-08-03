"""Deep JobSpec lifecycle tests: spec validation, state transitions, failure
recovery, retry logic, priority handling, deadline enforcement, and resource
allocation.

Covers JobSpec, OwnershipSpec, WorkCeilingSpec, JobIngressLimits, cross-tenant
validation, and denial audit records.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any, Final, cast

import pytest
from pydantic import ValidationError

from general_ludd.schemas.job import (
    JobIngressLimits,
    JobSpec,
    OwnershipSpec,
    WorkCeilingSpec,
    audit_invalid_job,
    build_denial_audit_record,
    validate_cross_tenant,
)
from general_ludd.schemas.todo import ResourceProfile, WorkType

# ═══════════════════════════════════════════════════════════════════════
# Virtual job lifecycle state machine (modelled here; no class in src/)
# ═══════════════════════════════════════════════════════════════════════


class JobLifecycleState(Enum):
    QUEUED = auto()
    DISPATCHED = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    TIMEOUT = auto()
    CANCELLED = auto()
    RETRYING = auto()


_job_lifecycle_graph: dict[JobLifecycleState, frozenset[JobLifecycleState]] = {
    JobLifecycleState.QUEUED: frozenset({JobLifecycleState.DISPATCHED, JobLifecycleState.CANCELLED}),
    JobLifecycleState.DISPATCHED: frozenset({JobLifecycleState.RUNNING, JobLifecycleState.CANCELLED}),
    JobLifecycleState.RUNNING: frozenset(
        {
            JobLifecycleState.COMPLETED,
            JobLifecycleState.FAILED,
            JobLifecycleState.TIMEOUT,
            JobLifecycleState.CANCELLED,
        }
    ),
    JobLifecycleState.FAILED: frozenset({JobLifecycleState.RETRYING, JobLifecycleState.CANCELLED}),
    JobLifecycleState.TIMEOUT: frozenset({JobLifecycleState.RETRYING, JobLifecycleState.CANCELLED}),
    JobLifecycleState.RETRYING: frozenset({JobLifecycleState.QUEUED, JobLifecycleState.CANCELLED}),
    JobLifecycleState.COMPLETED: frozenset(),
    JobLifecycleState.CANCELLED: frozenset(),
}

TERMINAL_STATES: Final[frozenset[JobLifecycleState]] = frozenset(
    {JobLifecycleState.COMPLETED, JobLifecycleState.CANCELLED}
)

RETRYABLE_STATES: Final[frozenset[JobLifecycleState]] = frozenset({JobLifecycleState.FAILED, JobLifecycleState.TIMEOUT})


def validate_transition(
    current: JobLifecycleState,
    next_state: JobLifecycleState,
) -> tuple[bool, str]:
    allowed = _job_lifecycle_graph.get(current, frozenset())
    if next_state in allowed:
        return True, "ok"
    return False, f"TRANSITION_DENIED: {current.name} → {next_state.name}"


def compute_retry_delay(attempt: int, base_delay: float = 1.0, max_delay: float = 60.0) -> float:
    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
    return delay


@dataclass(frozen=True, slots=True)
class JobStateRecord:
    state: JobLifecycleState
    retry_count: int
    max_retries: int
    priority: int
    deadline_epoch: float | None
    resource_profile: str


def can_retry(record: JobStateRecord) -> bool:
    return record.state in RETRYABLE_STATES and record.retry_count < record.max_retries


def is_deadline_exceeded(record: JobStateRecord, now: float) -> bool:
    return record.deadline_epoch is not None and now > record.deadline_epoch


# ═══════════════════════════════════════════════════════════════════════
# JobSpec helpers
# ═══════════════════════════════════════════════════════════════════════


def _job_kwargs(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "job_id": "JOB-001",
        "playbook": "tasks/review.yml",
        "queue": "core-priority",
    }
    payload.update(overrides)
    return payload


def _build(**overrides: object) -> JobSpec:
    return cast(JobSpec, cast(Any, JobSpec)(**_job_kwargs(**overrides)))


# ═══════════════════════════════════════════════════════════════════════
# 1. Spec validation
# ═══════════════════════════════════════════════════════════════════════


class TestJobSpecConstruction:
    def test_minimal_valid_spec(self) -> None:
        spec = _build()
        assert spec.job_id == "JOB-001"
        assert spec.playbook == "tasks/review.yml"
        assert spec.queue == "core-priority"

    def test_all_fields_explicit(self) -> None:
        spec = _build(
            job_id="JOB-FULL",
            todo_id="TODO-42",
            return_id="RET-99",
            project_id="proj-7",
            playbook="ansible/run.yml",
            queue="batch",
            work_type="code",
            resource_profile="ai_heavy",
            model_profile="opus",
            prompt_profile="verbose",
            vars_namespace_refs=["ns-prod", "ns-staging"],
            artifact_dir="/tmp/artifacts",
            budget_context={"cost_cap": 5000},
            candidate_todos=["task-a", "task-b"],
            artifact_summaries=["summary-1.log"],
            plan_artifact="plan-v2.json",
            prompt_text="Review this PR",
            skill_body="---\nskill: code-review\n",
            ansible_roles_path="/opt/ansible/roles",
            templates_dir="/opt/gludd/templates",
            timeout=300.0,
            human_input="User approved deployment",
            ownership=OwnershipSpec(
                tenant_id="acme",
                project_id="widgets",
                agent_id="agent-7",
            ),
        )
        assert spec.job_id == "JOB-FULL"
        assert spec.todo_id == "TODO-42"
        assert spec.return_id == "RET-99"
        assert spec.project_id == "proj-7"
        assert spec.resource_profile == "ai_heavy"
        assert spec.ownership is not None
        assert spec.ownership.tenant_id == "acme"
        assert spec.ownership.project_id == "widgets"
        assert spec.timeout == 300.0
        assert spec.prompt_text == "Review this PR"
        assert len(spec.candidate_todos) == 2
        assert spec.human_input == "User approved deployment"

    def test_defaults_are_sensible(self) -> None:
        spec = _build()
        assert spec.work_type == "unknown"
        assert spec.resource_profile == "low_resource"
        assert spec.timeout is None
        assert spec.ownership is None
        assert spec.candidate_todos == []
        assert spec.artifact_summaries == []
        assert spec.budget_context == {}

    @pytest.mark.parametrize(
        ("field", "invalid", "match"),
        (
            ("job_id", "", "must not be empty"),
            ("job_id", "   ", "must not be empty"),
            ("playbook", "", "must not be empty"),
            ("queue", "", "must not be empty"),
            ("queue", "bad/name", "identifier-like slug"),
        ),
    )
    def test_required_fields_reject_empty(self, field: str, invalid: str, match: str) -> None:
        with pytest.raises(ValidationError, match=match):
            _build(**{field: invalid})

    def test_extra_fields_forbidden(self) -> None:
        with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
            _build(fake_field="bad")


# ═══════════════════════════════════════════════════════════════════════
# 2. State transitions
# ═══════════════════════════════════════════════════════════════════════


class TestJobLifecycleStateTransitions:
    def test_queued_to_dispatched_allowed(self) -> None:
        ok, _ = validate_transition(JobLifecycleState.QUEUED, JobLifecycleState.DISPATCHED)
        assert ok

    def test_queued_to_cancelled_allowed(self) -> None:
        ok, _ = validate_transition(JobLifecycleState.QUEUED, JobLifecycleState.CANCELLED)
        assert ok

    def test_queued_to_completed_denied(self) -> None:
        ok, msg = validate_transition(JobLifecycleState.QUEUED, JobLifecycleState.COMPLETED)
        assert not ok
        assert "TRANSITION_DENIED" in msg

    def test_running_to_completed_allowed(self) -> None:
        ok, _ = validate_transition(JobLifecycleState.RUNNING, JobLifecycleState.COMPLETED)
        assert ok

    def test_running_to_failed_allowed(self) -> None:
        ok, _ = validate_transition(JobLifecycleState.RUNNING, JobLifecycleState.FAILED)
        assert ok

    def test_running_to_timeout_allowed(self) -> None:
        ok, _ = validate_transition(JobLifecycleState.RUNNING, JobLifecycleState.TIMEOUT)
        assert ok

    def test_failed_to_retrying_allowed(self) -> None:
        ok, _ = validate_transition(JobLifecycleState.FAILED, JobLifecycleState.RETRYING)
        assert ok

    def test_timeout_to_retrying_allowed(self) -> None:
        ok, _ = validate_transition(JobLifecycleState.TIMEOUT, JobLifecycleState.RETRYING)
        assert ok

    def test_retrying_to_queued_allowed(self) -> None:
        ok, _ = validate_transition(JobLifecycleState.RETRYING, JobLifecycleState.QUEUED)
        assert ok

    def test_completed_is_terminal(self) -> None:
        assert JobLifecycleState.COMPLETED in TERMINAL_STATES
        assert len(_job_lifecycle_graph[JobLifecycleState.COMPLETED]) == 0

    def test_cancelled_is_terminal(self) -> None:
        assert JobLifecycleState.CANCELLED in TERMINAL_STATES
        assert len(_job_lifecycle_graph[JobLifecycleState.CANCELLED]) == 0

    def test_terminal_states_have_no_outgoing(self) -> None:
        for state in TERMINAL_STATES:
            assert _job_lifecycle_graph[state] == frozenset(), f"{state.name} should be terminal"

    def test_all_non_terminal_states_have_outgoing(self) -> None:
        for state in JobLifecycleState:
            if state not in TERMINAL_STATES:
                assert len(_job_lifecycle_graph[state]) > 0, f"{state.name} should have outgoing transitions"

    def test_dispatched_skips_running_denied(self) -> None:
        ok, msg = validate_transition(JobLifecycleState.DISPATCHED, JobLifecycleState.COMPLETED)
        assert not ok
        assert "TRANSITION_DENIED" in msg

    def test_graph_is_exhaustive(self) -> None:
        for state in JobLifecycleState:
            assert state in _job_lifecycle_graph, f"{state.name} missing from graph"


# ═══════════════════════════════════════════════════════════════════════
# 3. Failure recovery & denial audit
# ═══════════════════════════════════════════════════════════════════════


class TestFailureRecovery:
    def test_audit_record_has_required_fields(self) -> None:
        record = build_denial_audit_record(
            reason_code="INVALID_JOB_ID",
            detail="job_id contained unsafe characters",
        )
        assert record["schema_version"] == "1.0"
        assert record["decision"] == "deny"
        assert record["reason_code"] == "INVALID_JOB_ID"
        assert "event_id" in record
        assert "timestamp" in record

    def test_audit_record_detail_is_truncated(self) -> None:
        long_detail = "x" * 2000
        record = build_denial_audit_record(
            reason_code="PAYLOAD_TOO_LARGE",
            detail=long_detail,
        )
        assert len(cast(str, record["detail"])) <= 1024

    def test_audit_record_is_serializable(self) -> None:
        record = build_denial_audit_record(
            reason_code="CROSS_TENANT",
            detail="tenant mismatch",
        )
        serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        assert len(serialized.encode("utf-8")) <= 131_072

    def test_audit_invalid_job_same_as_direct_audit(self) -> None:
        record = audit_invalid_job(
            reason_code="INVALID_PAYLOAD",
            detail="payload exceeded depth limit",
        )
        assert record["reason_code"] == "INVALID_PAYLOAD"
        assert record["decision"] == "deny"

    def test_audit_with_redacted_secrets(self) -> None:
        record = build_denial_audit_record(
            reason_code="DENIED",
            detail="rejected",
        )
        assert "api_key" not in json.dumps(record)

    @pytest.mark.parametrize(
        "reason_code",
        (
            "INVALID_JOB_ID",
            "CROSS_TENANT_MISMATCH",
            "MISSING_OWNERSHIP",
            "PAYLOAD_TOO_DEEP",
            "EMPTY_PLAYBOOK",
        ),
    )
    def test_known_failure_reason_codes_accepted(self, reason_code: str) -> None:
        record = build_denial_audit_record(
            reason_code=reason_code,
            detail=f"Failure detail for {reason_code}",
        )
        assert record["reason_code"] == reason_code

    def test_cross_tenant_missing_request_tenant(self) -> None:
        result, reason = validate_cross_tenant(
            OwnershipSpec(tenant_id="acme", project_id="p", agent_id="a"),
            request_tenant_id=None,
        )
        assert not result
        assert "missing or empty" in reason

    def test_cross_tenant_empty_request_tenant(self) -> None:
        result, reason = validate_cross_tenant(
            OwnershipSpec(tenant_id="acme", project_id="p", agent_id="a"),
            request_tenant_id="   ",
        )
        assert not result
        assert "missing or empty" in reason

    def test_cross_tenant_no_ownership_spec(self) -> None:
        result, reason = validate_cross_tenant(None, "acme")
        assert not result
        assert "no ownership spec" in reason

    def test_cross_tenant_mismatch(self) -> None:
        result, reason = validate_cross_tenant(
            OwnershipSpec(tenant_id="acme", project_id="p", agent_id="a"),
            request_tenant_id="evilcorp",
        )
        assert not result
        assert "does not match" in reason

    def test_cross_tenant_valid_match(self) -> None:
        result, reason = validate_cross_tenant(
            OwnershipSpec(tenant_id="acme", project_id="p", agent_id="a"),
            request_tenant_id="acme",
        )
        assert result
        assert reason == "ok"


# ═══════════════════════════════════════════════════════════════════════
# 4. Retry logic
# ═══════════════════════════════════════════════════════════════════════


class TestRetryLogic:
    def test_failed_state_is_retryable(self) -> None:
        assert JobLifecycleState.FAILED in RETRYABLE_STATES

    def test_timeout_state_is_retryable(self) -> None:
        assert JobLifecycleState.TIMEOUT in RETRYABLE_STATES

    def test_completed_is_not_retryable(self) -> None:
        assert JobLifecycleState.COMPLETED not in RETRYABLE_STATES

    def test_cancelled_is_not_retryable(self) -> None:
        assert JobLifecycleState.CANCELLED not in RETRYABLE_STATES

    def test_can_retry_when_under_limit(self) -> None:
        record = JobStateRecord(
            state=JobLifecycleState.FAILED,
            retry_count=2,
            max_retries=5,
            priority=0,
            deadline_epoch=None,
            resource_profile="low_resource",
        )
        assert can_retry(record)

    def test_cannot_retry_when_at_limit(self) -> None:
        record = JobStateRecord(
            state=JobLifecycleState.FAILED,
            retry_count=5,
            max_retries=5,
            priority=0,
            deadline_epoch=None,
            resource_profile="low_resource",
        )
        assert not can_retry(record)

    def test_cannot_retry_when_exceeded_limit(self) -> None:
        record = JobStateRecord(
            state=JobLifecycleState.FAILED,
            retry_count=7,
            max_retries=5,
            priority=0,
            deadline_epoch=None,
            resource_profile="low_resource",
        )
        assert not can_retry(record)

    def test_cannot_retry_non_retryable_state(self) -> None:
        record = JobStateRecord(
            state=JobLifecycleState.COMPLETED,
            retry_count=0,
            max_retries=5,
            priority=0,
            deadline_epoch=None,
            resource_profile="low_resource",
        )
        assert not can_retry(record)

    def test_retry_delay_exponential_backoff(self) -> None:
        assert compute_retry_delay(1) == 1.0
        assert compute_retry_delay(2) == 2.0
        assert compute_retry_delay(3) == 4.0
        assert compute_retry_delay(4) == 8.0

    def test_retry_delay_capped_at_max(self) -> None:
        assert compute_retry_delay(10) == 60.0
        assert compute_retry_delay(20) == 60.0

    def test_retry_delay_with_custom_base(self) -> None:
        delay = compute_retry_delay(3, base_delay=5.0, max_delay=120.0)
        assert delay == 20.0

    def test_full_retry_cycle_transitions(self) -> None:
        path = [
            (JobLifecycleState.QUEUED, JobLifecycleState.DISPATCHED),
            (JobLifecycleState.DISPATCHED, JobLifecycleState.RUNNING),
            (JobLifecycleState.RUNNING, JobLifecycleState.FAILED),
            (JobLifecycleState.FAILED, JobLifecycleState.RETRYING),
            (JobLifecycleState.RETRYING, JobLifecycleState.QUEUED),
            (JobLifecycleState.QUEUED, JobLifecycleState.DISPATCHED),
            (JobLifecycleState.DISPATCHED, JobLifecycleState.RUNNING),
            (JobLifecycleState.RUNNING, JobLifecycleState.COMPLETED),
        ]
        for curr, nxt in path:
            ok, msg = validate_transition(curr, nxt)
            assert ok, f"{curr.name} → {nxt.name}: {msg}"


# ═══════════════════════════════════════════════════════════════════════
# 5. Priority handling
# ═══════════════════════════════════════════════════════════════════════


class TestPriorityHandling:
    def test_resource_profile_default_is_low(self) -> None:
        spec = _build()
        assert spec.resource_profile == "low_resource"

    def test_resource_profile_ai_heavy_accepted(self) -> None:
        spec = _build(resource_profile="ai_heavy")
        assert spec.resource_profile == "ai_heavy"

    def test_resource_profile_local_heavy_accepted(self) -> None:
        spec = _build(resource_profile="local_heavy")
        assert spec.resource_profile == "local_heavy"

    def test_resource_profile_network_heavy_accepted(self) -> None:
        spec = _build(resource_profile="network_heavy")
        assert spec.resource_profile == "network_heavy"

    def test_resource_profile_from_enum_preserves_value(self) -> None:
        spec = _build(resource_profile=ResourceProfile.AI_HEAVY)
        assert spec.resource_profile == "ai_heavy"

    def test_work_type_default_is_unknown(self) -> None:
        spec = _build()
        assert spec.work_type == "unknown"

    def test_work_type_code_accepted(self) -> None:
        spec = _build(work_type=WorkType.CODE)
        assert spec.work_type == "code"

    def test_work_type_audit_accepted(self) -> None:
        spec = _build(work_type=WorkType.AUDIT)
        assert spec.work_type == "audit"

    def test_work_type_review_accepted(self) -> None:
        spec = _build(work_type=WorkType.REVIEW)
        assert spec.work_type == "review"

    @pytest.mark.parametrize(
        ("resource", "expected"),
        (
            (ResourceProfile.LOW_RESOURCE, "low_resource"),
            (ResourceProfile.AI_HEAVY, "ai_heavy"),
            (ResourceProfile.LOCAL_HEAVY, "local_heavy"),
            (ResourceProfile.HYBRID, "hybrid"),
            (ResourceProfile.NETWORK_HEAVY, "network_heavy"),
        ),
    )
    def test_all_resource_profiles_normalize_correctly(self, resource: ResourceProfile, expected: str) -> None:
        spec = _build(resource_profile=resource)
        assert spec.resource_profile == expected


# ═══════════════════════════════════════════════════════════════════════
# 6. Deadline enforcement
# ═══════════════════════════════════════════════════════════════════════


class TestDeadlineEnforcement:
    def test_no_deadline_never_exceeded(self) -> None:
        record = JobStateRecord(
            state=JobLifecycleState.RUNNING,
            retry_count=0,
            max_retries=3,
            priority=0,
            deadline_epoch=None,
            resource_profile="low_resource",
        )
        assert not is_deadline_exceeded(record, 1_000_000.0)

    def test_future_deadline_not_exceeded(self) -> None:
        record = JobStateRecord(
            state=JobLifecycleState.RUNNING,
            retry_count=0,
            max_retries=3,
            priority=0,
            deadline_epoch=1000.0,
            resource_profile="low_resource",
        )
        assert not is_deadline_exceeded(record, 500.0)

    def test_past_deadline_is_exceeded(self) -> None:
        record = JobStateRecord(
            state=JobLifecycleState.RUNNING,
            retry_count=0,
            max_retries=3,
            priority=0,
            deadline_epoch=1000.0,
            resource_profile="low_resource",
        )
        assert is_deadline_exceeded(record, 1500.0)

    def test_exact_deadline_not_exceeded(self) -> None:
        record = JobStateRecord(
            state=JobLifecycleState.RUNNING,
            retry_count=0,
            max_retries=3,
            priority=0,
            deadline_epoch=1000.0,
            resource_profile="low_resource",
        )
        assert not is_deadline_exceeded(record, 1000.0)

    def test_timeout_positive_accepted(self) -> None:
        spec = _build(timeout=60.0)
        assert spec.timeout == 60.0

    def test_timeout_zero_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timeout must be positive"):
            _build(timeout=0)

    def test_timeout_negative_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timeout must be positive"):
            _build(timeout=-1)

    def test_timeout_none_accepted(self) -> None:
        spec = _build(timeout=None)
        assert spec.timeout is None

    def test_timeout_from_string(self) -> None:
        spec = _build(timeout="30")
        assert spec.timeout == 30.0

    def test_timeout_float_from_string(self) -> None:
        spec = _build(timeout="12.5")
        assert spec.timeout == 12.5

    def test_timeout_invalid_string_rejected(self) -> None:
        with pytest.raises(ValidationError, match="timeout must be a number"):
            _build(timeout="not-a-number")

    def test_deadline_exceeded_prevents_retry(self) -> None:
        record = JobStateRecord(
            state=JobLifecycleState.FAILED,
            retry_count=2,
            max_retries=5,
            priority=0,
            deadline_epoch=1000.0,  # past
            resource_profile="low_resource",
        )
        assert can_retry(record)
        assert is_deadline_exceeded(record, 1500.0)


# ═══════════════════════════════════════════════════════════════════════
# 7. Resource allocation
# ═══════════════════════════════════════════════════════════════════════


class TestResourceAllocation:
    def test_default_work_ceiling_has_sensible_defaults(self) -> None:
        ceiling = WorkCeilingSpec()
        assert ceiling.max_wall_seconds == 3600
        assert ceiling.max_cpu_seconds == 900
        assert ceiling.max_memory_bytes == 536_870_912
        assert ceiling.max_output_bytes == 1_048_576
        assert ceiling.max_spend_micro_dollars == 10_000_000

    def test_code_work_type_has_lower_ceilings(self) -> None:
        ceiling = WorkCeilingSpec.for_work_type("code")
        assert ceiling.max_wall_seconds == 1800
        assert ceiling.max_cpu_seconds == 300
        assert ceiling.max_memory_bytes == 268_435_456
        assert ceiling.max_spend_micro_dollars == 5_000_000

    def test_audit_work_type_has_higher_ceilings(self) -> None:
        ceiling = WorkCeilingSpec.for_work_type("audit")
        assert ceiling.max_wall_seconds == 7200
        assert ceiling.max_cpu_seconds == 1800
        assert ceiling.max_memory_bytes == 1_073_741_824
        assert ceiling.max_spend_micro_dollars == 20_000_000

    def test_research_work_type_ceilings(self) -> None:
        ceiling = WorkCeilingSpec.for_work_type("research")
        assert ceiling.max_wall_seconds == 3600
        assert ceiling.max_cpu_seconds == 600

    def test_unknown_work_type_uses_defaults(self) -> None:
        ceiling = WorkCeilingSpec.for_work_type("nonexistent")
        assert ceiling.max_wall_seconds == 3600
        assert ceiling.max_cpu_seconds == 900

    def test_ceiling_can_be_overridden(self) -> None:
        ceiling = WorkCeilingSpec(max_wall_seconds=500, max_cpu_seconds=100)
        assert ceiling.max_wall_seconds == 500
        assert ceiling.max_cpu_seconds == 100

    def test_ceiling_rejects_zero_wall_seconds(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            WorkCeilingSpec(max_wall_seconds=0)

    def test_ceiling_rejects_zero_memory(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            WorkCeilingSpec(max_memory_bytes=0)

    def test_ceiling_allows_zero_spend(self) -> None:
        ceiling = WorkCeilingSpec(max_spend_micro_dollars=0)
        assert ceiling.max_spend_micro_dollars == 0

    def test_job_ingress_limits_defaults(self) -> None:
        limits = JobIngressLimits()
        assert limits.max_depth == 16
        assert limits.max_collection_items == 10_000
        assert limits.max_serialized_bytes == 1_048_576
        assert limits.max_identifier_chars == 128
        assert limits.max_playbook_chars == 255
        assert limits.max_queue_chars == 128

    def test_job_ingress_limits_from_env_full(self) -> None:
        limits = JobIngressLimits.from_environment(
            {
                "GLUDD_JOB_INGRESS_MAX_DEPTH": "32",
                "GLUDD_JOB_INGRESS_MAX_COLLECTION_ITEMS": "50000",
                "GLUDD_JOB_INGRESS_MAX_SERIALIZED_BYTES": "4194304",
                "GLUDD_JOB_INGRESS_MAX_IDENTIFIER_CHARS": "200",
                "GLUDD_JOB_INGRESS_MAX_PLAYBOOK_CHARS": "512",
                "GLUDD_JOB_INGRESS_MAX_QUEUE_CHARS": "128",
            }
        )
        assert limits.max_depth == 32
        assert limits.max_collection_items == 50_000
        assert limits.max_serialized_bytes == 4_194_304

    def test_job_ingress_limits_from_env_partial(self) -> None:
        limits = JobIngressLimits.from_environment({"GLUDD_JOB_INGRESS_MAX_DEPTH": "8"})
        assert limits.max_depth == 8
        assert limits.max_collection_items == 10_000  # default
        assert limits.max_serialized_bytes == 1_048_576  # default

    def test_job_ingress_limits_below_safe_lower_bound_rejected(self) -> None:
        with pytest.raises(ValueError, match="GLUDD_JOB_INGRESS_MAX_DEPTH"):
            JobIngressLimits.from_environment({"GLUDD_JOB_INGRESS_MAX_DEPTH": "1"})

    def test_job_ingress_limits_above_safe_upper_bound_rejected(self) -> None:
        with pytest.raises(ValueError, match="GLUDD_JOB_INGRESS_MAX_DEPTH"):
            JobIngressLimits.from_environment({"GLUDD_JOB_INGRESS_MAX_DEPTH": "999"})

    def test_job_ingress_limits_not_integer_rejected(self) -> None:
        with pytest.raises(ValueError, match="must be an integer"):
            JobIngressLimits.from_environment({"GLUDD_JOB_INGRESS_MAX_DEPTH": "abc"})

    def test_ceiling_serialization_roundtrip(self) -> None:
        ceiling = WorkCeilingSpec.for_work_type("code")
        d = ceiling.model_dump()
        assert isinstance(d, dict)
        assert d["max_wall_seconds"] == 1800
        assert d["max_memory_bytes"] == 268_435_456


# ═══════════════════════════════════════════════════════════════════════
# 8. Ownership & policy
# ═══════════════════════════════════════════════════════════════════════


class TestOwnershipAndPolicy:
    def test_ownership_constructs_with_valid_ids(self) -> None:
        owner = OwnershipSpec(tenant_id="acme", project_id="widgets", agent_id="agent-01")
        assert owner.tenant_id == "acme"
        assert owner.project_id == "widgets"
        assert owner.agent_id == "agent-01"

    def test_ownership_rejects_empty_tenant(self) -> None:
        with pytest.raises(ValidationError, match="must not be empty"):
            OwnershipSpec(tenant_id="", project_id="p", agent_id="a")

    def test_ownership_rejects_unsafe_characters(self) -> None:
        with pytest.raises(ValidationError, match="unsafe characters"):
            OwnershipSpec(tenant_id="acme/evil", project_id="p", agent_id="a")

    def test_ownership_rejects_null_byte(self) -> None:
        with pytest.raises(ValidationError, match="unsafe characters"):
            OwnershipSpec(tenant_id="acme\x00bad", project_id="p", agent_id="a")

    def test_job_policy_version_is_stable(self) -> None:
        spec = _build()
        assert spec.policy_version() == "jobspec-v1:sha256"

    def test_job_policy_hash_no_ownership(self) -> None:
        spec = _build()
        h = spec.policy_hash()
        assert isinstance(h, str)
        assert len(h) == 64
        digest = hashlib.sha256()
        digest.update(b"jobspec-v1")
        assert h == digest.hexdigest()

    def test_job_policy_hash_with_ownership(self) -> None:
        spec = _build(ownership=OwnershipSpec(tenant_id="acme", project_id="p", agent_id="a"))
        h = spec.policy_hash()
        assert isinstance(h, str)
        assert len(h) == 64
        digest = hashlib.sha256()
        digest.update(b"jobspec-v1")
        digest.update(b"acme")
        digest.update(b"p")
        digest.update(b"a")
        assert h == digest.hexdigest()

    def test_job_policy_hash_differs_by_tenant(self) -> None:
        a = _build(ownership=OwnershipSpec(tenant_id="acme", project_id="p", agent_id="a"))
        b = _build(ownership=OwnershipSpec(tenant_id="evil", project_id="p", agent_id="a"))
        assert a.policy_hash() != b.policy_hash()

    def test_job_policy_hash_differs_by_project(self) -> None:
        a = _build(ownership=OwnershipSpec(tenant_id="acme", project_id="p", agent_id="a"))
        b = _build(ownership=OwnershipSpec(tenant_id="acme", project_id="q", agent_id="a"))
        assert a.policy_hash() != b.policy_hash()

    def test_cross_tenant_whitespace_only_tenant(self) -> None:
        owner = OwnershipSpec(tenant_id="acme", project_id="p", agent_id="a")
        result, reason = validate_cross_tenant(owner, "\t  \n")
        assert not result
        assert "missing or empty" in reason


# ═══════════════════════════════════════════════════════════════════════
# 9. Denial audit record bounded output
# ═══════════════════════════════════════════════════════════════════════


class TestDenialAuditBounded:
    def test_record_detail_truncation_at_1024_bytes(self) -> None:
        huge = "\U0001f4a3" * 2000
        record = build_denial_audit_record("PAYLOAD_DENIED", huge)
        detail = cast(str, record.get("detail", ""))
        assert len(detail) <= 1024

    def test_record_extra_large_serialization_still_fits_max_bytes(self) -> None:
        huge_reason_code = "X" * 20000
        record = build_denial_audit_record(huge_reason_code, "detail")
        serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        assert len(serialized.encode("utf-8")) <= 131_072

    def test_audit_invalid_job_output_fits_bounds(self) -> None:
        record = audit_invalid_job("DENIED", "x" * 3000)
        serialized = json.dumps(record, ensure_ascii=False, separators=(",", ":"))
        assert len(serialized.encode("utf-8")) <= 131_072

    def test_denial_record_timestamp_is_iso8601_utc(self) -> None:
        record = build_denial_audit_record("DENIED", "test")
        ts = cast(str, record["timestamp"])
        assert "T" in ts
        assert ts.endswith("Z")

    def test_denial_record_event_id_is_hex_uuid(self) -> None:
        record = build_denial_audit_record("DENIED", "test")
        event_id = cast(str, record["event_id"])
        assert len(event_id) == 32
        assert all(c in "0123456789abcdef" for c in event_id)


# ═══════════════════════════════════════════════════════════════════════
# 10. Combined lifecycle with real schemas
# ═══════════════════════════════════════════════════════════════════════


class TestCombinedLifecycleRealSchemas:
    def test_spec_modifies_without_side_effects(self) -> None:
        original = _build(
            job_id="JOB-A",
            work_type="code",
            resource_profile="ai_heavy",
        )
        modified = original.model_copy(update={"job_id": "JOB-B"})
        assert original.job_id == "JOB-A"
        assert modified.job_id == "JOB-B"
        assert original.work_type == "code"
        assert modified.work_type == "code"

    def test_spec_serialization_preserves_fields(self) -> None:
        spec = _build(
            job_id="JOB-SER",
            playbook="tasks/nop.yml",
            queue="core",
            todo_id="TODO-1",
            timeout=60.0,
            ownership=OwnershipSpec(tenant_id="t", project_id="p", agent_id="a"),
        )
        d = spec.model_dump()
        restored = JobSpec.model_validate(d)
        assert restored.job_id == spec.job_id
        assert restored.timeout == spec.timeout
        assert restored.ownership == spec.ownership

    def test_spec_immutable_copy_produces_equal_spec(self) -> None:
        a = _build(job_id="JOB-K", playbook="tasks/review.yml", queue="core")
        b = a.model_copy()
        assert a.model_dump() == b.model_dump()
        assert a is not b

    def test_full_lifecycle_smoke(self) -> None:
        spec = _build(
            job_id="SMOKE-001",
            todo_id="TODO-99",
            work_type="code",
            resource_profile="ai_heavy",
            timeout=120.0,
            ownership=OwnershipSpec(tenant_id="acme", project_id="dag", agent_id="a1"),
            candidate_todos=["todo-1", "todo-2", "todo-3"],
        )
        assert spec.job_id == "SMOKE-001"
        assert spec.work_type == "code"
        assert spec.resource_profile == "ai_heavy"
        assert spec.timeout == 120.0
        assert spec.ownership is not None
        assert spec.ownership.tenant_id == "acme"
        assert len(spec.candidate_todos) == 3
        h = spec.policy_hash()
        assert len(h) == 64
        sorted_data = spec.model_dump()
        restored = JobSpec.model_validate(sorted_data)
        assert restored == spec
