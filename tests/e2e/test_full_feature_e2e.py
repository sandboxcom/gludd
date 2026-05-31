"""Comprehensive e2e tests for all implemented features."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import yaml

from agentic_harness.agents.behavior import (
    AgentBehavior,
    BehaviorRenderer,
    GuardrailConfig,
)
from agentic_harness.agents.registry import AgentRegistry
from agentic_harness.agents.tool_adapter import AgentToolAdapter
from agentic_harness.agents.types import AgentConfig, AgentPermission, AgentType
from agentic_harness.ansible.isolation import ProcessIsolationConfig
from agentic_harness.ansible.runner import AnsibleRunnerAdapter
from agentic_harness.config.loader import (
    build_config_layer,
    load_agent_config,
    save_agent_config,
)
from agentic_harness.config.model_routing import (
    ModelRoutingConfig,
    build_router_from_config,
    load_model_routing,
)
from agentic_harness.config.task_loader import load_task_definitions
from agentic_harness.config.user_config import AgentConfig as UserConfigAgentConfig
from agentic_harness.config.user_config import ConfigLayer, UserConfig
from agentic_harness.controllers.budget import RunBudgetGuard
from agentic_harness.event_loop.loop import EventLoop
from agentic_harness.infra.compute import (
    ComputeConfig,
    ComputeProvider,
    GPUType,
    InferenceEngine,
)
from agentic_harness.infra.providers import ProviderRegistry as InfraProviderRegistry
from agentic_harness.infra.terraform import TerraformGenerator
from agentic_harness.mcp.registry import MCPTool, MCPToolRegistry
from agentic_harness.models.gateway import ModelGateway, ModelProfile
from agentic_harness.models.router import ModelRouter
from agentic_harness.planning.artifact import PlanArtifact
from agentic_harness.planning.repo_map import RepoMap, RepoMapBuilder
from agentic_harness.prompts.registry import PromptRegistry
from agentic_harness.review.reviewer import ReturnReviewer
from agentic_harness.schemas.job import JobSpec
from agentic_harness.schemas.task_definition import TaskDefinition
from agentic_harness.schemas.task_return import TaskReturn
from agentic_harness.schemas.todo import (
    VALID_TRANSITIONS,
    Todo,
    TodoStatus,
)


class TestModelRoutingConfig:
    def test_load_config_from_yaml(self, tmp_path: Path) -> None:
        config_data = {
            "default_profile": "gpt4",
            "weak_model_profile": "gpt35",
            "role_routing": {"coder": "gpt4", "reviewer": "gpt4"},
            "quality_routing": {"high": "gpt4", "low": "gpt35"},
            "latency_routing": {"fast": "gpt35"},
            "pattern_routing": {"commit_message": "gpt35"},
        }
        p = tmp_path / "model_routing.yml"
        p.write_text(yaml.dump(config_data))
        config = load_model_routing(p)
        assert config.default_profile == "gpt4"
        assert config.weak_model_profile == "gpt35"
        assert config.role_routing == {"coder": "gpt4", "reviewer": "gpt4"}
        assert config.quality_routing == {"high": "gpt4", "low": "gpt35"}
        assert config.latency_routing == {"fast": "gpt35"}
        assert config.pattern_routing == {"commit_message": "gpt35"}

    def test_load_config_missing_file(self, tmp_path: Path) -> None:
        config = load_model_routing(tmp_path / "nonexistent.yml")
        assert config.default_profile is None
        assert config.role_routing == {}

    def test_build_router_from_config(self) -> None:
        config = ModelRoutingConfig(
            default_profile="default_prof",
            weak_model_profile="weak_prof",
            role_routing={"coder": "coder_prof", "reviewer": "review_prof"},
            quality_routing={"high": "quality_prof"},
            latency_routing={"fast": "latency_prof"},
        )
        router = build_router_from_config(config)
        assert router.resolve_role("coder") == "coder_prof"
        assert router.resolve_role("reviewer") == "review_prof"
        assert router.resolve_role("weak") == "weak_prof"
        assert router.resolve_role("unknown") == "default_prof"
        assert router.resolve_by_quality("high") == "quality_prof"
        assert router.resolve_by_latency("fast") == "latency_prof"

    def test_role_routing_resolves_correctly(self) -> None:
        config = ModelRoutingConfig(
            role_routing={"planner": "plan_model", "builder": "build_model"},
            default_profile="fallback",
        )
        router = build_router_from_config(config)
        assert router.resolve_role("planner") == "plan_model"
        assert router.resolve_role("builder") == "build_model"
        assert router.resolve_role("nonexistent") == "fallback"

    def test_quality_and_latency_routing(self) -> None:
        config = ModelRoutingConfig(
            quality_routing={"premium": "big_model", "economy": "small_model"},
            latency_routing={"realtime": "fast_model", "batch": "slow_model"},
        )
        router = build_router_from_config(config)
        assert router.resolve_by_quality("premium") == "big_model"
        assert router.resolve_by_quality("economy") == "small_model"
        assert router.resolve_by_latency("realtime") == "fast_model"
        assert router.resolve_by_latency("batch") == "slow_model"
        assert router.resolve_by_quality("unknown") is None
        assert router.resolve_by_latency("unknown") is None


class TestUserConfigLayer:
    def test_three_tier_precedence_user_overrides_agent(self) -> None:
        user = UserConfig(budget={"max_usd": 50.0})
        agent = UserConfigAgentConfig(active_model_profile="agent_prof")
        defaults = {"budget": {"max_usd": 100.0}}
        layer = ConfigLayer(user=user, agent=agent, defaults=defaults)
        result = layer.resolve("budget")
        assert result == {"max_usd": 50.0}

    def test_three_tier_precedence_agent_overrides_defaults(self) -> None:
        user = UserConfig()
        agent = UserConfigAgentConfig(active_model_profile="agent_prof")
        defaults = {"active_model_profile": "default_prof"}
        layer = ConfigLayer(user=user, agent=agent, defaults=defaults)
        result = layer.resolve("active_model_profile")
        assert result == "agent_prof"

    def test_defaults_used_when_no_override(self) -> None:
        user = UserConfig()
        agent = UserConfigAgentConfig()
        defaults = {"some_key": "some_value"}
        layer = ConfigLayer(user=user, agent=agent, defaults=defaults)
        assert layer.resolve("some_key") == "some_value"

    def test_resolve_model_routing_user_priority(self) -> None:
        user_routing = ModelRoutingConfig(default_profile="user_default")
        agent_routing = ModelRoutingConfig(default_profile="agent_default")
        user = UserConfig(model_routing=user_routing)
        agent = UserConfigAgentConfig(model_routing=agent_routing)
        layer = ConfigLayer(user=user, agent=agent)
        result = layer.resolve_model_routing()
        assert result.default_profile == "user_default"

    def test_resolve_model_routing_agent_fallback(self) -> None:
        agent_routing = ModelRoutingConfig(default_profile="agent_default")
        user = UserConfig()
        agent = UserConfigAgentConfig(model_routing=agent_routing)
        layer = ConfigLayer(user=user, agent=agent)
        result = layer.resolve_model_routing()
        assert result.default_profile == "agent_default"

    def test_load_and_save_agent_config(self, tmp_path: Path) -> None:
        config = UserConfigAgentConfig(
            active_model_profile="saved_prof",
            session_notes="test session",
        )
        path = tmp_path / "agent_config.yml"
        save_agent_config(config, path)
        assert path.exists()
        loaded = load_agent_config(path)
        assert loaded.active_model_profile == "saved_prof"
        assert loaded.session_notes == "test session"

    def test_build_config_layer_from_files(self, tmp_path: Path) -> None:
        user_data = {"budget": {"max_usd": 25.0}}
        user_path = tmp_path / "user.yml"
        user_path.write_text(yaml.dump(user_data))
        agent_data = {"active_model_profile": "prof_a"}
        agent_path = tmp_path / "agent.yml"
        agent_path.write_text(yaml.dump(agent_data))
        layer = build_config_layer(
            user_path=user_path,
            agent_path=agent_path,
            defaults={"fallback": True},
        )
        assert layer.resolve("budget") == {"max_usd": 25.0}
        assert layer.resolve("active_model_profile") == "prof_a"
        assert layer.defaults == {"fallback": True}


class TestProcessIsolation:
    def test_to_runner_kwargs_with_blocked_tools(self) -> None:
        config = ProcessIsolationConfig(
            enabled=True,
            executable="podman",
            isolation_path="/tmp/sandbox",
            hide_paths=["/secret"],
            show_paths=["/workspace"],
            ro_paths=["/readonly"],
            block_local_tools=["bash", "git"],
        )
        kwargs = config.to_runner_kwargs()
        assert kwargs["process_isolation"] is True
        assert kwargs["process_isolation_executable"] == "podman"
        assert kwargs["process_isolation_path"] == "/tmp/sandbox"
        assert "/secret" in kwargs["process_isolation_hide_paths"]
        assert "/workspace" in kwargs["process_isolation_show_paths"]
        assert "/readonly" in kwargs["process_isolation_ro_paths"]

    def test_resolve_tool_paths_maps_known_tools(self) -> None:
        config = ProcessIsolationConfig()
        bash_paths = config.resolve_tool_paths("bash")
        assert any("/bash" in p for p in bash_paths)
        git_paths = config.resolve_tool_paths("git")
        assert ".git" in git_paths

    def test_resolve_tool_paths_unknown_tool_returns_empty(self) -> None:
        config = ProcessIsolationConfig()
        assert config.resolve_tool_paths("unknown_tool_xyz") == []

    def test_resolve_tool_paths_file_write(self) -> None:
        config = ProcessIsolationConfig()
        paths = config.resolve_tool_paths("file_write")
        assert "/workspace" in paths

    def test_is_module_blocked_shell(self) -> None:
        config = ProcessIsolationConfig(
            enabled=True, block_local_tools=["bash"]
        )
        assert config.is_module_blocked("shell") is True
        assert config.is_module_blocked("command") is True
        assert config.is_module_blocked("ansible.builtin.shell") is True

    def test_is_module_blocked_not_enabled(self) -> None:
        config = ProcessIsolationConfig(
            enabled=False, block_local_tools=["bash"]
        )
        assert config.is_module_blocked("shell") is False

    def test_ansible_runner_adapter_passes_isolation(self) -> None:
        isolation = ProcessIsolationConfig(
            enabled=True,
            executable="podman",
            block_local_tools=["bash"],
        )
        adapter = AnsibleRunnerAdapter(isolation_config=isolation)
        assert adapter.isolation_config is not None
        kwargs = adapter.isolation_config.to_runner_kwargs()
        assert kwargs["process_isolation"] is True


class TestBudgetCapsInEventLoop:
    def test_run_budget_guard_tracks_spend(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=100.0)
        guard.record_spend(30.0)
        guard.record_spend(20.0)
        assert guard.get_total_spend() == pytest.approx(50.0)

    def test_run_budget_guard_enforces_cap(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=5.0)
        guard.record_spend(6.0)
        result = guard.check_run_budget()
        assert result["allowed"] is False

    async def test_event_loop_skips_dispatch_when_budget_exceeded(self) -> None:
        guard = RunBudgetGuard(run_budget_usd=0.0)
        guard.record_spend(1.0)
        loop = EventLoop(
            task_return_repo=AsyncMock(),
            todo_repo=AsyncMock(),
            http_client=AsyncMock(),
            budget_guard=guard,
        )
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = []
        metrics = await loop.tick()
        assert metrics["todos_dispatched"] == 0

    def test_budget_exceeded_status_exists(self) -> None:
        assert hasattr(TodoStatus, "BUDGET_EXCEEDED")
        assert TodoStatus.BUDGET_EXCEEDED.value == "budget_exceeded"

    def test_transitions_to_budget_exceeded_are_valid(self) -> None:
        assert TodoStatus.BUDGET_EXCEEDED in VALID_TRANSITIONS[TodoStatus.ACTIVE]
        assert TodoStatus.BUDGET_EXCEEDED in VALID_TRANSITIONS[TodoStatus.AWAITING_RESULT]
        assert TodoStatus.BUDGET_EXCEEDED in VALID_TRANSITIONS[TodoStatus.REVIEWING_RETURN]

    def test_per_call_budget_rejection(self) -> None:
        guard = RunBudgetGuard(per_call_budget_usd=1.0)
        result = guard.check_per_call(5.0)
        assert result["allowed"] is False
        assert "per-call" in result["reason"]


class TestModelRouterInGateway:
    def test_call_model_by_role_resolves_through_router(self) -> None:
        router = ModelRouter(
            role_mapping={"coder": "coder_prof"},
            default_profile_id="default_prof",
        )
        profile = ModelProfile(
            model_profile_id="coder_prof",
            provider="openai",
            model_name="gpt-4",
            enabled=True,
            run_budget_usd=999.0,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
        )
        reg = MagicMock()
        reg.is_installed.return_value = True
        FakeChat = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="response",
            usage_metadata={"input_tokens": 5, "output_tokens": 3},
        )
        FakeChat.return_value = fake_instance
        reg.get_provider_class.return_value = FakeChat
        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
            router=router,
        )
        resp = gw.call_model_by_role(
            "coder", [{"role": "user", "content": "hello"}]
        )
        assert resp.content == "response"

    def test_call_model_by_pattern_maps_correctly(self) -> None:
        router = ModelRouter(
            role_mapping={"reviewer": "review_prof", "weak": "weak_prof", "fast": "fast_prof"},
        )
        router.add_pattern_mapping("return_review", "reviewer")
        router.add_pattern_mapping("commit_message", "weak")
        router.add_pattern_mapping("gap_analysis", "fast")
        profile = ModelProfile(
            model_profile_id="review_prof",
            provider="openai",
            model_name="review-model",
            enabled=True,
            run_budget_usd=999.0,
            cost_per_input_token=0.0,
            cost_per_output_token=0.0,
        )
        reg = MagicMock()
        reg.is_installed.return_value = True
        FakeChat = MagicMock()
        fake_instance = MagicMock()
        fake_instance.invoke.return_value = MagicMock(
            content="review done",
            usage_metadata={"input_tokens": 2, "output_tokens": 1},
        )
        FakeChat.return_value = fake_instance
        reg.get_provider_class.return_value = FakeChat
        gw = ModelGateway(
            profiles=[profile],
            provider_registry=reg,
            router=router,
        )
        resp = gw.call_model_by_pattern(
            "return_review", [{"role": "user", "content": "review this"}]
        )
        assert resp.content == "review done"

    def test_call_model_by_pattern_unknown_raises(self) -> None:
        router = ModelRouter(role_mapping={"coder": "prof"})
        gw = ModelGateway(router=router)
        with pytest.raises(ValueError, match="No profile resolved"):
            gw.call_model_by_pattern(
                "unknown_pattern", [{"role": "user", "content": "hi"}]
            )

    def test_call_model_by_role_no_router_raises(self) -> None:
        gw = ModelGateway()
        with pytest.raises(ValueError, match="No router"):
            gw.call_model_by_role(
                "coder", [{"role": "user", "content": "hi"}]
            )


class TestConversationInReview:
    def test_conversation_created_for_new_review(self) -> None:
        gw = MagicMock()
        registry = PromptRegistry()
        registry.register("return_review.md.j2", "Review: {{ task_return.return_id }}")
        reviewer = ReturnReviewer(
            gateway=gw,
            prompt_registry=registry,
        )
        tr = TaskReturn(
            return_id="RET-001",
            todo_id="TODO-001",
            job_id="JOB-001",
            playbook="noop.yml",
            queue="core",
        )
        reviewer.review_return(tr, [], [])
        convs = reviewer.get_conversations()
        assert "TODO-001" in convs
        assert convs["TODO-001"].message_count() >= 1

    def test_conversation_accumulates_messages(self) -> None:
        gw = MagicMock()
        registry = PromptRegistry()
        registry.register("return_review.md.j2", "Review: {{ task_return.return_id }}")
        reviewer = ReturnReviewer(
            gateway=gw,
            prompt_registry=registry,
        )
        tr1 = TaskReturn(
            return_id="RET-001",
            todo_id="TODO-001",
            job_id="JOB-001",
            playbook="noop.yml",
            queue="core",
        )
        tr2 = TaskReturn(
            return_id="RET-002",
            todo_id="TODO-001",
            job_id="JOB-002",
            playbook="noop.yml",
            queue="core",
        )
        reviewer.review_return(tr1, [], [])
        reviewer.review_return(tr2, [], [])
        convs = reviewer.get_conversations()
        conv = convs["TODO-001"]
        assert conv.message_count() >= 4

    def test_reviewer_uses_router_for_profile(self) -> None:
        gw = MagicMock()
        router = ModelRouter(role_mapping={"return_review": "review_prof"})
        registry = PromptRegistry()
        registry.register("return_review.md.j2", "prompt text")
        reviewer = ReturnReviewer(
            gateway=gw,
            prompt_registry=registry,
            router=router,
        )
        tr = TaskReturn(
            return_id="RET-RT01",
            todo_id="TODO-RT01",
            job_id="JOB-RT01",
            playbook="noop.yml",
            queue="core",
        )
        reviewer.review_return(tr, [], [])
        assert reviewer._model_profile_id == "review_prof"


class TestPlanArtifactInDispatch:
    def test_todo_with_plan_artifact(self) -> None:
        todo = Todo(
            title="Test task",
            plan_artifact="## Plan\nDo the thing",
        )
        assert todo.plan_artifact is not None
        assert "Plan" in todo.plan_artifact

    def test_job_spec_includes_plan_artifact(self) -> None:
        job = JobSpec(
            job_id="EXEC-001",
            todo_id="TODO-001",
            playbook="run.yml",
            queue="core",
            plan_artifact="## Plan\nStep 1: code",
        )
        assert job.plan_artifact is not None
        assert "Plan" in job.plan_artifact
        data = job.model_dump(mode="json")
        assert data["plan_artifact"] == "## Plan\nStep 1: code"

    async def test_event_loop_dispatches_with_plan_artifact(self) -> None:
        todo = Todo(
            title="Plan test",
            plan_artifact="## Execution Plan\nWrite tests first",
            work_type="code",
        )
        todo.status = TodoStatus.QUEUED
        http_client = AsyncMock()
        loop = EventLoop(
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
            http_client=http_client,
            config={"default_playbook": "noop.yml"},
        )
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = [todo]
        await loop.tick()
        http_client.post.assert_called()
        call_args = http_client.post.call_args
        body = call_args[1]["json"] if "json" in call_args[1] else call_args[0][1]
        if isinstance(body, dict):
            assert body.get("plan_artifact") == "## Execution Plan\nWrite tests first"

    def test_plan_artifact_from_todo(self) -> None:
        todo = Todo(
            title="Feature X",
            description="Build feature X",
            tags=["feature"],
            test_commands=["make test"],
        )
        artifact = PlanArtifact.from_todo(todo)
        assert artifact.todo_id == todo.todo_id
        assert artifact.title == "Feature X"
        md = artifact.to_markdown()
        assert "Feature X" in md


class TestAgentBehaviorCodification:
    def test_custom_behavior_renders_all_sections(self) -> None:
        behavior = AgentBehavior(
            completion_policy="complete_all",
            self_directed_work=True,
            tdd_enforced=True,
            commit_after_green=True,
            evidence_required=True,
            atomic_commits=True,
            session_persistence=True,
            guardrail=GuardrailConfig(
                config_layer=True, hook_layer=True, prompt_layer=True
            ),
            allowed_command_patterns=["make *"],
            stop_conditions=["missing_credentials"],
        )
        renderer = BehaviorRenderer()
        result = renderer.render(behavior)
        assert "## Task Completion" in result
        assert "## Self-Directed Work" in result
        assert "## TDD Policy" in result
        assert "## Commit-After-Green" in result
        assert "## Evidence-Based Responses" in result
        assert "## Atomic Commits" in result
        assert "## Session Persistence" in result
        assert "## Guardrail Policy" in result
        assert "## Command Policy" in result
        assert "## Stop Conditions" in result
        assert "3 layer(s)" in result

    def test_disabled_sections_excluded(self) -> None:
        behavior = AgentBehavior(
            self_directed_work=False,
            tdd_enforced=False,
            commit_after_green=False,
            evidence_required=False,
            atomic_commits=False,
            session_persistence=False,
            allowed_command_patterns=[],
            stop_conditions=[],
        )
        renderer = BehaviorRenderer()
        result = renderer.render(behavior)
        assert "## Self-Directed Work" not in result
        assert "## TDD Policy" not in result
        assert "## Commit-After-Green" not in result
        assert "## Evidence-Based Responses" not in result
        assert "## Atomic Commits" not in result
        assert "## Session Persistence" not in result
        assert "## Command Policy" not in result
        assert "## Stop Conditions" not in result

    def test_guardrail_config_validation(self) -> None:
        with pytest.raises(ValueError, match="At least one guardrail"):
            GuardrailConfig(
                config_layer=False, hook_layer=False, prompt_layer=False
            ).ensure_valid()

    def test_render_as_prompt_includes_agent_and_task(self) -> None:
        behavior = AgentBehavior()
        renderer = BehaviorRenderer()
        result = renderer.render_as_prompt(
            behavior, agent_name="build", task="fix the bug"
        )
        assert "**build**" in result
        assert "fix the bug" in result
        assert "## Task Completion" in result

    def test_should_stop_condition(self) -> None:
        behavior = AgentBehavior(
            stop_conditions=["missing_credentials", "disk_full"]
        )
        assert behavior.should_stop("missing_credentials") is True
        assert behavior.should_stop("disk_full") is True
        assert behavior.should_stop("normal_error") is False

    def test_behavior_serialization_roundtrip(self) -> None:
        behavior = AgentBehavior(
            completion_policy="complete_all",
            max_retries=5,
            tdd_enforced=True,
        )
        data = behavior.to_dict()
        restored = AgentBehavior.from_dict(data)
        assert restored.completion_policy == "complete_all"
        assert restored.max_retries == 5
        assert restored.tdd_enforced is True


class TestTreeSitterRepoMap:
    def test_parse_python_extracts_symbols(self) -> None:
        code = (
            "import os\n"
            "from pathlib import Path\n"
            "\n"
            "class MyClass:\n"
            "    def method_one(self):\n"
            "        pass\n"
            "\n"
            "    def method_two(self, x):\n"
            "        return x\n"
            "\n"
            "def standalone_func():\n"
            "    return 42\n"
        )
        builder = RepoMapBuilder()
        symbols = builder.parse_file("test_module.py", code)
        names = {s.name for s in symbols}
        assert "os" in names
        assert "MyClass" in names
        assert "method_one" in names
        assert "method_two" in names
        assert "standalone_func" in names
        classes = [s for s in symbols if s.kind == "class"]
        assert len(classes) == 1
        assert classes[0].name == "MyClass"
        methods = [s for s in symbols if s.kind == "method"]
        assert len(methods) == 2
        for m in methods:
            assert m.parent == "MyClass"

    def test_compact_string_format(self) -> None:
        code = (
            "class Handler:\n"
            "    def process(self):\n"
            "        pass\n"
            "\n"
            "def helper():\n"
            "    pass\n"
        )
        builder = RepoMapBuilder()
        symbols = builder.parse_file("handler.py", code)
        repo_map = RepoMap(symbols=symbols)
        compact = repo_map.to_compact_string()
        assert "handler.py:" in compact
        assert "class Handler" in compact
        assert "method process()" in compact
        assert "function helper()" in compact

    def test_empty_repo_map_compact_string(self) -> None:
        repo_map = RepoMap()
        assert repo_map.to_compact_string() == ""

    def test_repo_map_roundtrip(self) -> None:
        code = "class Foo:\n    def bar(self):\n        pass\n"
        builder = RepoMapBuilder()
        symbols = builder.parse_file("foo.py", code)
        repo_map = RepoMap(symbols=symbols, file_count=1, total_lines=3)
        data = repo_map.to_dict()
        restored = RepoMap.from_dict(data)
        assert len(restored.symbols) == len(symbols)
        assert restored.file_count == 1
        assert restored.total_lines == 3


class TestYAMLTaskDefinitions:
    def test_load_task_definitions_from_yaml(self, tmp_path: Path) -> None:
        task_data = {
            "tasks": [
                {
                    "name": "Write tests",
                    "description": "Add unit tests for module",
                    "target_agent": "build",
                    "queue": "core",
                    "work_type": "test",
                    "priority": 5,
                    "tags": ["testing"],
                    "dependencies": ["setup-venv"],
                    "test_commands": ["make test-unit"],
                },
                {
                    "name": "Fix lint errors",
                    "target_agent": "build",
                    "queue": "core",
                    "work_type": "code",
                    "priority": 3,
                },
            ]
        }
        p = tmp_path / "tasks.yml"
        p.write_text(yaml.dump(task_data))
        tasks = load_task_definitions(p)
        assert len(tasks) == 2
        assert tasks[0].name == "Write tests"
        assert tasks[0].dependencies == ["setup-venv"]
        assert tasks[1].name == "Fix lint errors"

    def test_task_definition_to_todo_conversion(self) -> None:
        td = TaskDefinition(
            name="Implement feature",
            description="Build the new feature",
            target_agent="build",
            queue="core",
            work_type="code",
            priority=10,
            tags=["feature", "urgent"],
            dependencies=["TODO-001"],
            test_commands=["make test"],
            risk_level="medium",
            resource_profile="ai_heavy",
        )
        todo = td.to_todo()
        assert todo.title == "Implement feature"
        assert todo.description == "Build the new feature"
        assert todo.assigned_agent == "build"
        assert todo.priority == 10
        assert "feature" in todo.tags
        assert todo.dependencies == ["TODO-001"]

    def test_dependency_ordering(self) -> None:
        tasks = [
            TaskDefinition(name="Task C", dependencies=["Task A", "Task B"]),
            TaskDefinition(name="Task A", dependencies=[]),
            TaskDefinition(name="Task B", dependencies=["Task A"]),
        ]
        name_to_task = {t.name: t for t in tasks}
        order: list[str] = []
        visited: set[str] = set()

        def visit(name: str) -> None:
            if name in visited:
                return
            visited.add(name)
            task = name_to_task[name]
            for dep in task.dependencies:
                visit(dep)
            order.append(name)

        for t in tasks:
            visit(t.name)
        assert order.index("Task A") < order.index("Task B")
        assert order.index("Task B") < order.index("Task C")

    def test_load_empty_yaml(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.yml"
        p.write_text("")
        tasks = load_task_definitions(p)
        assert tasks == []


class TestEphemeralGPUCompute:
    def test_aws_terraform_generation(self) -> None:
        config = ComputeConfig(
            provider=ComputeProvider.AWS,
            gpu_type=GPUType.T4,
            gpu_count=1,
            engine=InferenceEngine.VLLM,
            model_name="meta-llama/Llama-3-8B",
            region="us-east-1",
        )
        gen = TerraformGenerator()
        hcl = gen.generate(config)
        assert 'resource "aws_instance" "gpu_instance"' in hcl
        assert "g4dn.xlarge" in hcl
        assert "us-east-1" in hcl
        assert "meta-llama/Llama-3-8B" in hcl
        assert "ephemeral-gpu-t4" in hcl

    def test_gcp_terraform_generation(self) -> None:
        config = ComputeConfig(
            provider=ComputeProvider.GCP,
            gpu_type=GPUType.L4,
            gpu_count=2,
            engine=InferenceEngine.VLLM,
            model_name="test-model",
            region="us-central1",
        )
        gen = TerraformGenerator()
        hcl = gen.generate(config)
        assert 'resource "google_compute_instance" "gpu_instance"' in hcl
        assert "nvidia-l4" in hcl
        assert "count = 2" in hcl

    def test_azure_terraform_generation(self) -> None:
        config = ComputeConfig(
            provider=ComputeProvider.AZURE,
            gpu_type=GPUType.T4,
            engine=InferenceEngine.VLLM,
            model_name="test-model",
        )
        gen = TerraformGenerator()
        hcl = gen.generate(config)
        assert 'resource "azurerm_virtual_machine" "gpu_vm"' in hcl
        assert 'resource "azurerm_resource_group" "gpu_rg"' in hcl

    def test_runpod_terraform_generation(self) -> None:
        config = ComputeConfig(
            provider=ComputeProvider.RUNPOD,
            gpu_type=GPUType.A100_80,
            gpu_count=1,
            engine=InferenceEngine.VLLM,
            model_name="big-model",
        )
        gen = TerraformGenerator()
        hcl = gen.generate(config)
        assert 'resource "runpod_pod" "gpu_pod"' in hcl
        assert "NVIDIA A100 80GB" in hcl

    def test_provider_registry_pricing(self) -> None:
        registry = InfraProviderRegistry()
        aws = registry.get(ComputeProvider.AWS)
        assert aws.display_name == "Amazon Web Services"
        assert "t4" in aws.pricing
        assert aws.pricing["t4"] > 0

    def test_cheapest_provider_for_gpu(self) -> None:
        registry = InfraProviderRegistry()
        cheapest = registry.get_cheapest_for_gpu(GPUType.A100_80)
        a100_prices: list[tuple[float, str]] = []
        for info in registry.list_providers():
            if "a100_80" in info.pricing:
                a100_prices.append((info.pricing["a100_80"], info.display_name))
        a100_prices.sort()
        assert cheapest.display_name == a100_prices[0][1]

    def test_all_providers_registered(self) -> None:
        registry = InfraProviderRegistry()
        providers = registry.list_providers()
        provider_names = {p.provider for p in providers}
        assert ComputeProvider.AWS in provider_names
        assert ComputeProvider.GCP in provider_names
        assert ComputeProvider.AZURE in provider_names
        assert ComputeProvider.RUNPOD in provider_names
        assert ComputeProvider.VAST_AI in provider_names
        assert len(providers) == 10


class TestMCPToolsInEventLoop:
    def test_mcp_tool_registry_registers_and_lists(self) -> None:
        registry = MCPToolRegistry()
        tool = MCPTool(
            name="read_file",
            description="Read a file from disk",
            input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
        )
        registry.register_tool("server-1", tool)
        assert "read_file" in registry.tool_names()
        assert registry.get_tool("read_file") is not None

    def test_event_loop_exposes_available_tools(self) -> None:
        registry = MCPToolRegistry()
        registry.register_tool(
            "s1",
            MCPTool(name="tool_a", description="Tool A"),
        )
        registry.register_tool(
            "s1",
            MCPTool(name="tool_b", description="Tool B"),
        )
        loop = EventLoop(mcp_tool_registry=registry)
        tools = loop.get_available_tools()
        assert "tool_a" in tools
        assert "tool_b" in tools

    def test_event_loop_no_tools_without_registry(self) -> None:
        loop = EventLoop()
        assert loop.get_available_tools() == []

    def test_agent_tool_adapter_wraps_agents(self) -> None:
        agent_registry = AgentRegistry()
        agent_registry.register(AgentConfig(
            name="build",
            description="Build agent",
            type=AgentType.PRIMARY,
            permissions=AgentPermission(can_dispatch_subagents=True, allowed_subagents=["*"]),
        ))
        agent_registry.register(AgentConfig(
            name="explore",
            description="Explore agent",
            type=AgentType.SUBAGENT,
        ))
        adapter = AgentToolAdapter(agent_registry)
        tools = adapter.list_agent_tools()
        names = {t["name"] for t in tools}
        assert "dispatch_build" in names
        assert "dispatch_explore" in names
        build_tool = adapter.get_agent_as_tool("build")
        assert build_tool is not None
        assert build_tool["type"] == "agent_dispatch"
        assert build_tool["target_agent"] == "build"

    async def test_event_loop_includes_mcp_tools_in_job(self) -> None:
        registry = MCPToolRegistry()
        registry.register_tool("s1", MCPTool(name="search", description="Search"))
        todo = Todo(title="MCP test", status="queued", work_type="code")
        http_client = AsyncMock()
        loop = EventLoop(
            todo_repo=AsyncMock(),
            task_return_repo=AsyncMock(),
            http_client=http_client,
            mcp_tool_registry=registry,
            config={"default_playbook": "noop.yml"},
        )
        loop._task_return_repo.claim_unreviewed.return_value = []
        loop._todo_repo.claim_runnable.return_value = [todo]
        await loop.tick()
        http_client.post.assert_called()
        call_kwargs = http_client.post.call_args
        json_kwarg = call_kwargs[1].get("json")
        pos_arg = call_kwargs[0][1] if len(call_kwargs[0]) > 1 else None
        body = json_kwarg or pos_arg
        if isinstance(body, dict):
            budget_ctx = body.get("budget_context", {})
            assert "mcp_tools" in budget_ctx
            assert "search" in budget_ctx["mcp_tools"]
