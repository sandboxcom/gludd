from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.loop import (
    EventLoop,
    _resolve_prompt_text_static,
    _work_type_to_task_type,
)
from general_ludd.schemas.task_decision import TaskDecision
from general_ludd.schemas.task_return import TaskReturn, TaskReturnStatus
from general_ludd.schemas.todo import Todo, TodoStatus


def _make_loop(**overrides):
    session = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = []
    session.execute.return_value = db_result
    session.delete = AsyncMock()
    session.flush = AsyncMock()
    http_client = AsyncMock()
    todo_repo = AsyncMock()
    task_return_repo = AsyncMock()
    defaults = dict(
        worker_base_url="http://worker:8000",
        config={"tick_interval": 1.0},
        session=session,
        http_client=http_client,
        todo_repo=todo_repo,
        task_return_repo=task_return_repo,
    )
    defaults.update(overrides)
    loop = EventLoop(**defaults)
    return loop, {
        "session": session,
        "http_client": http_client,
        "todo_repo": todo_repo,
        "task_return_repo": task_return_repo,
    }


class TestResolvePromptTextStaticJinjaException:
    def test_jinja_render_exception_returns_none(self, tmp_path):
        tmpl = tmp_path / "bad.j2"
        tmpl.write_text("{% for x in broken %}")
        result = _resolve_prompt_text_static(
            None,
            "bad.j2",
            project_templates_dir=str(tmp_path),
        )
        assert result is None


class TestWorkTypeToTaskTypeInvalid:
    def test_invalid_value_returns_feature(self):
        with pytest.MonkeyPatch.context() as m:
            from general_ludd.schemas import benchmark as bm

            original_init = bm.TaskType.__init__

            def patched_init(self, *a, **kw):
                if a and a[0] == "INVALID_XYZ":
                    raise ValueError("bad")
                return original_init(self, *a, **kw)

            m.setattr(bm.TaskType, "__init__", patched_init)
            result = _work_type_to_task_type("INVALID_XYZ")
            assert result == bm.TaskType.FEATURE


class TestOnConfigReloaded:
    def test_config_reloaded_updates_snapshot(self):
        loop, _ = _make_loop(config={"key": "val"})
        event = MagicMock()
        event.payload = {"scope": "global"}
        loop._on_config_reloaded(event)
        assert loop._config_snapshot == {"key": "val"}

    def test_config_reloaded_with_none_payload(self):
        loop, _ = _make_loop(config={"a": 1})
        event = MagicMock()
        event.payload = None
        loop._on_config_reloaded(event)
        assert loop._config_snapshot == {"a": 1}


class TestDispatchReviewJobNoHttpNoRunner:
    @pytest.mark.asyncio
    async def test_dispatch_review_job_returns_when_no_client_and_no_runner(self):
        loop, _ = _make_loop(http_client=None, runner=None)
        tr = MagicMock()
        tr.return_id = "RET-999"
        tr.todo_id = "TODO-999"
        tr.queue = "core"
        tr.plan_artifact = None
        tr.project_id = None
        await loop._dispatch_review_job(tr)


