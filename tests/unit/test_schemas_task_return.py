"""Structural tests for schemas/task_return.py — TaskReturn model and TaskReturnStatus."""

from __future__ import annotations

import pytest

from general_ludd.schemas.task_return import TaskReturn, TaskReturnStatus


class TestTaskReturnStatus:
    def test_enum_members(self):
        members = list(TaskReturnStatus)
        assert TaskReturnStatus.CREATED in members
        assert TaskReturnStatus.CLAIMED_FOR_REVIEW in members
        assert TaskReturnStatus.REVIEWED in members
        assert TaskReturnStatus.ARCHIVED in members

    def test_created_is_default(self):
        assert TaskReturnStatus.CREATED.value == "created"

    def test_is_string_enum(self):
        assert TaskReturnStatus.CREATED == "created"
        assert TaskReturnStatus("created") == TaskReturnStatus.CREATED


class TestTaskReturnModel:
    def test_defaults_created_status(self):
        tr = TaskReturn(
            return_id="r1",
            job_id="j1",
            playbook="noop.yml",
            queue="core",
        )
        assert tr.status == TaskReturnStatus.CREATED

    def test_defaults_exit_code_zero(self):
        tr = TaskReturn(
            return_id="r1",
            job_id="j1",
            playbook="noop.yml",
            queue="core",
        )
        assert tr.exit_code == 0

    def test_defaults_empty_artifacts(self):
        tr = TaskReturn(
            return_id="r1",
            job_id="j1",
            playbook="noop.yml",
            queue="core",
        )
        assert tr.artifacts == []

    def test_defaults_schema_version(self):
        tr = TaskReturn(
            return_id="r1",
            job_id="j1",
            playbook="noop.yml",
            queue="core",
        )
        assert tr.schema_version == 1

    def test_default_work_type(self):
        tr = TaskReturn(
            return_id="r1",
            job_id="j1",
            playbook="noop.yml",
            queue="core",
        )
        assert tr.work_type == "unknown"

    def test_created_at_set(self):
        tr = TaskReturn(
            return_id="r1",
            job_id="j1",
            playbook="noop.yml",
            queue="core",
        )
        assert tr.created_at is not None

    def test_optional_fields_default_none(self):
        tr = TaskReturn(
            return_id="r1",
            job_id="j1",
            playbook="noop.yml",
            queue="core",
        )
        assert tr.todo_id is None
        assert tr.logs_ref is None
        assert tr.diff_ref is None
        assert tr.test_results_ref is None
        assert tr.molecule_results_ref is None
        assert tr.coverage_results_ref is None
        assert tr.model_usage_ref is None
        assert tr.producer_worker_id is None

    def test_return_id_required(self):
        with pytest.raises(ValueError, match="field must not be empty"):
            TaskReturn(return_id="", job_id="j1", playbook="p", queue="q")

    def test_return_id_whitespace_rejected(self):
        with pytest.raises(ValueError, match="field must not be empty"):
            TaskReturn(return_id="   ", job_id="j1", playbook="p", queue="q")

    def test_job_id_required(self):
        with pytest.raises(ValueError, match="field must not be empty"):
            TaskReturn(return_id="r1", job_id="", playbook="p", queue="q")

    def test_playbook_required(self):
        with pytest.raises(ValueError, match="field must not be empty"):
            TaskReturn(return_id="r1", job_id="j1", playbook="", queue="q")

    def test_queue_required(self):
        with pytest.raises(ValueError, match="field must not be empty"):
            TaskReturn(return_id="r1", job_id="j1", playbook="p", queue="")

    def test_fields_stripped(self):
        tr = TaskReturn(
            return_id="  r1  ",
            job_id="  j1  ",
            playbook="  noop.yml  ",
            queue="  core  ",
        )
        assert tr.return_id == "r1"
        assert tr.job_id == "j1"
        assert tr.playbook == "noop.yml"
        assert tr.queue == "core"

    def test_custom_status(self):
        tr = TaskReturn(
            return_id="r1",
            job_id="j1",
            playbook="noop.yml",
            queue="core",
            status=TaskReturnStatus.ARCHIVED,
        )
        assert tr.status == TaskReturnStatus.ARCHIVED

    def test_custom_exit_code(self):
        tr = TaskReturn(
            return_id="r1",
            job_id="j1",
            playbook="noop.yml",
            queue="core",
            exit_code=1,
        )
        assert tr.exit_code == 1

    def test_artifacts_preserved(self):
        tr = TaskReturn(
            return_id="r1",
            job_id="j1",
            playbook="noop.yml",
            queue="core",
            artifacts=["a1", "a2"],
        )
        assert tr.artifacts == ["a1", "a2"]
