"""E2E: Self-improvement and reload (sprint objective 14).

Covers ReloadManager lifecycle, SelfImprovementWorkflow validation gate,
apply/reload cycle, rollback capability, and playbook stubs.
"""

from __future__ import annotations

import os

from general_ludd.reload.manager import ReloadManager, ReloadStatus, ReloadType
from general_ludd.reload.self_improve import ApplyResult, SelfImprovementWorkflow
from general_ludd.validation.runner import ValidationResult


class TestReloadManagerImport:
    def test_reload_manager_importable(self):
        assert ReloadManager is not None

    def test_reload_type_enum_values(self):
        expected = {"config", "prompts", "rules", "worker_code", "event_loop_code", "schema_migration"}
        actual = {rt.value for rt in ReloadType}
        assert actual == expected

    def test_reload_manager_instantiation(self):
        mgr = ReloadManager()
        assert mgr is not None


class TestReloadManagerRequestExecute:
    def test_request_reload_returns_pending(self):
        mgr = ReloadManager()
        result = mgr.request_reload(ReloadType.CONFIG)
        assert result.status == "pending"
        assert result.reload_type == ReloadType.CONFIG
        assert result.reload_id

    def test_execute_reload_returns_success(self):
        mgr = ReloadManager()
        rr = mgr.request_reload(ReloadType.WORKER_CODE)
        result = mgr.execute_reload(rr.reload_id)
        assert result.status == "success"
        assert result.reload_type == ReloadType.WORKER_CODE

    def test_execute_reload_unknown_id_returns_failed(self):
        mgr = ReloadManager()
        result = mgr.execute_reload("nonexistent")
        assert result.status == "failed"
        assert "Unknown reload_id" in result.message

    def test_request_reload_with_config(self):
        mgr = ReloadManager()
        result = mgr.request_reload(ReloadType.RULES, config={"dry_run": True})
        assert result.status == "pending"
        assert result.reload_type == ReloadType.RULES


class TestReloadManagerRollback:
    def test_rollback_transitions_to_rolled_back(self):
        mgr = ReloadManager()
        rr = mgr.request_reload(ReloadType.PROMPTS)
        mgr.execute_reload(rr.reload_id)
        result = mgr.rollback(rr.reload_id)
        assert result.status == "rolled_back"
        assert "Rolled back" in result.message

    def test_rollback_unknown_id_returns_failed(self):
        mgr = ReloadManager()
        result = mgr.rollback("nonexistent")
        assert result.status == "failed"

    def test_rollback_after_request_without_execute(self):
        mgr = ReloadManager()
        rr = mgr.request_reload(ReloadType.SCHEMA_MIGRATION)
        result = mgr.rollback(rr.reload_id)
        assert result.status == "rolled_back"


class TestReloadManagerStatus:
    def test_get_reload_status_after_request(self):
        mgr = ReloadManager()
        rr = mgr.request_reload(ReloadType.CONFIG)
        status = mgr.get_reload_status(rr.reload_id)
        assert isinstance(status, ReloadStatus)
        assert status.status == "pending"
        assert status.type == ReloadType.CONFIG
        assert status.started_at
        assert status.completed_at is None

    def test_get_reload_status_after_execute(self):
        mgr = ReloadManager()
        rr = mgr.request_reload(ReloadType.WORKER_CODE)
        mgr.execute_reload(rr.reload_id)
        status = mgr.get_reload_status(rr.reload_id)
        assert status.status == "success"
        assert status.completed_at is not None

    def test_get_reload_status_unknown_returns_unknown(self):
        mgr = ReloadManager()
        status = mgr.get_reload_status("nonexistent")
        assert status.status == "unknown"

    def test_get_reload_status_after_rollback(self):
        mgr = ReloadManager()
        rr = mgr.request_reload(ReloadType.EVENT_LOOP_CODE)
        mgr.rollback(rr.reload_id)
        status = mgr.get_reload_status(rr.reload_id)
        assert status.status == "rolled_back"


class TestSelfImprovementWorkflow:
    def test_workflow_importable(self):
        assert SelfImprovementWorkflow is not None

    def test_workflow_instantiation(self):
        wf = SelfImprovementWorkflow()
        assert wf is not None

    def test_create_improvement_todo(self):
        wf = SelfImprovementWorkflow()
        todo = wf.create_improvement_todo("Add caching", "Cache validation results")
        assert todo["todo_id"].startswith("SI-")
        assert todo["title"] == "Add caching"
        assert todo["status"] == "pending"
        assert todo["created_at"]

    def test_create_improvement_todo_unique_ids(self):
        wf = SelfImprovementWorkflow()
        t1 = wf.create_improvement_todo("A", "desc a")
        t2 = wf.create_improvement_todo("B", "desc b")
        assert t1["todo_id"] != t2["todo_id"]


