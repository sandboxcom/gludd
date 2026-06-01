"""Unit tests for schemas."""

import pytest

from general_ludd.schemas.job import JobSpec
from general_ludd.schemas.queue import INITIAL_QUEUES
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn, TaskReturnStatus


class TestTaskDecision:
    def test_valid_decision(self):
        d = TaskDecision(return_id="R1", decision="complete", confidence=0.9)
        assert d.decision == "complete"

    def test_invalid_decision(self):
        with pytest.raises(ValueError, match="Invalid decision"):
            TaskDecision(return_id="R1", decision="invalid_choice")

    def test_all_valid_decisions(self):
        for decision in TaskDecision.valid_decisions():
            d = TaskDecision(return_id="R1", decision=decision)
            assert d.decision == decision


class TestTaskReturn:
    def test_default_values(self):
        r = TaskReturn(return_id="R1", job_id="J1", playbook="noop.yml", queue="core")
        assert r.status == TaskReturnStatus.CREATED
        assert r.exit_code == 0
        assert r.schema_version == 1


class TestQueue:
    def test_initial_queues_exist(self):
        assert len(INITIAL_QUEUES) >= 12
        names = {q.queue_name for q in INITIAL_QUEUES}
        assert "core" in names
        assert "manual_hold" in names
        assert "self_improve" in names

    def test_manual_hold_disabled(self):
        q = next(q for q in INITIAL_QUEUES if q.queue_name == "manual_hold")
        assert q.queue_enabled is False


class TestJobSpec:
    def test_job_spec_defaults(self):
        j = JobSpec(job_id="J1", playbook="noop.yml", queue="core")
        assert j.work_type == "unknown"
        assert j.vars_namespace_refs == []
        assert j.budget_context == {}
