"""Tests for pipeline wiring: profile propagation, rules evaluation, and LLM integration."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.event_loop.loop import EventLoop
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.prompts.registry import PromptRegistry
from general_ludd.review.reviewer import ReturnReviewer
from general_ludd.rules.engine import Rule, evaluate_rules
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


class TestProfilePropagation:
    @pytest.mark.asyncio
    async def test_dispatch_execute_propagates_model_profile(self):
        loop, mocks = _make_loop()
        todo = Todo(
            title="test task",
            todo_id="TODO-001",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            resource_profile="low_resource",
            model_profile="zai_coder",
            prompt_profile="coder_default",
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        loop._tick_state["claimed_todos"] = [todo]
        await loop._phase_dispatch_execute_jobs()
        call_kwargs = mocks["http_client"].post.call_args
        body = call_kwargs[1]["json"] if "json" in call_kwargs[1] else call_kwargs.kwargs["json"]
        assert body["model_profile"] == "zai_coder"
        assert body["prompt_profile"] == "coder_default"

    @pytest.mark.asyncio
    async def test_dispatch_execute_propagates_plan_artifact(self):
        loop, mocks = _make_loop()
        todo = Todo(
            title="test task",
            todo_id="TODO-002",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            plan_artifact="## Plan\nDo the thing",
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        loop._tick_state["claimed_todos"] = [todo]
        await loop._phase_dispatch_execute_jobs()
        call_kwargs = mocks["http_client"].post.call_args
        body = call_kwargs[1]["json"] if "json" in call_kwargs[1] else call_kwargs.kwargs["json"]
        assert body["plan_artifact"] == "## Plan\nDo the thing"

    @pytest.mark.asyncio
    async def test_dispatch_execute_runner_path_includes_profiles(self):
        runner = MagicMock()
        runner.prepare_job_dirs.return_value = {"root": "/tmp/job", "artifacts": "/tmp/art"}
        loop, _mocks = _make_loop(runner=runner)
        todo = Todo(
            title="test task",
            todo_id="TODO-003",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
            model_profile="zai_coder",
            prompt_profile="coder_default",
        )
        loop._tick_state["claimed_todos"] = [todo]
        await loop._phase_dispatch_execute_jobs()
        write_call = runner.write_vars.call_args
        job_vars = (
            write_call.kwargs.get("job_vars", {})
        )
        assert job_vars.get("model_profile") == "zai_coder"
        assert job_vars.get("prompt_profile") == "coder_default"

    @pytest.mark.asyncio
    async def test_dispatch_execute_null_profiles_ok(self):
        loop, mocks = _make_loop()
        todo = Todo(
            title="test task",
            todo_id="TODO-004",
            status=TodoStatus.ACTIVE,
            queue="core",
            work_type="code",
        )
        mocks["http_client"].post.return_value = MagicMock(status_code=202)
        loop._tick_state["claimed_todos"] = [todo]
        await loop._phase_dispatch_execute_jobs()
        call_kwargs = mocks["http_client"].post.call_args
        body = call_kwargs[1]["json"] if "json" in call_kwargs[1] else call_kwargs.kwargs["json"]
        assert body["model_profile"] is None
        assert body["prompt_profile"] is None


class TestRulesEvaluationInEventLoop:
    @pytest.mark.asyncio
    async def test_evaluate_rules_phase_with_rules(self):
        loop, _ = _make_loop()
        rules = [
            Rule(
                rule_id="test_route",
                priority=10,
                condition={"field": "todo.work_type", "op": "eq", "value": "dependency"},
                actions=[{"type": "route", "queue": "dependency"}],
            )
        ]
        loop.config["rules"] = rules
        loop.config["todos"] = [
            {"todo_id": "T1", "work_type": "dependency", "status": "queued"},
        ]
        await loop._phase_evaluate_rules()
        results = loop._tick_state.get("rule_evaluation_results", [])
        assert len(results) > 0

    @pytest.mark.asyncio
    async def test_evaluate_rules_no_rules_configured(self):
        loop, _ = _make_loop()
        await loop._phase_evaluate_rules()
        assert loop._tick_state.get("rule_evaluation_results") is not None

    @pytest.mark.asyncio
    async def test_evaluate_rules_matches_dependency_work_type(self):
        rules = [
            Rule(
                rule_id="route_dependency_updates",
                priority=10,
                condition={"field": "todo.work_type", "op": "eq", "value": "dependency"},
                actions=[{"type": "route", "queue": "dependency"}],
            )
        ]
        actions = evaluate_rules(rules, {"todo": {"work_type": "dependency"}})
        assert len(actions) == 1
        assert actions[0].action_type == "route"

    @pytest.mark.asyncio
    async def test_evaluate_rules_no_match(self):
        rules = [
            Rule(
                rule_id="route_dependency_updates",
                priority=10,
                condition={"field": "todo.work_type", "op": "eq", "value": "dependency"},
                actions=[{"type": "route", "queue": "dependency"}],
            )
        ]
        actions = evaluate_rules(rules, {"todo": {"work_type": "code"}})
        assert len(actions) == 0


class TestReturnReviewerLLMCall:
    def test_call_model_uses_gateway(self):
        gateway = MagicMock(spec=ModelGateway)
        from general_ludd.models.gateway import ModelResponse
        gateway.call_model.return_value = ModelResponse(
            content='{"return_id":"R1","matched_todo_id":"T1","decision":"complete","confidence":0.95,"evidence_refs":[],"todo_updates":{},"child_todos":[],"validation_requests":[],"git_requests":[],"audit_notes":[],"policy_flags":[]}',
            usage_metadata={},
            cost_estimate=0.01,
            model_name="glm-5.1",
        )
        gateway.get_profile.return_value = ModelProfile(
            model_profile_id="zai_coder",
            model_name="glm-5.1",
            enabled=True,
        )
        registry = PromptRegistry()
        registry.register("return_review.md.j2", "Review: {{ task_return.return_id }}")
        reviewer = ReturnReviewer(
            gateway=gateway,
            prompt_registry=registry,
            model_profile_id="zai_coder",
        )
        tr = TaskReturn(
            return_id="RET-001",
            job_id="JOB-001",
            todo_id="TODO-001",
            playbook="noop.yml",
            queue="core",
            status=TaskReturnStatus.CREATED,
        )
        decision = reviewer.review_return(tr, [], [])
        assert decision.decision == "complete"
        gateway.call_model.assert_called_once()

    def test_call_model_falls_back_on_parse_error(self):
        gateway = MagicMock(spec=ModelGateway)
        from general_ludd.models.gateway import ModelResponse
        gateway.call_model.return_value = ModelResponse(
            content="not valid json",
            usage_metadata={},
            cost_estimate=0.0,
            model_name="glm-5.1",
        )
        gateway.get_profile.return_value = ModelProfile(
            model_profile_id="zai_coder",
            model_name="glm-5.1",
            enabled=True,
        )
        registry = PromptRegistry()
        registry.register("return_review.md.j2", "Review: {{ task_return.return_id }}")
        reviewer = ReturnReviewer(
            gateway=gateway,
            prompt_registry=registry,
            model_profile_id="zai_coder",
        )
        tr = TaskReturn(
            return_id="RET-002",
            job_id="JOB-002",
            todo_id="TODO-002",
            playbook="noop.yml",
            queue="core",
            status=TaskReturnStatus.CREATED,
        )
        decision = reviewer.review_return(tr, [], [])
        assert decision.decision == "failed"
        assert decision.confidence == 0.0


class TestMetricsCollectorWiring:
    def test_gateway_records_metrics_on_call(self):
        from general_ludd.metrics.collector import MetricsCollector
        from general_ludd.models.gateway import ModelGateway

        collector = MetricsCollector()
        collector.register_agent("agent-1", agent_name="test")

        gateway = ModelGateway(
            profiles=[ModelProfile(
                model_profile_id="test-profile",
                provider="openai",
                model_name="gpt-4",
                enabled=True,
                cost_per_input_token=0.01,
                cost_per_output_token=0.03,
            )],
            metrics_collector=collector,
            metrics_agent_id="agent-1",
        )

        mock_response = MagicMock()
        mock_response.content = "hello"
        mock_response.usage_metadata = {"input_tokens": 100, "output_tokens": 50}

        fake_provider_cls = type("FakeProvider", (), {
            "__init__": lambda self, **kw: None,
            "invoke": lambda self, m: mock_response,
        })

        from general_ludd.models.provider_registry import ProviderRegistry
        registry = MagicMock(spec=ProviderRegistry)
        registry.is_installed.return_value = True
        registry.get_provider_class.return_value = fake_provider_cls
        gateway._registry = registry

        response = gateway.call_model("test-profile", [{"role": "user", "content": "hi"}])
        assert response.content == "hello"

        agent = collector.get_agent("agent-1")
        assert agent is not None
        assert "test-profile" in agent.model_usage
        assert agent.model_usage["test-profile"].total_input_tokens == 100
        assert agent.model_usage["test-profile"].total_output_tokens == 50
        assert agent.model_usage["test-profile"].successful_calls == 1

    def test_gateway_without_metrics_collector_still_works(self):
        from general_ludd.models.gateway import ModelGateway

        gateway = ModelGateway()
        assert gateway._metrics_collector is None


class TestPromptResolution:
    def test_resolve_prompt_text_static_returns_rendered_text(self):
        from general_ludd.event_loop.loop import _resolve_prompt_text_static

        registry = MagicMock()
        registry.render.return_value = "Review task TODO-001"
        result = _resolve_prompt_text_static(registry, "return_review.md.j2", task_id="TODO-001")
        assert result == "Review task TODO-001"
        registry.render.assert_called_once_with("return_review.md.j2", task_id="TODO-001")

    def test_resolve_prompt_text_static_returns_none_without_registry(self):
        from general_ludd.event_loop.loop import _resolve_prompt_text_static

        assert _resolve_prompt_text_static(None, "return_review.md.j2") is None

    def test_resolve_prompt_text_static_returns_none_without_profile(self):
        from general_ludd.event_loop.loop import _resolve_prompt_text_static

        registry = MagicMock()
        assert _resolve_prompt_text_static(registry, None) is None
        assert _resolve_prompt_text_static(registry, "") is None
        registry.render.assert_not_called()

    def test_resolve_prompt_text_static_returns_none_on_error(self):
        from general_ludd.event_loop.loop import _resolve_prompt_text_static

        registry = MagicMock()
        registry.render.side_effect = Exception("template not found")
        assert _resolve_prompt_text_static(registry, "missing.j2") is None

    @pytest.mark.asyncio
    async def test_event_loop_resolves_prompt_in_dispatch(self):
        prompt_reg = MagicMock()
        prompt_reg.render.return_value = "Execute: fix the bug"

        loop, _ = _make_loop(
            config={"tick_interval": 1.0},
            prompt_registry=prompt_reg,
        )

        todo = MagicMock()
        todo.todo_id = "TODO-100"
        todo.queue = "core"
        todo.work_type = "code"
        todo.resource_profile = "low_resource"
        todo.model_profile = None
        todo.prompt_profile = "execute.md.j2"
        todo.plan_artifact = None
        todo.project_id = None

        await loop._dispatch_execute_job(todo)

        prompt_reg.render.assert_called_once()
        call_args = prompt_reg.render.call_args
        assert call_args[0][0] == "execute.md.j2"


class TestSkillsInjection:
    def test_resolve_skill_body_matches_title(self):
        from general_ludd.skills.skill import Skill

        skill_reg = MagicMock()
        skill = Skill(name="tdd", body="Always write tests first", trigger_patterns=["test", "tdd"])
        skill_reg.match_trigger.return_value = [skill]

        loop, _ = _make_loop(skill_registry=skill_reg)
        todo = MagicMock()
        todo.title = "Add TDD support for feature X"
        body = loop._resolve_skill_body(todo)
        assert body == "Always write tests first"

    def test_resolve_skill_body_returns_none_no_match(self):
        skill_reg = MagicMock()
        skill_reg.match_trigger.return_value = []
        loop, _ = _make_loop(skill_registry=skill_reg)
        todo = MagicMock()
        todo.title = "Refactor database layer"
        assert loop._resolve_skill_body(todo) is None

    def test_resolve_skill_body_returns_none_without_registry(self):
        loop, _ = _make_loop()
        todo = MagicMock()
        todo.title = "Something"
        assert loop._resolve_skill_body(todo) is None


class TestPIDPhase:
    def test_pid_phase_skips_without_queues(self):
        loop, _ = _make_loop(config={"tick_interval": 1.0})
        loop._config_snapshot = {}
        import asyncio
        asyncio.run(loop._phase_evaluate_pid_controllers())
        assert "pid_outputs" not in loop._tick_state

    def test_pid_phase_does_not_crash_with_queues(self):
        from general_ludd.schemas.queue import Queue

        queues = [Queue(queue_name="core").model_dump()]
        loop, _ = _make_loop(config={"tick_interval": 1.0, "queues": queues})
        loop._config_snapshot = {"queues": queues}
        import asyncio
        asyncio.run(loop._phase_evaluate_pid_controllers())


class TestTaskReturnPersistence:
    def test_persist_task_return_creates_row(self):
        loop, _ = _make_loop()
        task_return_repo = AsyncMock()
        loop._task_return_repo = task_return_repo

        todo = MagicMock()
        todo.todo_id = "TODO-200"
        from general_ludd.schemas.job import JobSpec
        job = JobSpec(job_id="EXEC-200", todo_id="TODO-200", playbook="noop.yml", queue="core")

        resp = MagicMock()
        resp.json = AsyncMock(return_value={"exit_code": 0, "result_summary": "OK"})

        import asyncio
        asyncio.run(loop._persist_task_return(todo, job, resp))

        task_return_repo.create.assert_called_once()
        call_data = task_return_repo.create.call_args[1]["data"]
        assert call_data["todo_id"] == "TODO-200"
        assert call_data["job_id"] == "EXEC-200"

    def test_persist_task_return_skips_without_repo(self):
        loop, _ = _make_loop()
        loop._task_return_repo = None
        from general_ludd.schemas.job import JobSpec

        todo = MagicMock()
        job = JobSpec(job_id="EXEC-201", todo_id="TODO-201", playbook="noop.yml", queue="core")
        import asyncio
        asyncio.run(loop._persist_task_return(todo, job, MagicMock()))


class TestConfigSnapshotDeepCopy:
    @pytest.mark.asyncio
    async def test_config_snapshot_is_deep_copy(self):
        config = {
            "model_profiles": [{"id": "zai_coder"}],
            "rules": [{"rule_id": "r1"}],
            "queues": [{"queue_name": "core"}],
        }
        loop, _ = _make_loop(config=config)
        await loop._phase_load_config_snapshot()

        config["model_profiles"].append({"id": "new_profile"})
        config["rules"].append({"rule_id": "r2"})

        assert len(loop._config_snapshot["model_profiles"]) == 1
        assert len(loop._config_snapshot["rules"]) == 1

    @pytest.mark.asyncio
    async def test_config_snapshot_includes_shared_vars(self):
        from general_ludd.db.repository import VariableNamespaceRepository

        session = AsyncMock()
        var_repo = MagicMock(spec=VariableNamespaceRepository)
        var_repo.load_vars_for_project = AsyncMock(return_value={"SHARED_KEY": "shared_val"})

        loop, _ = _make_loop(
            config={"model_profiles": [], "rules": []},
            session=session,
        )
        loop._variable_repo = var_repo
        await loop._phase_load_config_snapshot()

        assert loop._config_snapshot.get("shared_vars") == {"SHARED_KEY": "shared_val"}

    @pytest.mark.asyncio
    async def test_config_snapshot_handles_no_variable_repo(self):
        loop, _ = _make_loop(config={"model_profiles": []})
        loop._variable_repo = None
        await loop._phase_load_config_snapshot()
        assert "shared_vars" not in loop._config_snapshot
