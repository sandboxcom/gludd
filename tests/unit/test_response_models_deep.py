"""Deep response model shape tests — every Pydantic response model serializes
correctly, required fields are present, optional fields omitted correctly,
datetime format is consistent, and enum values are valid."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from general_ludd.schemas.benchmark import (
    BenchmarkResult,
    BenchmarkScores,
    PromptProfile,
    RoutingCandidate,
    RoutingDecision,
    TaskRole,
    TaskType,
)
from general_ludd.schemas.deployment import DeploymentRecord
from general_ludd.schemas.job import (
    JobSpec,
    OwnershipSpec,
    WorkCeilingSpec,
)
from general_ludd.schemas.quality_gate import (
    AnsibleTestGate,
    EnforcementGate,
    MoleculeQualityGate,
    PythonQualityGate,
    QualityGateConfig,
)
from general_ludd.schemas.queue import Queue
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_definition import TaskDefinition
from general_ludd.schemas.task_return import TaskReturn, TaskReturnStatus
from general_ludd.schemas.todo import (
    ResourceProfile,
    RiskLevel,
    Todo,
    TodoStatus,
    WorkType,
)

# ── Helpers ──────────────────────────────────────────────────────────────────

_MODELS = [
    Todo(title="x"),
    TaskReturn(return_id="r1", job_id="j1", playbook="p.yml", queue="core"),
    TaskDecision(return_id="r1", decision="complete"),
    TaskDefinition(name="def1"),
    JobSpec(job_id="j1", playbook="p.yml", queue="core"),
    OwnershipSpec(tenant_id="acme", project_id="proj", agent_id="a1"),
    WorkCeilingSpec(),
    QualityGateConfig(),
    PythonQualityGate(),
    MoleculeQualityGate(),
    AnsibleTestGate(),
    EnforcementGate(),
    PromptProfile(id="p1", name="Default", source="builtin", prompt_text="hello"),
    BenchmarkScores(
        completion_score=0.5,
        code_quality_score=0.5,
        instruction_adherence_score=0.5,
        token_efficiency_score=0.5,
    ),
    BenchmarkResult(
        model_profile_id="m1",
        task_type=TaskType.BUG_FIX,
        scores=BenchmarkScores(
            completion_score=0.8,
            code_quality_score=0.6,
            instruction_adherence_score=0.7,
            token_efficiency_score=0.9,
        ),
    ),
    RoutingCandidate(
        prompt_profile_id=None,
        model_profile_id="m1",
        composite_score=0.5,
        avg_cost_usd=0.01,
        sample_count=5,
        task_type=TaskType.BUG_FIX,
    ),
    RoutingDecision(
        selected_prompt_profile_id="p1",
        selected_model_profile_id="m1",
        composite_score=0.5,
        estimated_cost_usd=0.01,
        sample_count=10,
    ),
    DeploymentRecord(instance_id="i-1", working_dir="/tmp/d"),
    Queue(queue_name="core"),
]

_MODEL_NAMES = [type(m).__name__ for m in _MODELS]


# ── Serialization ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("model", _MODELS, ids=_MODEL_NAMES)
def test_every_model_dumps_to_json(model):
    """Every response model produces valid JSON via model_dump_json."""
    raw = model.model_dump_json()
    assert isinstance(raw, str)
    data = json.loads(raw)
    assert isinstance(data, dict)


@pytest.mark.parametrize("model", _MODELS, ids=_MODEL_NAMES)
def test_every_model_dump_round_trips(model):
    """Every response model survives model_dump → model_validate round-trip."""
    cls = type(model)
    data = model.model_dump()
    restored = cls.model_validate(data)
    assert type(restored) is cls


@pytest.mark.parametrize("model", _MODELS, ids=_MODEL_NAMES)
def test_every_model_json_round_trips(model):
    """Every response model survives model_dump_json → model_validate_json round-trip."""
    cls = type(model)
    json_str = model.model_dump_json()
    restored = cls.model_validate_json(json_str)
    assert type(restored) is cls


# ── Required / optional field presence ───────────────────────────────────────


class TestRequiredFieldsPresent:
    def test_todo_required_fields(self):
        t = Todo(title="Test")
        data = t.model_dump()
        for field in (
            "todo_id",
            "title",
            "status",
            "priority",
            "queue",
            "version",
            "created_at",
            "updated_at",
            "work_type",
        ):
            assert field in data, f"required field {field!r} missing"

    def test_task_return_required_fields(self):
        tr = TaskReturn(return_id="r1", job_id="j1", playbook="p.yml", queue="core")
        data = tr.model_dump()
        for field in (
            "return_id",
            "job_id",
            "playbook",
            "queue",
            "status",
            "exit_code",
            "created_at",
            "schema_version",
        ):
            assert field in data, f"required field {field!r} missing"

    def test_job_spec_required_fields(self):
        js = JobSpec(job_id="j1", playbook="p.yml", queue="core")
        data = js.model_dump()
        for field in ("job_id", "playbook", "queue", "work_type", "resource_profile", "budget_context"):
            assert field in data, f"required field {field!r} missing"

    def test_queue_required_fields(self):
        q = Queue(queue_name="core")
        data = q.model_dump()
        for field in ("queue_name", "queue_enabled", "priority_weight", "hard_cap", "soft_cap"):
            assert field in data, f"required field {field!r} missing"

    def test_deployment_record_required_fields(self):
        d = DeploymentRecord(instance_id="i-1", working_dir="/tmp/d")
        data = d.model_dump()
        for field in ("instance_id", "working_dir", "state", "created_at"):
            assert field in data, f"required field {field!r} missing"


class TestOptionalFieldsOmitted:
    def test_todo_exclude_none_omits_nullables(self):
        t = Todo(title="x")
        data = t.model_dump(exclude_none=True)
        for nullable in (
            "project_id",
            "completed_at",
            "parent_todo_id",
            "assigned_agent",
            "model_profile",
            "prompt_profile",
            "estimated_cost_usd",
            "confidence",
            "manual_hold_reason",
            "scheduled_at",
            "cron",
            "next_run_at",
            "last_run_at",
            "max_runs",
        ):
            assert nullable not in data, f"nullable field {nullable!r} should be omitted"

    def test_task_return_exclude_none_omits_nullables(self):
        tr = TaskReturn(return_id="r1", job_id="j1", playbook="p.yml", queue="core")
        data = tr.model_dump(exclude_none=True)
        for nullable in (
            "todo_id",
            "logs_ref",
            "diff_ref",
            "test_results_ref",
            "molecule_results_ref",
            "coverage_results_ref",
            "model_usage_ref",
            "producer_worker_id",
        ):
            assert nullable not in data, f"nullable field {nullable!r} should be omitted"

    def test_ownership_spec_exclude_none_omits_nothing(self):
        o = OwnershipSpec(tenant_id="acme", project_id="proj", agent_id="a1")
        data = o.model_dump(exclude_none=True)
        assert "tenant_id" in data
        assert "project_id" in data
        assert "agent_id" in data

    def test_task_decision_exclude_none_omits_nullables(self):
        td = TaskDecision(return_id="r1", decision="complete")
        data = td.model_dump(exclude_none=True)
        assert "matched_todo_id" not in data


# ── Datetime format consistency ──────────────────────────────────────────────


class TestDatetimeFormat:
    def test_todo_created_at_is_utc_aware(self):
        t = Todo(title="x")
        assert t.created_at.tzinfo is not None
        offset = t.created_at.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 0.0

    def test_todo_updated_at_is_utc_aware(self):
        t = Todo(title="x")
        assert t.updated_at.tzinfo is not None

    def test_task_return_created_at_is_utc_aware(self):
        tr = TaskReturn(return_id="r1", job_id="j1", playbook="p.yml", queue="core")
        assert tr.created_at.tzinfo is not None
        offset = tr.created_at.utcoffset()
        assert offset is not None
        assert offset.total_seconds() == 0.0

    def test_task_decision_created_at_is_utc_aware(self):
        td = TaskDecision(return_id="r1", decision="complete")
        assert td.created_at.tzinfo is not None

    def test_deployment_record_created_at_is_utc_aware(self):
        d = DeploymentRecord(instance_id="i-1", working_dir="/tmp/d")
        assert d.created_at.tzinfo is not None

    def test_datetimes_json_serialize_as_iso_strings(self):
        models_with_datetimes = [
            Todo(title="x"),
            TaskReturn(return_id="r1", job_id="j1", playbook="p.yml", queue="core"),
            TaskDecision(return_id="r1", decision="complete"),
            BenchmarkResult(
                model_profile_id="m1",
                task_type=TaskType.BUG_FIX,
                scores=BenchmarkScores(
                    completion_score=0.5,
                    code_quality_score=0.5,
                    instruction_adherence_score=0.5,
                    token_efficiency_score=0.5,
                ),
            ),
            DeploymentRecord(instance_id="i-1", working_dir="/tmp/d"),
            PromptProfile(id="p1", name="X", source="Y", prompt_text="Z"),
        ]
        for model in models_with_datetimes:
            data = model.model_dump(mode="json")
            for key, value in data.items():
                name = type(model).__name__
                if key.endswith("_at"):
                    if value is None:
                        continue
                    assert isinstance(value, str), f"{name}.{key} not a string: {value!r}"
                    assert "T" in value, f"{name}.{key} missing ISO separator: {value!r}"
                    assert value.endswith("Z") or "+" in value, f"{name}.{key} missing tz offset: {value!r}"

    def test_explicit_datetime_preserves_utc(self):
        past = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        t = Todo(title="x", created_at=past)
        data = t.model_dump(mode="json")
        assert "2025-06-01T12:00:00" in data["created_at"]


# ── Enum value validity ──────────────────────────────────────────────────────


class TestEnumValues:
    def test_all_todo_status_values_are_non_empty_strings(self):
        for status in TodoStatus:
            assert isinstance(status.value, str)
            assert len(status.value) > 0

    def test_all_work_type_values_are_non_empty_strings(self):
        for wt in WorkType:
            assert isinstance(wt.value, str)
            assert len(wt.value) > 0

    def test_all_task_return_status_values_are_non_empty_strings(self):
        for s in TaskReturnStatus:
            assert isinstance(s.value, str)
            assert len(s.value) > 0

    def test_all_task_type_values_are_non_empty_strings(self):
        for tt in TaskType:
            assert isinstance(tt.value, str)
            assert len(tt.value) > 0

    def test_all_task_role_values_are_non_empty_strings(self):
        for tr in TaskRole:
            assert isinstance(tr.value, str)
            assert len(tr.value) > 0

    def test_all_risk_level_values_are_non_empty_strings(self):
        for rl in RiskLevel:
            assert isinstance(rl.value, str)
            assert len(rl.value) > 0

    def test_all_resource_profile_values_are_non_empty_strings(self):
        for rp in ResourceProfile:
            assert isinstance(rp.value, str)
            assert len(rp.value) > 0

    def test_enum_values_serialize_as_strings_in_json(self):
        t = Todo(title="x")
        data = json.loads(t.model_dump_json())
        assert isinstance(data["status"], str)
        assert data["status"] == "backlog"
        assert isinstance(data["work_type"], str)
        assert data["work_type"] == "unknown"


# ── Nested model serialization ───────────────────────────────────────────────


class TestNestedModelSerialization:
    def test_quality_gate_config_nested_serialization(self):
        cfg = QualityGateConfig(
            python=PythonQualityGate(line_coverage_min_percent=85.0),
            molecule=MoleculeQualityGate(coverage_min_percent=95.0),
            enforcement=EnforcementGate(block_push=True),
        )
        data = json.loads(cfg.model_dump_json())
        assert data["python"]["line_coverage_min_percent"] == 85.0
        assert data["molecule"]["coverage_min_percent"] == 95.0
        assert data["enforcement"]["block_push"] is True

    def test_benchmark_result_nested_scores(self):
        r = BenchmarkResult(
            model_profile_id="m1",
            task_type=TaskType.FEATURE,
            scores=BenchmarkScores(
                completion_score=0.8,
                code_quality_score=0.7,
                instruction_adherence_score=0.6,
                token_efficiency_score=0.9,
            ),
        )
        data = json.loads(r.model_dump_json())
        assert isinstance(data["scores"], dict)
        assert data["scores"]["completion_score"] == 0.8

    def test_job_spec_with_ownership_nested(self):
        o = OwnershipSpec(tenant_id="acme", project_id="proj", agent_id="a1")
        js = JobSpec(job_id="j1", playbook="p.yml", queue="core", ownership=o)
        data = json.loads(js.model_dump_json())
        assert data["ownership"]["tenant_id"] == "acme"
        assert data["ownership"]["project_id"] == "proj"

    def test_quality_gate_config_defaults_nested(self):
        cfg = QualityGateConfig()
        data = cfg.model_dump()
        assert "enabled" in data
        assert isinstance(data["python"], dict)
        assert data["python"]["enabled"] is True
        assert isinstance(data["molecule"], dict)
        assert data["molecule"]["coverage_min_percent"] == 100.0

    def test_todo_default_lists_serialize_as_empty_arrays(self):
        t = Todo(title="x")
        data = json.loads(t.model_dump_json())
        assert data["tags"] == []
        assert data["child_todo_ids"] == []
        assert data["acceptance_criteria"] == []
        assert data["artifacts"] == []
        assert data["evidence_refs"] == []

    def test_task_decision_lists_serialize_as_arrays(self):
        td = TaskDecision(return_id="r1", decision="complete", evidence_refs=["ref:1"], git_requests=["push"])
        data = json.loads(td.model_dump_json())
        assert data["evidence_refs"] == ["ref:1"]
        assert data["git_requests"] == ["push"]
        assert data["audit_notes"] == []


# ── Field type checks ────────────────────────────────────────────────────────


class TestFieldTypes:
    def test_todo_boolean_fields(self):
        t = Todo(title="x")
        assert isinstance(t.schedule_paused, bool)

    def test_quality_gate_boolean_fields(self):
        eg = EnforcementGate()
        assert isinstance(eg.block_commit, bool)
        assert isinstance(eg.block_push, bool)
        assert isinstance(eg.fail_completion_when_below_gate, bool)

    def test_queue_numeric_fields(self):
        q = Queue(queue_name="core")
        assert isinstance(q.hard_cap, int)
        assert isinstance(q.soft_cap, int)
        assert isinstance(q.priority_weight, int)
        assert isinstance(q.max_error_rate, float)

    def test_benchmark_result_numeric_fields(self):
        scores = BenchmarkScores(
            completion_score=0.5,
            code_quality_score=0.5,
            instruction_adherence_score=0.5,
            token_efficiency_score=0.5,
        )
        r = BenchmarkResult(
            model_profile_id="m1",
            task_type=TaskType.FEATURE,
            scores=scores,
            time_seconds=30.5,
            input_tokens=100,
            output_tokens=200,
            cost_usd=0.05,
        )
        data = r.model_dump()
        assert isinstance(data["time_seconds"], float)
        assert isinstance(data["input_tokens"], int)
        assert isinstance(data["output_tokens"], int)
        assert isinstance(data["cost_usd"], float)
        assert isinstance(data["success"], bool)


# ── Extra fields forbidden ──────────────────────────────────────────────────


class TestExtraFieldsForbidden:
    def test_job_spec_extra_fields_rejected(self):
        with pytest.raises(ValueError):
            JobSpec.model_validate({"job_id": "j1", "playbook": "p.yml", "queue": "q", "bogus": 42})

    def test_ownership_spec_extra_fields_rejected(self):
        with pytest.raises(ValueError):
            OwnershipSpec.model_validate({"tenant_id": "a", "project_id": "p", "agent_id": "a", "bogus": 1})

    def test_work_ceiling_extra_fields_rejected(self):
        with pytest.raises(ValueError):
            WorkCeilingSpec.model_validate({"bogus": 1})