class TestPersistReviewResponse:
    @pytest.mark.asyncio
    async def test_persist_review_response_dict_resp(self):
        task_return_repo = AsyncMock()
        loop, mocks = _make_loop()
        loop._task_return_repo = task_return_repo
        tr = MagicMock()
        tr.return_id = "RET-D1"
        tr.todo_id = "TODO-D1"
        tr.project_id = None
        resp = {"decision": "complete", "confidence": 0.9, "evidence_refs": [], "audit_notes": []}
        await loop._persist_review_response(tr, resp)
        mocks["session"].add.assert_called_once()
        mocks["session"].flush.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_review_response_body_bytes(self):
        import json as _json

        task_return_repo = AsyncMock()
        loop, mocks = _make_loop()
        loop._task_return_repo = task_return_repo
        tr = MagicMock()
        tr.return_id = "RET-B1"
        tr.todo_id = "TODO-B1"
        tr.project_id = None
        resp = MagicMock()
        resp.json = None
        resp.body = _json.dumps({
            "decision": "complete",
            "confidence": 0.8,
            "evidence_refs": [],
            "audit_notes": [],
        }).encode()
        await loop._persist_review_response(tr, resp)
        mocks["session"].add.assert_called_once()

    @pytest.mark.asyncio
    async def test_persist_review_response_body_none_returns(self):
        task_return_repo = AsyncMock()
        loop, mocks = _make_loop()
        loop._task_return_repo = task_return_repo
        tr = MagicMock()
        tr.return_id = "RET-N1"
        resp = MagicMock()
        resp.json = None
        resp.body = None
        await loop._persist_review_response(tr, resp)
        mocks["session"].add.assert_not_called()

    @pytest.mark.asyncio
    async def test_persist_review_response_non_dict_data_returns(self):
        task_return_repo = AsyncMock()
        loop, mocks = _make_loop()
        loop._task_return_repo = task_return_repo
        tr = MagicMock()
        tr.return_id = "RET-ND"
        resp = MagicMock()
        resp.json = AsyncMock(return_value="not a dict")
        await loop._persist_review_response(tr, resp)
        mocks["session"].add.assert_not_called()


class TestPIDPhaseException:
    @pytest.mark.asyncio
    async def test_pid_phase_handles_exception_gracefully(self):
        from general_ludd.schemas.queue import Queue

        queues = [Queue(queue_name="core").model_dump()]
        loop, _ = _make_loop(config={"tick_interval": 1.0, "queues": queues})
        loop._config_snapshot = {"queues": queues}
        import general_ludd.controllers.load_scrape as ls_mod

        original = ls_mod.LoadSnapshot

        def bad_snapshot(*a, **kw):
            raise RuntimeError("forced error")

        ls_mod.LoadSnapshot = bad_snapshot
        try:
            await loop._phase_evaluate_pid_controllers()
        finally:
            ls_mod.LoadSnapshot = original
        assert "pid_outputs" not in loop._tick_state


class TestClaimRunnableTodosNoProject:
    @pytest.mark.asyncio
    async def test_claim_runnable_no_project_returns_none_project(self):
        todo_repo = AsyncMock()
        todo_repo.claim_runnable.return_value = []
        pm = MagicMock()
        pm.select_project.return_value = None
        loop, _ = _make_loop(todo_repo=todo_repo, project_manager=pm)
        await loop._phase_claim_runnable_todos()
        todo_repo.claim_runnable.assert_called_once_with()


class TestGetRuleOverridesNonDictResult:
    def test_rule_overrides_skips_non_dict_result(self):
        loop, _ = _make_loop()
        loop._tick_state["rule_evaluation_results"] = ["not_a_dict", {"todo_id": "T1", "actions": []}]
        todo = MagicMock()
        todo.todo_id = "T1"
        result = loop._get_rule_overrides_for_todo(todo)
        assert result == {}


class TestDispatchValidateJob:
    @pytest.mark.asyncio
    async def test_dispatch_validate_job_posts_to_worker(self):
        loop, mocks = _make_loop()
        mocks["http_client"].post.return_value = MagicMock(status_code=200)
        todo = MagicMock()
        todo.todo_id = "TODO-V1"
        todo.queue = "core"
        todo.work_type = "code"
        todo.project_id = None
        await loop._dispatch_validate_job(todo)
        mocks["http_client"].post.assert_called_once()
        url = mocks["http_client"].post.call_args[0][0]
        assert "validate" in url

    @pytest.mark.asyncio
    async def test_dispatch_validate_job_returns_without_http_client(self):
        loop, _ = _make_loop(http_client=None)
        todo = MagicMock()
        todo.todo_id = "TODO-V2"
        await loop._dispatch_validate_job(todo)