class TestSelfImprovementApply:
    def test_apply_rejects_failed_validation(self):
        wf = SelfImprovementWorkflow()
        todo = wf.create_improvement_todo("Fix", "Fix bug")
        vr = ValidationResult(success=False, passed_count=0, failed_count=1, output="FAIL")
        result = wf.apply_improvement(todo["todo_id"], vr)
        assert isinstance(result, ApplyResult)
        assert result.applied is False
        assert result.validation_passed is False
        assert result.reload_needed is False

    def test_apply_accepts_passed_validation(self):
        wf = SelfImprovementWorkflow()
        todo = wf.create_improvement_todo("Feat", "New feature")
        vr = ValidationResult(success=True, passed_count=5, failed_count=0, output="OK")
        result = wf.apply_improvement(todo["todo_id"], vr)
        assert result.applied is True
        assert result.validation_passed is True
        assert result.reload_needed is True

    def test_apply_updates_todo_status(self):
        wf = SelfImprovementWorkflow()
        todo = wf.create_improvement_todo("Refactor", "Refactor module")
        vr = ValidationResult(success=True, passed_count=3, failed_count=0, output="OK")
        wf.apply_improvement(todo["todo_id"], vr)
        stored = wf._todos.get(todo["todo_id"])
        assert stored is not None
        assert stored["status"] == "applied"


class TestSelfImprovementReload:
    def test_reload_not_needed_when_validation_failed(self):
        wf = SelfImprovementWorkflow()
        ar = ApplyResult(todo_id="SI-abcd1234", applied=False, reload_needed=False, validation_passed=False)
        result = wf.reload_if_needed(ar)
        assert result.status == "pending"
        assert "not needed" in result.message

    def test_reload_executed_when_needed(self):
        wf = SelfImprovementWorkflow()
        ar = ApplyResult(todo_id="SI-abcd1234", applied=True, reload_needed=True, validation_passed=True)
        result = wf.reload_if_needed(ar)
        assert result.status == "success"
        assert result.reload_type == ReloadType.WORKER_CODE


class TestSelfImprovementFullCycle:
    def test_full_cycle_pass(self):
        wf = SelfImprovementWorkflow()
        todo = wf.create_improvement_todo("Improve X", "Make X faster")
        vr = ValidationResult(success=True, passed_count=10, failed_count=0, output="ALL PASS")
        ar = wf.apply_improvement(todo["todo_id"], vr)
        assert ar.applied is True
        rr = wf.reload_if_needed(ar)
        assert rr.status == "success"

    def test_full_cycle_fail_blocks_reload(self):
        wf = SelfImprovementWorkflow()
        todo = wf.create_improvement_todo("Fix Y", "Fix broken Y")
        vr = ValidationResult(success=False, passed_count=3, failed_count=2, output="FAIL")
        ar = wf.apply_improvement(todo["todo_id"], vr)
        assert ar.applied is False
        rr = wf.reload_if_needed(ar)
        assert "not needed" in rr.message


class TestReloadPlaybookStubs:
    def test_self_improve_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "playbooks", "self_improve_harness.yml")
        assert os.path.isfile(path)

    def test_reload_playbook_exists(self):
        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "playbooks", "reload_harness.yml")
        assert os.path.isfile(path)

    def test_self_improve_playbook_valid_yaml(self):
        import yaml

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "playbooks", "self_improve_harness.yml")
        with open(path) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, list)
        assert data[0]["name"]
        assert data[0]["hosts"] == "localhost"

    def test_reload_playbook_valid_yaml(self):
        import yaml

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "playbooks", "reload_harness.yml")
        with open(path) as f:
            data = yaml.safe_load(f)
        assert isinstance(data, list)
        assert data[0]["name"]
        assert data[0]["hosts"] == "localhost"

    def test_self_improve_playbook_has_tasks(self):
        import yaml

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "playbooks", "self_improve_harness.yml")
        with open(path) as f:
            data = yaml.safe_load(f)
        tasks = data[0].get("tasks", [])
        assert len(tasks) >= 1

    def test_reload_playbook_has_tasks(self):
        import yaml

        repo_root = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
        path = os.path.join(repo_root, "playbooks", "reload_harness.yml")
        with open(path) as f:
            data = yaml.safe_load(f)
        tasks = data[0].get("tasks", [])
        assert len(tasks) >= 1
