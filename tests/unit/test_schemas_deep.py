"""Deep schema model validation tests — Pydantic models, field constraints,
JSON serialization, database mapping, and migration compatibility."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from general_ludd.schemas.benchmark import (
    BenchmarkResult,
    BenchmarkScores,
    PromptProfile,
    RoutingCandidate,
    RoutingDecision,
    TaskType,
)
from general_ludd.schemas.deployment import DeploymentRecord
from general_ludd.schemas.job import (
    JobSpec,
    OwnershipSpec,
    WorkCeilingSpec,
    validate_cross_tenant,
)
from general_ludd.schemas.quality_gate import (
    AnsibleTestGate,
    EnforcementGate,
    MoleculeQualityGate,
    PythonQualityGate,
    QualityGateConfig,
)
from general_ludd.schemas.queue import INITIAL_QUEUES, Queue
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_definition import TaskDefinition
from general_ludd.schemas.task_return import TaskReturn, TaskReturnStatus
from general_ludd.schemas.todo import (
    VALID_TRANSITIONS,
    Todo,
    TodoStatus,
    WorkType,
    validate_transition,
)

# ── Todo ────────────────────────────────────────────────────────────────────


class TestTodoValidation:
    def test_minimal_creation(self):
        t = Todo(title="Fix login bug")
        assert t.title == "Fix login bug"
        assert t.todo_id.startswith("TODO-")
        assert t.status == TodoStatus.BACKLOG
        assert t.priority == 0
        assert t.queue == "core"
        assert t.version == 1

    def test_title_required(self):
        with pytest.raises(ValueError, match="field must not be empty"):
            Todo(title="")

    def test_title_stripped(self):
        t = Todo(title="  Fix bug  ")
        assert t.title == "Fix bug"

    def test_priority_non_negative(self):
        with pytest.raises(ValueError, match="priority must be non-negative"):
            Todo(title="x", priority=-1)

    def test_priority_max_1000(self):
        with pytest.raises(ValueError, match="priority must not exceed 1000"):
            Todo(title="x", priority=1001)

    def test_confidence_range(self):
        Todo(title="x", confidence=0.0)
        Todo(title="x", confidence=1.0)
        Todo(title="x", confidence=0.5)
        with pytest.raises(ValueError, match="confidence must be between"):
            Todo(title="x", confidence=-0.1)
        with pytest.raises(ValueError, match="confidence must be between"):
            Todo(title="x", confidence=1.1)

    def test_version_minimum(self):
        with pytest.raises(ValueError, match="version must be at least 1"):
            Todo(title="x", version=0)

    def test_run_count_non_negative(self):
        with pytest.raises(ValueError, match="run_count must be non-negative"):
            Todo(title="x", run_count=-1)

    def test_max_runs_positive_when_set(self):
        Todo(title="x", max_runs=1)
        Todo(title="x", max_runs=None)
        with pytest.raises(ValueError, match="max_runs must be at least 1"):
            Todo(title="x", max_runs=0)

    def test_cron_well_formed(self):
        Todo(title="x", cron="0 0 * * *")
        Todo(title="x", cron=None)
        Todo(title="x", cron="")
        with pytest.raises(ValueError, match="cron must be a 5-field expression"):
            Todo(title="x", cron="* * *")

    def test_completed_at_only_when_complete(self):
        past = datetime(2025, 1, 1, tzinfo=UTC)
        with pytest.raises(ValueError, match="completed_at can only be set"):
            Todo(title="x", status=TodoStatus.ACTIVE, completed_at=past)

    def test_acceptance_criteria_max_length(self):
        criteria = [f"criterion_{i}" for i in range(21)]
        with pytest.raises(ValueError):
            Todo(title="x", acceptance_criteria=criteria)

    def test_todo_id_auto_generated(self):
        t1 = Todo(title="a")
        t2 = Todo(title="b")
        assert t1.todo_id != t2.todo_id
        assert len(t1.todo_id) == 13  # "TODO-" + 8 hex

    def test_default_risk_level(self):
        t = Todo(title="x")
        assert t.risk_level.value == "low"

    def test_default_work_type(self):
        t = Todo(title="x")
        assert t.work_type == WorkType.UNKNOWN


class TestTodoStateMachine:
    def test_valid_transitions(self):
        assert TodoStatus.QUEUED in VALID_TRANSITIONS[TodoStatus.BACKLOG]
        assert TodoStatus.ACTIVE in VALID_TRANSITIONS[TodoStatus.QUEUED]
        assert TodoStatus.COMPLETE in VALID_TRANSITIONS[TodoStatus.REVIEWING_RETURN]
        assert TodoStatus.QUEUED in VALID_TRANSITIONS[TodoStatus.NEEDS_MORE_WORK]

    def test_invalid_transition_returns_false(self):
        assert not validate_transition(TodoStatus.COMPLETE, TodoStatus.QUEUED)
        assert not validate_transition(TodoStatus.CANCELLED, TodoStatus.ACTIVE)

    def test_terminal_states_have_no_exits(self):
        assert VALID_TRANSITIONS[TodoStatus.COMPLETE] == set()
        assert VALID_TRANSITIONS[TodoStatus.CANCELLED] == set()

    def test_transition_to_updates_status(self):
        t = Todo(title="x")
        t.transition_to(TodoStatus.QUEUED)
        assert t.status == TodoStatus.QUEUED

    def test_transition_to_marks_completed_at(self):
        t = Todo(title="x")
        t.transition_to(TodoStatus.QUEUED)
        t.transition_to(TodoStatus.ACTIVE)
        t.transition_to(TodoStatus.AWAITING_RESULT)
        t.transition_to(TodoStatus.REVIEWING_RETURN)
        t.transition_to(TodoStatus.COMPLETE)
        assert t.status == TodoStatus.COMPLETE
        assert t.completed_at is not None

    def test_invalid_transition_raises(self):
        t = Todo(title="x")
        with pytest.raises(ValueError, match="Invalid transition"):
            t.transition_to(TodoStatus.ACTIVE)


class TestTodoSerialization:
    def test_model_dump_round_trip(self):
        t = Todo(title="Test", priority=5, tags=["urgent"], confidence=0.8)
        data = t.model_dump()
        restored = Todo.model_validate(data)
        assert restored.title == t.title
        assert restored.priority == t.priority
        assert restored.tags == t.tags
        assert restored.confidence == t.confidence

    def test_model_dump_json_round_trip(self):
        t = Todo(
            title="Test",
            status=TodoStatus.QUEUED,
            work_type=WorkType.CODE,
            created_at=datetime(2025, 6, 1, tzinfo=UTC),
        )
        json_str = t.model_dump_json()
        restored = Todo.model_validate_json(json_str)
        assert restored.title == "Test"
        assert restored.status == TodoStatus.QUEUED
        assert restored.work_type == WorkType.CODE

    def test_model_dump_json_includes_null_by_default(self):
        t = Todo(title="x")
        json_str = t.model_dump_json()
        assert '"project_id":null' in json_str

    def test_model_dump_json_exclude_none(self):
        t = Todo(title="x")
        json_str = t.model_dump_json(exclude_none=True)
        assert "project_id" not in json_str


# ── TaskReturn ─────────────────────────────────────────────────────────────


class TestTaskReturnValidation:
    def test_minimal_creation(self):
        tr = TaskReturn(return_id="r1", job_id="j1", playbook="noop.yml", queue="core")
        assert tr.status == TaskReturnStatus.CREATED
        assert tr.exit_code == 0
        assert tr.artifacts == []
        assert tr.schema_version == 1

    def test_return_id_required(self):
        with pytest.raises(ValueError, match="field must not be empty"):
            TaskReturn(return_id="", job_id="j1", playbook="p", queue="q")

    def test_custom_status(self):
        tr = TaskReturn(
            return_id="r1",
            job_id="j1",
            playbook="p",
            queue="q",
            status=TaskReturnStatus.ARCHIVED,
        )
        assert tr.status == TaskReturnStatus.ARCHIVED

    def test_created_at_is_utc_aware(self):
        tr = TaskReturn(return_id="r1", job_id="j1", playbook="p", queue="q")
        assert tr.created_at.tzinfo is not None
        assert tr.created_at.utcoffset() == timedelta(0)


class TestTaskReturnStatusEnum:
    def test_all_members(self):
        assert TaskReturnStatus.CREATED == "created"
        assert TaskReturnStatus.CLAIMED_FOR_REVIEW == "claimed_for_review"
        assert TaskReturnStatus.REVIEWED == "reviewed"
        assert TaskReturnStatus.ARCHIVED == "archived"

    def test_from_string(self):
        assert TaskReturnStatus("created") == TaskReturnStatus.CREATED
        assert TaskReturnStatus("archived") == TaskReturnStatus.ARCHIVED


# ── TaskDecision ───────────────────────────────────────────────────────────


class TestTaskDecisionValidation:
    def test_valid_decision(self):
        d = TaskDecision(return_id="r1", decision="complete", confidence=0.9)
        assert d.decision == "complete"

    def test_invalid_decision_raises(self):
        with pytest.raises(ValueError, match="Invalid decision"):
            TaskDecision(return_id="r1", decision="bogus")

    def test_confidence_out_of_range(self):
        with pytest.raises(ValueError, match="confidence must be between"):
            TaskDecision(return_id="r1", decision="complete", confidence=1.5)
        with pytest.raises(ValueError, match="confidence must be between"):
            TaskDecision(return_id="r1", decision="complete", confidence=-0.5)

    def test_all_valid_decisions_construct(self):
        for d in TaskDecision.valid_decisions():
            td = TaskDecision(return_id="r1", decision=d)
            assert td.decision == d

    def test_return_id_required(self):
        with pytest.raises(ValueError, match="return_id must not be empty"):
            TaskDecision(return_id="", decision="complete")


# ── TaskDefinition ─────────────────────────────────────────────────────────


class TestTaskDefinitionValidation:
    def test_minimal_creation(self):
        td = TaskDefinition(name="refactor_auth")
        assert td.name == "refactor_auth"
        assert td.target_agent == "build"
        assert td.queue == "core"
        assert td.work_type == "code"
        assert td.priority == 0

    def test_name_required(self):
        with pytest.raises(ValueError, match="name must not be empty"):
            TaskDefinition(name="")

    def test_priority_range(self):
        TaskDefinition(name="x", priority=0)
        TaskDefinition(name="x", priority=1000)
        with pytest.raises(ValueError, match="priority must be non-negative"):
            TaskDefinition(name="x", priority=-1)
        with pytest.raises(ValueError, match="priority must not exceed"):
            TaskDefinition(name="x", priority=1001)

    def test_to_todo_conversion(self):
        td = TaskDefinition(
            name="Fix auth",
            description="Fix login bug",
            target_agent="coder",
            queue="core",
            work_type="code",
            priority=10,
            tags=["urgent"],
            acceptance_criteria=["Login works"],
        )
        todo = td.to_todo()
        assert todo.title == "Fix auth"
        assert todo.description == "Fix login bug"
        assert todo.assigned_agent == "coder"
        assert todo.queue == "core"
        assert todo.work_type == WorkType.CODE
        assert todo.priority == 10
        assert "urgent" in todo.tags
        assert "Login works" in todo.acceptance_criteria


# ── JobSpec ─────────────────────────────────────────────────────────────────


class TestJobSpecValidation:
    def test_minimal_creation(self):
        j = JobSpec(job_id="job-1", playbook="noop.yml", queue="core")
        assert j.job_id == "job-1"
        assert j.playbook == "noop.yml"
        assert j.queue == "core"
        assert j.work_type == "unknown"
        assert j.resource_profile == "low_resource"
        assert j.budget_context == {}

    def test_job_id_pattern(self):
        JobSpec(job_id="abc", playbook="a.yml", queue="q")
        JobSpec(job_id="a-b_c", playbook="a.yml", queue="q")
        with pytest.raises(ValueError, match="job_id must contain only"):
            JobSpec(job_id="has spaces", playbook="a.yml", queue="q")

    def test_job_id_empty(self):
        with pytest.raises(ValueError, match="job_id must not be empty"):
            JobSpec(job_id="", playbook="a.yml", queue="q")

    def test_playbook_safe_path(self):
        JobSpec(job_id="j1", playbook="foo/bar.yml", queue="q")
        with pytest.raises(ValueError, match="playbook must be a safe relative"):
            JobSpec(job_id="j1", playbook="/absolute.yml", queue="q")
        with pytest.raises(ValueError, match="playbook must contain only safe"):
            JobSpec(job_id="j1", playbook="foo/../bar.yml", queue="q")

    def test_queue_slug_pattern(self):
        JobSpec(job_id="j1", playbook="a.yml", queue="core")
        with pytest.raises(ValueError, match="queue must be an identifier-like"):
            JobSpec(job_id="j1", playbook="a.yml", queue="he||o")

    def test_timeout_validation(self):
        JobSpec(job_id="j1", playbook="a.yml", queue="q", timeout=30)
        JobSpec(job_id="j1", playbook="a.yml", queue="q", timeout=None)
        with pytest.raises(ValueError, match="timeout must be positive"):
            JobSpec(job_id="j1", playbook="a.yml", queue="q", timeout=-5)

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValueError):
            JobSpec.model_validate({"job_id": "j1", "playbook": "a.yml", "queue": "q", "bogus": 42})

    def test_policy_version(self):
        j = JobSpec(job_id="j1", playbook="a.yml", queue="q")
        assert "jobspec-v1" in j.policy_version()

    def test_policy_hash(self):
        j = JobSpec(job_id="j1", playbook="a.yml", queue="q")
        h = j.policy_hash()
        assert isinstance(h, str)
        assert len(h) == 64

    def test_with_ownership(self):
        o = OwnershipSpec(tenant_id="acme", project_id="proj1", agent_id="agent-1")
        j = JobSpec(job_id="j1", playbook="a.yml", queue="q", ownership=o)
        assert j.ownership is not None
        assert j.ownership.tenant_id == "acme"
        assert j.ownership.project_id == "proj1"
        h = j.policy_hash()
        assert len(h) == 64


class TestOwnershipSpec:
    def test_valid_creation(self):
        o = OwnershipSpec(tenant_id="acme", project_id="proj", agent_id="agent-1")
        assert o.tenant_id == "acme"

    def test_invalid_id(self):
        with pytest.raises(ValueError, match="ownership identifier must not contain"):
            OwnershipSpec(tenant_id="a/b", project_id="p", agent_id="a")

    def test_empty_id(self):
        with pytest.raises(ValueError, match="ownership identifier"):
            OwnershipSpec(tenant_id="", project_id="p", agent_id="a")

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValueError):
            OwnershipSpec.model_validate({"tenant_id": "a", "project_id": "p", "agent_id": "a", "bogus": 1})


class TestValidateCrossTenant:
    def test_matching_tenant(self):
        o = OwnershipSpec(tenant_id="acme", project_id="p", agent_id="a")
        ok, msg = validate_cross_tenant(o, "acme")
        assert ok
        assert msg == "ok"

    def test_mismatched_tenant(self):
        o = OwnershipSpec(tenant_id="acme", project_id="p", agent_id="a")
        ok, msg = validate_cross_tenant(o, "evilcorp")
        assert not ok
        assert "MISMATCH" in msg

    def test_missing_ownership(self):
        ok, _msg = validate_cross_tenant(None, "acme")
        assert not ok

    def test_empty_request_tenant(self):
        ok, _msg = validate_cross_tenant(None, "")
        assert not ok


class TestWorkCeilingSpec:
    def test_defaults(self):
        w = WorkCeilingSpec()
        assert w.max_wall_seconds == 3600
        assert w.max_cpu_seconds == 900
        assert w.max_memory_bytes == 536_870_912

    def test_for_work_type_code(self):
        w = WorkCeilingSpec.for_work_type("code")
        assert w.max_wall_seconds == 1800
        assert w.max_memory_bytes == 268_435_456

    def test_for_work_type_audit(self):
        w = WorkCeilingSpec.for_work_type("audit")
        assert w.max_wall_seconds == 7200
        assert w.max_memory_bytes == 1_073_741_824

    def test_for_unknown_work_type_returns_default(self):
        w = WorkCeilingSpec.for_work_type("bogus")
        assert w.max_wall_seconds == 3600

    def test_max_wall_seconds_must_be_positive(self):
        with pytest.raises(ValueError):
            WorkCeilingSpec(max_wall_seconds=0)

    def test_extra_fields_forbidden(self):
        with pytest.raises(ValueError):
            WorkCeilingSpec.model_validate({"bogus": 1})


# ── Queue ───────────────────────────────────────────────────────────────────


class TestQueueValidation:
    def test_valid_creation(self):
        q = Queue(queue_name="my-queue")
        assert q.queue_name == "my-queue"
        assert q.queue_enabled is True
        assert q.priority_weight == 100
        assert q.hard_cap == 10
        assert q.soft_cap == 5

    def test_queue_name_pattern(self):
        Queue(queue_name="core")
        Queue(queue_name="my_queue-2")
        with pytest.raises(ValueError, match="queue_name must match"):
            Queue(queue_name="Bad Name!")

    def test_queue_name_empty(self):
        with pytest.raises(ValueError, match="queue_name must not be empty"):
            Queue(queue_name="")

    def test_hard_cap_minimum(self):
        with pytest.raises(ValueError, match="hard_cap must be at least 1"):
            Queue(queue_name="q", hard_cap=0)

    def test_max_error_rate_range(self):
        Queue(queue_name="q", max_error_rate=0.0)
        Queue(queue_name="q", max_error_rate=1.0)
        with pytest.raises(ValueError, match="max_error_rate must be between"):
            Queue(queue_name="q", max_error_rate=1.5)

    def test_soft_cap_exceeds_hard_cap(self):
        with pytest.raises(ValueError, match="soft_cap must not exceed hard_cap"):
            Queue(queue_name="q", hard_cap=5, soft_cap=10)

    def test_soft_cap_equals_hard_cap_is_ok(self):
        q = Queue(queue_name="q", hard_cap=5, soft_cap=5)
        assert q.soft_cap == 5


class TestInitialQueues:
    def test_minimum_count(self):
        assert len(INITIAL_QUEUES) >= 12

    def test_all_queues_unique_names(self):
        names = [q.queue_name for q in INITIAL_QUEUES]
        assert len(names) == len(set(names))

    def test_manual_hold_disabled(self):
        q = next(q for q in INITIAL_QUEUES if q.queue_name == "manual_hold")
        assert q.queue_enabled is False

    def test_core_queue_exists(self):
        q = next(q for q in INITIAL_QUEUES if q.queue_name == "core")
        assert q.queue_enabled is True

    def test_all_queues_valid(self):
        for q in INITIAL_QUEUES:
            json_str = q.model_dump_json()
            restored = Queue.model_validate_json(json_str)
            assert restored.queue_name == q.queue_name


# ── Benchmark ───────────────────────────────────────────────────────────────


class TestBenchmarkScores:
    def test_composite_score(self):
        s = BenchmarkScores(
            completion_score=0.8,
            code_quality_score=0.6,
            instruction_adherence_score=0.7,
            token_efficiency_score=0.9,
        )
        expected = 0.8 * 0.35 + 0.6 * 0.25 + 0.7 * 0.25 + 0.9 * 0.15
        assert s.composite_score == pytest.approx(expected)

    def test_composite_score_bounds(self):
        s = BenchmarkScores(
            completion_score=1.0,
            code_quality_score=1.0,
            instruction_adherence_score=1.0,
            token_efficiency_score=1.0,
        )
        assert s.composite_score == pytest.approx(1.0)

    def test_composite_score_zero(self):
        s = BenchmarkScores(
            completion_score=0.0,
            code_quality_score=0.0,
            instruction_adherence_score=0.0,
            token_efficiency_score=0.0,
        )
        assert s.composite_score == pytest.approx(0.0)

    def test_scores_must_be_in_range(self):
        with pytest.raises(ValueError):
            BenchmarkScores(
                completion_score=1.5,
                code_quality_score=0.5,
                instruction_adherence_score=0.5,
                token_efficiency_score=0.5,
            )
        with pytest.raises(ValueError):
            BenchmarkScores(
                completion_score=-0.1,
                code_quality_score=0.5,
                instruction_adherence_score=0.5,
                token_efficiency_score=0.5,
            )


class TestBenchmarkResult:
    def test_minimal_creation(self):
        scores = BenchmarkScores(
            completion_score=0.8,
            code_quality_score=0.6,
            instruction_adherence_score=0.7,
            token_efficiency_score=0.9,
        )
        r = BenchmarkResult(model_profile_id="sonnet", task_type=TaskType.BUG_FIX, scores=scores)
        assert r.model_profile_id == "sonnet"
        assert r.task_type == TaskType.BUG_FIX
        assert r.success is False
        assert r.time_seconds == 0.0

    def test_negative_time_rejected(self):
        scores = BenchmarkScores(
            completion_score=0.5,
            code_quality_score=0.5,
            instruction_adherence_score=0.5,
            token_efficiency_score=0.5,
        )
        with pytest.raises(ValueError, match="must be non-negative"):
            BenchmarkResult(
                model_profile_id="m",
                task_type=TaskType.FEATURE,
                scores=scores,
                time_seconds=-1,
            )

    def test_negative_tokens_rejected(self):
        scores = BenchmarkScores(
            completion_score=0.5,
            code_quality_score=0.5,
            instruction_adherence_score=0.5,
            token_efficiency_score=0.5,
        )
        with pytest.raises(ValueError, match="must be non-negative"):
            BenchmarkResult(
                model_profile_id="m",
                task_type=TaskType.FEATURE,
                scores=scores,
                input_tokens=-1,
            )


class TestPromptProfile:
    def test_valid_creation(self):
        p = PromptProfile(
            id="p1",
            name="Default",
            source="builtin",
            prompt_text="You are a helpful assistant.",
        )
        assert p.id == "p1"
        assert p.version == "latest"

    def test_id_required(self):
        with pytest.raises(ValueError, match="field must not be empty"):
            PromptProfile(id="", name="x", source="x", prompt_text="x")

    def test_prompt_text_required(self):
        with pytest.raises(ValueError, match="field must not be empty"):
            PromptProfile(id="a", name="x", source="x", prompt_text="")


class TestRoutingCandidate:
    def test_valid_creation(self):
        rc = RoutingCandidate(
            prompt_profile_id="p1",
            model_profile_id="m1",
            composite_score=0.85,
            avg_cost_usd=0.01,
            sample_count=5,
            task_type=TaskType.BUG_FIX,
        )
        assert rc.composite_score == 0.85

    def test_score_range(self):
        with pytest.raises(ValueError, match="composite_score must be between"):
            RoutingCandidate(
                prompt_profile_id="p1",
                model_profile_id="m1",
                composite_score=1.5,
                avg_cost_usd=0.01,
                sample_count=5,
                task_type=TaskType.BUG_FIX,
            )

    def test_avg_cost_non_negative(self):
        with pytest.raises(ValueError, match="avg_cost_usd must be non-negative"):
            RoutingCandidate(
                prompt_profile_id="p1",
                model_profile_id="m1",
                composite_score=0.5,
                avg_cost_usd=-0.01,
                sample_count=5,
                task_type=TaskType.BUG_FIX,
            )

    def test_sample_count_positive(self):
        with pytest.raises(ValueError, match="sample_count must be at least 1"):
            RoutingCandidate(
                prompt_profile_id="p1",
                model_profile_id="m1",
                composite_score=0.5,
                avg_cost_usd=0.01,
                sample_count=0,
                task_type=TaskType.BUG_FIX,
            )


class TestRoutingDecision:
    def test_valid_creation(self):
        rd = RoutingDecision(
            selected_prompt_profile_id="p1",
            selected_model_profile_id="m1",
            composite_score=0.9,
            estimated_cost_usd=0.02,
            sample_count=10,
        )
        assert rd.composite_score == 0.9

    def test_score_range(self):
        with pytest.raises(ValueError, match="composite_score must be between"):
            RoutingDecision(
                selected_prompt_profile_id="p1",
                selected_model_profile_id="m1",
                composite_score=2.0,
                estimated_cost_usd=0.02,
                sample_count=10,
            )

    def test_cost_non_negative(self):
        with pytest.raises(ValueError, match="estimated_cost_usd must be non-negative"):
            RoutingDecision(
                selected_prompt_profile_id="p1",
                selected_model_profile_id="m1",
                composite_score=0.5,
                estimated_cost_usd=-1,
                sample_count=10,
            )

    def test_sample_count_non_negative(self):
        with pytest.raises(ValueError, match="sample_count must be non-negative"):
            RoutingDecision(
                selected_prompt_profile_id="p1",
                selected_model_profile_id="m1",
                composite_score=0.5,
                estimated_cost_usd=0.01,
                sample_count=-1,
            )

    def test_fallback_default(self):
        rd = RoutingDecision(
            selected_prompt_profile_id="p1",
            selected_model_profile_id="m1",
            composite_score=0.5,
            estimated_cost_usd=0.01,
            sample_count=10,
        )
        assert rd.fallback is False


# ── DeploymentRecord ────────────────────────────────────────────────────────


class TestDeploymentRecord:
    def test_minimal_creation(self):
        d = DeploymentRecord(instance_id="i-abc", working_dir="/tmp/deploy")
        assert d.instance_id == "i-abc"
        assert d.state == "running"
        assert d.provider == ""

    def test_model_dump_round_trip(self):
        d = DeploymentRecord(instance_id="i-1", working_dir="/tmp/d", provider="aws", state="running")
        data = d.model_dump()
        restored = DeploymentRecord.model_validate(data)
        assert restored.instance_id == "i-1"
        assert restored.state == "running"

    def test_model_dump_json(self):
        d = DeploymentRecord(instance_id="i-1", working_dir="/tmp/d")
        json_str = d.model_dump_json()
        assert "i-1" in json_str


# ── QualityGateConfig ──────────────────────────────────────────────────────


class TestQualityGateConfig:
    def test_defaults(self):
        cfg = QualityGateConfig()
        assert cfg.enabled is True
        assert cfg.python.enabled is True
        assert cfg.molecule.enabled is True

    def test_python_gate_coverage_range(self):
        with pytest.raises(ValueError, match="coverage percent must be between"):
            PythonQualityGate(line_coverage_min_percent=150)

    def test_molecule_gate_coverage_range(self):
        with pytest.raises(ValueError, match="coverage percent must be between"):
            MoleculeQualityGate(coverage_min_percent=-1)

    def test_molecule_exemption_max_age_positive(self):
        with pytest.raises(ValueError, match="exemption_max_age_days must be at least 1"):
            MoleculeQualityGate(exemption_max_age_days=0)

    def test_enforcement_gate_defaults(self):
        eg = EnforcementGate()
        assert eg.fail_completion_when_below_gate is True
        assert eg.block_commit is True
        assert eg.block_push is True

    def test_nested_round_trip(self):
        cfg = QualityGateConfig(
            enabled=True,
            python=PythonQualityGate(line_coverage_min_percent=85.0),
            molecule=MoleculeQualityGate(coverage_min_percent=100.0),
        )
        json_str = cfg.model_dump_json()
        restored = QualityGateConfig.model_validate_json(json_str)
        assert restored.python.line_coverage_min_percent == 85.0
        assert restored.molecule.coverage_min_percent == 100.0

    def test_ansible_test_gate(self):
        atg = AnsibleTestGate()
        assert atg.enabled_for_custom_collection_plugins is True


# ── Database Mapping ────────────────────────────────────────────────────────


class TestSchemaDBMapping:
    """Verify Pydantic schema field names are compatible with corresponding
    SQLAlchemy model column names for models that map 1:1."""

    def test_todo_schema_fields_exist_in_todo_model(self):
        schema_fields = {f for f in Todo.model_fields if not f.startswith("_")}
        essential = {
            "todo_id",
            "title",
            "status",
            "priority",
            "queue",
            "work_type",
            "risk_level",
            "resource_profile",
            "created_at",
            "updated_at",
            "completed_at",
            "version",
        }
        assert essential <= schema_fields

    def test_task_return_schema_fields_map_to_db(self):
        schema_fields = {f for f in TaskReturn.model_fields}
        db_columns = {
            "return_id",
            "todo_id",
            "job_id",
            "playbook",
            "queue",
            "work_type",
            "status",
            "exit_code",
            "artifacts",
            "logs_ref",
            "diff_ref",
            "test_results_ref",
            "molecule_results_ref",
            "coverage_results_ref",
            "model_usage_ref",
            "created_at",
            "schema_version",
        }
        assert db_columns <= schema_fields

    def test_queue_schema_fields_map_to_db(self):
        schema_fields = {f for f in Queue.model_fields}
        db_columns = {
            "queue_name",
            "queue_enabled",
            "priority_weight",
            "resource_profile",
            "hard_cap",
            "soft_cap",
            "pid_group",
            "allowed_playbooks",
            "allowed_model_profiles",
            "allowed_prompt_profiles",
            "required_molecule_coverage_profile",
            "max_error_rate",
            "retry_policy",
        }
        assert db_columns <= schema_fields

    def test_deployment_record_schema_fields_map_to_db(self):
        schema_fields = {f for f in DeploymentRecord.model_fields}
        db_columns = {
            "instance_id",
            "working_dir",
            "provider",
            "model_name",
            "state",
            "ip_address",
            "endpoint_url",
            "created_at",
        }
        assert db_columns <= schema_fields

    def test_benchmark_result_schema_fields_map_to_db(self):
        schema_fields = {f for f in BenchmarkResult.model_fields}
        db_columns = {
            "id",
            "prompt_profile_id",
            "model_profile_id",
            "task_type",
            "task_description",
            "time_seconds",
            "input_tokens",
            "output_tokens",
            "cost_usd",
            "success",
            "error_message",
            "raw_output",
            "task_role",
            "created_at",
        }
        assert db_columns <= schema_fields

    def test_task_decision_schema_fields_map_to_db(self):
        schema_fields = {f for f in TaskDecision.model_fields}
        db_columns = {
            "return_id",
            "matched_todo_id",
            "decision",
            "confidence",
            "evidence_refs",
            "todo_updates",
            "child_todos",
            "validation_requests",
            "git_requests",
            "audit_notes",
            "policy_flags",
            "created_at",
        }
        assert db_columns <= schema_fields

    def test_all_task_return_status_values_valid_text(self):
        for s in TaskReturnStatus:
            assert isinstance(s.value, str)
            assert len(s.value) > 0

    def test_all_todo_status_values_valid_text(self):
        for s in TodoStatus:
            assert isinstance(s.value, str)
            assert len(s.value) > 0

    def test_all_work_type_values_valid_text(self):
        for s in WorkType:
            assert isinstance(s.value, str)
            assert len(s.value) > 0

    def test_all_task_type_values_valid_text(self):
        for s in TaskType:
            assert isinstance(s.value, str)
            assert len(s.value) > 0


# ── Migration Compatibility ─────────────────────────────────────────────────


class TestMigrationCompatibility:
    """Ensure schema changes wouldn't break existing migrations."""

    def test_todo_has_required_columns(self):
        t = Todo(title="x")
        data = t.model_dump()
        for col in ("todo_id", "title", "status", "version", "created_at", "updated_at"):
            assert col in data

    def test_task_return_has_required_columns(self):
        tr = TaskReturn(return_id="r1", job_id="j1", playbook="p", queue="q")
        data = tr.model_dump()
        required = (
            "return_id",
            "job_id",
            "playbook",
            "queue",
            "status",
            "exit_code",
            "created_at",
            "schema_version",
        )
        for col in required:
            assert col in data

    def test_todo_scheduled_at_nullable(self):
        t = Todo(title="x", scheduled_at=None)
        assert t.scheduled_at is None

    def test_todo_cron_nullable(self):
        t = Todo(title="x", cron=None)
        assert t.cron is None

    def test_task_return_todo_id_nullable(self):
        tr = TaskReturn(return_id="r1", job_id="j1", playbook="p", queue="q", todo_id=None)
        assert tr.todo_id is None

    def test_deployment_record_fields_match_migration(self):
        d = DeploymentRecord(instance_id="i1", working_dir="/tmp/d")
        data = d.model_dump()
        for col in ("instance_id", "working_dir", "provider", "model_name", "state", "created_at"):
            assert col in data

    def test_benchmark_result_task_role_nullable(self):
        scores = BenchmarkScores(
            completion_score=0.5,
            code_quality_score=0.5,
            instruction_adherence_score=0.5,
            token_efficiency_score=0.5,
        )
        r = BenchmarkResult(
            model_profile_id="m",
            task_type=TaskType.FEATURE,
            scores=scores,
            task_role=None,
        )
        assert r.task_role is None

    def test_todo_acceptance_criteria_is_list(self):
        t = Todo(title="x", acceptance_criteria=["a", "b"])
        assert isinstance(t.acceptance_criteria, list)
        assert len(t.acceptance_criteria) == 2

    def test_queue_allowed_playbooks_is_list(self):
        q = Queue(queue_name="q", allowed_playbooks=["a.yml"])
        assert isinstance(q.allowed_playbooks, list)
        assert "a.yml" in q.allowed_playbooks

    def test_json_list_fields_serialize_as_arrays(self):
        t = Todo(title="x", tags=["urgent", "bug"], acceptance_criteria=["Must pass"])
        data = json.loads(t.model_dump_json())
        assert isinstance(data["tags"], list)
        assert isinstance(data["acceptance_criteria"], list)