class TestPersistTaskReturnDictResp:
    @pytest.mark.asyncio
    async def test_persist_task_return_dict_resp(self):
        task_return_repo = AsyncMock()
        loop, _mocks = _make_loop()
        loop._task_return_repo = task_return_repo
        from general_ludd.schemas.job import JobSpec

        todo = MagicMock()
        todo.todo_id = "TODO-D3"
        job = JobSpec(job_id="EXEC-D3", todo_id="TODO-D3", playbook="noop.yml", queue="core")
        resp = {"exit_code": 0, "result_summary": "ok", "return_id": "RET-D3"}
        await loop._persist_task_return(todo, job, resp)
        task_return_repo.create.assert_called_once()


class TestReconcileCompletedDecisionsSkips:
    @pytest.mark.asyncio
    async def test_reconcile_skips_no_matched_todo_id(self):
        loop, mocks = _make_loop()
        decision_row = MagicMock()
        decision_row.return_id = "RET-S1"
        decision_row.matched_todo_id = None
        decision_row.decision = "complete"
        decision_row.confidence = 0.9
        result_mock = MagicMock()
        result_mock.scalars().all.return_value = [decision_row]
        mocks["session"].execute.return_value = result_mock
        await loop._phase_reconcile_completed_decisions()
        mocks["todo_repo"].get_by_id.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconcile_skips_wrong_status(self):
        loop, mocks = _make_loop()
        todo_model = MagicMock()
        todo_model.todo_id = "TODO-S2"
        todo_model.status = "queued"
        todo_model.version = 1
        decision_row = MagicMock()
        decision_row.return_id = "RET-S2"
        decision_row.matched_todo_id = "TODO-S2"
        decision_row.decision = "complete"
        decision_row.confidence = 0.9
        result_mock = MagicMock()
        result_mock.scalars().all.return_value = [decision_row]
        mocks["session"].execute.return_value = result_mock
        mocks["todo_repo"].get_by_id.return_value = todo_model
        await loop._phase_reconcile_completed_decisions()
        mocks["todo_repo"].transition.assert_not_called()


class TestDispatchReturnReviewCreated:
    @pytest.mark.asyncio
    async def test_dispatch_return_review_created_status_builds_job(self):
        loop = EventLoop()
        tr = TaskReturn(
            return_id="RET-CR1",
            job_id="JOB-CR1",
            todo_id="TODO-CR1",
            playbook="noop.yml",
            queue="core",
            status=TaskReturnStatus.CREATED,
        )
        result = await loop.dispatch_return_review(tr)
        assert result["status"] == "dispatched"
        assert result["job_id"] == "REVIEW-RET-CR1"


class TestClaimRunnableTodosMethod:
    @pytest.mark.asyncio
    async def test_claim_runnable_filters_queued(self):
        loop = EventLoop()
        queued = Todo(title="q", status=TodoStatus.QUEUED)
        active = Todo(title="a", status=TodoStatus.ACTIVE)
        result = await loop.claim_runnable_todos([queued, active])
        assert len(result) == 1
        assert result[0].title == "q"


class TestReconcileDecisionBranches:
    @pytest.mark.asyncio
    async def test_reconcile_decision_failed(self):
        loop = EventLoop()
        todo = Todo(title="t", status=TodoStatus.REVIEWING_RETURN)
        decision = TaskDecision(return_id="R1", decision="failed", confidence=0.5)
        updated = await loop.reconcile_decision(decision, todo)
        assert updated.status == TodoStatus.FAILED

    @pytest.mark.asyncio
    async def test_reconcile_decision_blocked(self):
        loop = EventLoop()
        todo = Todo(title="t", status=TodoStatus.REVIEWING_RETURN)
        decision = TaskDecision(return_id="R1", decision="blocked", confidence=0.5)
        updated = await loop.reconcile_decision(decision, todo)
        assert updated.status == TodoStatus.BLOCKED

    @pytest.mark.asyncio
    async def test_reconcile_decision_manual_hold(self):
        loop = EventLoop()
        todo = Todo(title="t", status=TodoStatus.REVIEWING_RETURN)
        decision = TaskDecision(return_id="R1", decision="manual_hold", confidence=0.5)
        updated = await loop.reconcile_decision(decision, todo)
        assert updated.status == TodoStatus.MANUAL_HOLD
