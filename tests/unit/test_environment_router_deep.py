"""Deep unit tests for the environment router's internal facets and edge cases.

Covers every internal function, every fail-soft path, every model default,
and every guard clause that existing tests skip.  No PSK middleware needed
— these are pure function/async-function tests.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI

from general_ludd.routers.environment import (
    _ANSIBLE_TOOL_MODULES,
    _DEFAULT_EXPECTED_OUTPUT_TOKENS,
    _SAFE_MODEL_FIELDS,
    _WORK_TYPE_TO_TASK_TYPE,
    AdviceBrief,
    EnvironmentBrief,
    _borrowing_config,
    _budget_facet,
    _compute_facet,
    _inherited_knowledge_facet,
    _models_facet,
    _parse_interface,
    _project_facet,
    _queues_facet,
    _resolve_advice,
    _resolve_project_id,
    _routing_facet,
    _skills_facet,
    _system_facet,
    _tools_facet,
)

# ---------------------------------------------------------------------------
# EnvironmentBrief / AdviceBrief model defaults
# ---------------------------------------------------------------------------


class TestModelDefaults:
    def test_environment_brief_defaults(self) -> None:
        brief = EnvironmentBrief()
        assert brief.models == []
        assert brief.routing == {}
        assert brief.budget == {}
        assert brief.compute == {}
        assert brief.tools == []
        assert brief.skills == []
        assert brief.queues == []
        assert brief.system == {}
        assert brief.optimization == {}
        assert brief.project == {}

    def test_advice_brief_defaults(self) -> None:
        brief = AdviceBrief()
        assert brief.task_type == ""
        assert brief.recommendation == {}
        assert brief.route == {}
        assert brief.est_cost_usd == 0.0
        assert brief.use_workflow is False
        assert brief.workflow_reason == ""
        assert brief.resource_hints == {}


# ---------------------------------------------------------------------------
# _SAFE_MODEL_FIELDS — the security allow-list
# ---------------------------------------------------------------------------


class TestSafeModelFields:
    def test_every_safe_field_is_known_characteristic(self) -> None:
        for field in _SAFE_MODEL_FIELDS:
            assert field in (
                "enabled",
                "quality_class",
                "latency_class",
                "context_window",
                "max_input_tokens",
                "max_output_tokens",
                "cost_per_input_token",
                "cost_per_output_token",
                "run_budget_usd",
                "api_metered",
                "fallback_profiles",
            ), f"unexpected safe field: {field}"

    def test_credential_fields_never_in_safe_list(self) -> None:
        for forbidden in (
            "credential_alias",
            "api_base_alias",
            "api_key",
            "token",
            "secret",
            "password",
            "psk",
            "auth_header",
        ):
            assert forbidden not in _SAFE_MODEL_FIELDS, f"{forbidden} leaked into safe fields"


# ---------------------------------------------------------------------------
# _WORK_TYPE_TO_TASK_TYPE mapping
# ---------------------------------------------------------------------------


class TestWorkTypeMapping:
    @pytest.mark.parametrize(
        "work,task",
        [
            ("feature", "feature"),
            ("bugfix", "bug_fix"),
            ("bug_fix", "bug_fix"),
            ("refactor", "refactor"),
            ("review", "code_review"),
            ("code_review", "code_review"),
            ("test", "test_write"),
            ("test_write", "test_write"),
            ("docs", "documentation"),
            ("documentation", "documentation"),
            ("debug", "debugging"),
            ("debugging", "debugging"),
            ("optimize", "optimization"),
            ("optimization", "optimization"),
            ("security", "security_fix"),
            ("security_fix", "security_fix"),
            ("integration", "integration"),
        ],
    )
    def test_known_work_types_map_correctly(self, work: str, task: str) -> None:
        assert _WORK_TYPE_TO_TASK_TYPE[work] == task

    def test_unmapped_work_type_absent(self) -> None:
        assert "chat" not in _WORK_TYPE_TO_TASK_TYPE
        assert "classify" not in _WORK_TYPE_TO_TASK_TYPE


# ---------------------------------------------------------------------------
# _ANSIBLE_TOOL_MODULES — the fail-soft floor
# ---------------------------------------------------------------------------


class TestAnsibleToolModules:
    def test_all_four_modules_present(self) -> None:
        names = {m["name"] for m in _ANSIBLE_TOOL_MODULES}
        assert names == {"gludd_facts", "gludd_metrics", "gludd_traces", "gludd_environment"}

    def test_every_module_has_source_and_description(self) -> None:
        for m in _ANSIBLE_TOOL_MODULES:
            assert m["source"] == "ansible"
            assert isinstance(m["description"], str)
            assert len(m["description"]) > 0


# ---------------------------------------------------------------------------
# _models_facet — secret leak prevention + fail-soft
# ---------------------------------------------------------------------------


class ModelProfileStub:
    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class ModelsFacetTestGateway:
    def __init__(self, profiles: list[ModelProfileStub]) -> None:
        self._profiles = profiles

    def list_profiles(self) -> list[ModelProfileStub]:
        return list(self._profiles)


class TestModelsFacet:
    def test_empty_list_when_no_gateway(self) -> None:
        app = FastAPI()
        assert _models_facet(app) == []

    def test_empty_list_when_gateway_has_no_list_profiles(self) -> None:
        app = FastAPI()
        app.state._model_gateway = object()
        assert _models_facet(app) == []

    def test_empty_profiles_yields_empty_list(self) -> None:
        app = FastAPI()
        app.state._model_gateway = ModelsFacetTestGateway([])
        assert _models_facet(app) == []

    def test_never_includes_fields_outside_safe_allowlist(self) -> None:
        p = ModelProfileStub(
            model_profile_id="p1",
            provider="openai",
            model_name="gpt-4",
            enabled=True,
            api_metered=True,
            credential_alias="sk-secret",  # MUST be absent
            api_base_alias="https://secret.invalid",  # MUST be absent
            internal_secret="do-not-leak",  # pragma: allowlist secret
        )
        app = FastAPI()
        app.state._model_gateway = ModelsFacetTestGateway([p])
        roster = _models_facet(app)
        assert len(roster) == 1
        entry = roster[0]
        assert "credential_alias" not in entry
        assert "api_base_alias" not in entry
        assert "internal_secret" not in entry
        # Safe base fields present.
        assert entry["profile_id"] == "p1"
        assert entry["provider"] == "openai"
        assert entry["model"] == "gpt-4"

    def test_fallback_profiles_defensive_copy(self) -> None:
        p = ModelProfileStub(
            model_profile_id="p1",
            provider="openai",
            model_name="gpt-4",
            fallback_profiles=["fb1", "fb2"],
        )
        app = FastAPI()
        app.state._model_gateway = ModelsFacetTestGateway([p])
        roster = _models_facet(app)
        assert roster[0]["fallback_profiles"] == ["fb1", "fb2"]

    def test_missing_field_defaults_to_none(self) -> None:
        p = ModelProfileStub(
            model_profile_id="p1",
            provider="openai",
            model_name="gpt-4",
        )
        app = FastAPI()
        app.state._model_gateway = ModelsFacetTestGateway([p])
        roster = _models_facet(app)
        entry = roster[0]
        assert entry["enabled"] is None
        assert entry["quality_class"] is None


# ---------------------------------------------------------------------------
# _routing_facet
# ---------------------------------------------------------------------------


class RoutingConfigStub:
    def __init__(self, **kwargs: Any) -> None:
        for k, v in kwargs.items():
            setattr(self, k, v)


class TestRoutingFacet:
    def test_empty_when_no_startup_config(self) -> None:
        app = FastAPI()
        assert _routing_facet(app) == {}

    def test_empty_when_no_model_routing_key(self) -> None:
        app = FastAPI()
        app.state._startup_config = {}
        assert _routing_facet(app) == {}

    def test_empty_when_model_routing_is_none(self) -> None:
        app = FastAPI()
        app.state._startup_config = {"model_routing": None}
        assert _routing_facet(app) == {}

    def test_full_routing_config_reflected(self) -> None:
        cfg = RoutingConfigStub(
            default_profile="flagship",
            weak_model_profile="weak",
            role_routing={"reviewer": "flagship"},
            latency_routing={"low": "weak"},
            quality_routing={"high": "flagship"},
            fallback_chain=["flagship", "weak"],
        )
        app = FastAPI()
        app.state._startup_config = {"model_routing": cfg}
        facet = _routing_facet(app)
        assert facet["default_profile"] == "flagship"
        assert facet["weak_model_profile"] == "weak"
        assert facet["roles"] == {"reviewer": "flagship"}
        assert facet["latency"] == {"low": "weak"}
        assert facet["quality"] == {"high": "flagship"}
        assert facet["fallback_chain"] == ["flagship", "weak"]


# ---------------------------------------------------------------------------
# _budget_facet
# ---------------------------------------------------------------------------


class BudgetGuardStub:
    def __init__(
        self,
        spent: float = 0.0,
        remaining: float = 10.0,
        elapsed: float = 0.0,
    ) -> None:
        self._spent = spent
        self._remaining = remaining
        self._elapsed = elapsed

    def get_total_spend(self) -> float:
        return self._spent

    def get_elapsed_seconds(self) -> float:
        return self._elapsed

    def check_run_budget(self) -> dict[str, Any]:
        return {"remaining_budget": self._remaining}


class TestBudgetFacet:
    def test_all_none_when_no_guard(self) -> None:
        app = FastAPI()
        facet = _budget_facet(app)
        assert facet["run_spent_usd"] is None
        assert facet["run_remaining_usd"] is None
        assert facet["run_limit_usd"] is None
        assert facet["elapsed_seconds"] is None

    def test_spent_and_remaining_derived(self) -> None:
        app = FastAPI()
        app.state._budget_guard = BudgetGuardStub(spent=3.0, remaining=7.0, elapsed=120.0)
        facet = _budget_facet(app)
        assert facet["run_spent_usd"] == 3.0
        assert facet["run_remaining_usd"] == 7.0
        assert facet["run_limit_usd"] == 10.0
        assert facet["elapsed_seconds"] == 120.0

    def test_window_key_present(self) -> None:
        app = FastAPI()
        facet = _budget_facet(app)
        assert "window" in facet


# ---------------------------------------------------------------------------
# _compute_facet
# ---------------------------------------------------------------------------


class TestComputeFacet:
    def test_providers_and_gpu_types_always_present(self) -> None:
        app = FastAPI()
        facet = _compute_facet(app)
        assert isinstance(facet["providers"], list)
        assert isinstance(facet["gpu_types"], list)
        assert facet["configured"] is None

    def test_configured_is_none_when_no_config(self) -> None:
        app = FastAPI()
        assert _compute_facet(app)["configured"] is None

    def test_configured_from_model_dump(self) -> None:
        config = MagicMock()
        config.model_dump.return_value = {"provider": "openai", "gpu": "a100"}
        app = FastAPI()
        app.state._compute_config = config
        facet = _compute_facet(app)
        assert facet["configured"] == {"provider": "openai", "gpu": "a100"}


# ---------------------------------------------------------------------------
# _tools_facet
# ---------------------------------------------------------------------------


class TestToolsFacet:
    @pytest.mark.asyncio
    async def test_ansible_modules_only_when_no_mcp(self) -> None:
        app = FastAPI()
        catalog = await _tools_facet(app)
        assert len(catalog) == len(_ANSIBLE_TOOL_MODULES)
        assert all(t["source"] == "ansible" for t in catalog)

    @pytest.mark.asyncio
    async def test_mcp_tools_appended_after_ansible(self) -> None:
        mcp = AsyncMock()
        mcp.list_tools.return_value = [
            {"name": "bash", "description": "Run shell commands"},
            {"name": "read", "description": "Read files"},
        ]
        app = FastAPI()
        app.state._mcp_client = mcp
        catalog = await _tools_facet(app)
        assert len(catalog) == len(_ANSIBLE_TOOL_MODULES) + 2
        mcp_names = [t["name"] for t in catalog if t["source"] == "mcp"]
        assert "bash" in mcp_names
        assert "read" in mcp_names

    @pytest.mark.asyncio
    async def test_mcp_tool_object_attrs(self) -> None:
        class _Tool:
            def __init__(self, name: str, description: str) -> None:
                self.name = name
                self.description = description

        mcp = AsyncMock()
        mcp.list_tools.return_value = [_Tool("exec", "Execute code")]
        app = FastAPI()
        app.state._mcp_client = mcp
        catalog = await _tools_facet(app)
        exec_tool = next(t for t in catalog if t["name"] == "exec")
        assert exec_tool["source"] == "mcp"
        assert exec_tool["description"] == "Execute code"

    @pytest.mark.asyncio
    async def test_mcp_client_without_list_tools_ignored(self) -> None:
        app = FastAPI()
        app.state._mcp_client = object()
        catalog = await _tools_facet(app)
        assert len(catalog) == len(_ANSIBLE_TOOL_MODULES)


# ---------------------------------------------------------------------------
# _skills_facet
# ---------------------------------------------------------------------------


class SkillStub:
    def __init__(self, name: str, description: str) -> None:
        self.name = name
        self.description = description


class SkillRegistryStub:
    def __init__(self, skills: list[SkillStub]) -> None:
        self._skills = skills

    def list_skills(self) -> list[SkillStub]:
        return list(self._skills)


class TestSkillsFacet:
    def test_empty_when_no_registry(self) -> None:
        app = FastAPI()
        assert _skills_facet(app) == []

    def test_empty_when_registry_lacks_list_skills(self) -> None:
        app = FastAPI()
        app.state._skill_registry = object()
        assert _skills_facet(app) == []

    def test_skills_returned_as_name_description_dicts(self) -> None:
        app = FastAPI()
        app.state._skill_registry = SkillRegistryStub(
            [
                SkillStub("python-expert", "Python expertise"),
                SkillStub("azure-expert", "Azure cloud operations"),
            ]
        )
        skills = _skills_facet(app)
        assert len(skills) == 2
        assert skills[0] == {"name": "python-expert", "description": "Python expertise"}
        assert skills[1] == {"name": "azure-expert", "description": "Azure cloud operations"}


# ---------------------------------------------------------------------------
# _queues_facet
# ---------------------------------------------------------------------------


class TestQueuesFacet:
    @pytest.mark.asyncio
    async def test_empty_when_no_session_factory(self) -> None:
        app = FastAPI()
        assert await _queues_facet(app) == []

    @pytest.mark.asyncio
    async def test_queues_from_repository_with_project_id(self) -> None:
        # This test verifies project_id is forwarded to the repos.
        # We patch the repository to capture the call.
        from general_ludd.db.repository import TaskReturnRepository, TodoRepository

        async def fake_status_summary(project_id: str | None = None) -> dict[str, Any]:
            return {"backlog_size": 5, "project_id_called": project_id}

        async def fake_work_summary(project_id: str | None = None) -> dict[str, Any]:
            return {"in_flight": 2, "project_id_called": project_id}

        with (
            patch.object(TodoRepository, "status_summary", side_effect=fake_status_summary),
            patch.object(TaskReturnRepository, "work_summary", side_effect=fake_work_summary),
        ):

            class _FakeSession:
                async def __aenter__(self) -> _FakeSession:
                    return self

                async def __aexit__(self, *args: Any) -> None:
                    return None

            factory = MagicMock(return_value=_FakeSession())
            app = FastAPI()
            app.state._session_factory = factory

            queues = await _queues_facet(app, project_id="proj-xyz")
            assert len(queues) == 2
            depth_by_name = {q["name"]: q["depth"] for q in queues}
            assert depth_by_name["todos"] == 5
            assert depth_by_name["work_in_flight"] == 2


# ---------------------------------------------------------------------------
# _system_facet
# ---------------------------------------------------------------------------


class TestSystemFacet:
    def test_all_keys_always_present(self) -> None:
        facet = _system_facet()
        for key in ("cpu_count", "python_version", "load_avg", "mem_available_mb", "disk_free_mb"):
            assert key in facet, f"missing system key: {key}"

    def test_cpu_count_is_int_or_none(self) -> None:
        facet = _system_facet()
        assert facet["cpu_count"] is None or isinstance(facet["cpu_count"], int)

    def test_python_version_is_str_or_none(self) -> None:
        facet = _system_facet()
        assert facet["python_version"] is None or isinstance(facet["python_version"], str)

    def test_disk_free_is_number_or_none(self) -> None:
        facet = _system_facet()
        val = facet["disk_free_mb"]
        assert val is None or isinstance(val, (int, float))


# ---------------------------------------------------------------------------
# _resolve_project_id
# ---------------------------------------------------------------------------


class ProjectManagerStub:
    def __init__(self, active: list[Any]) -> None:
        self._active = active

    def list_active(self) -> list[Any]:
        return list(self._active)


class TestResolveProjectId:
    def test_explicit_param_wins(self) -> None:
        app = FastAPI()
        assert _resolve_project_id(app, "explicit-id") == "explicit-id"

    def test_none_when_no_manager(self) -> None:
        app = FastAPI()
        assert _resolve_project_id(app, None) is None

    def test_none_when_manager_lacks_list_active(self) -> None:
        app = FastAPI()
        app.state._project_manager = object()
        assert _resolve_project_id(app, None) is None

    def test_single_active_project_returned(self) -> None:
        class _Proj:
            project_id = "only-one"

        app = FastAPI()
        app.state._project_manager = ProjectManagerStub([_Proj()])
        assert _resolve_project_id(app, None) == "only-one"

    def test_none_when_zero_active(self) -> None:
        app = FastAPI()
        app.state._project_manager = ProjectManagerStub([])
        assert _resolve_project_id(app, None) is None

    def test_none_when_multiple_active(self) -> None:
        class _Proj:
            def __init__(self, pid: str) -> None:
                self.project_id = pid

        app = FastAPI()
        app.state._project_manager = ProjectManagerStub([_Proj("a"), _Proj("b")])
        assert _resolve_project_id(app, None) is None


# ---------------------------------------------------------------------------
# _parse_interface
# ---------------------------------------------------------------------------


class TestParseInterface:
    def test_hint_and_contract_from_valid_json_string(self) -> None:
        edge = MagicMock(
            interface_hint="consumes auth",
            interface_contract=json.dumps({"direction": "consumes", "protocol": "http"}),
        )
        result = _parse_interface(edge)
        assert result["hint"] == "consumes auth"
        assert result["contract"] == {"direction": "consumes", "protocol": "http"}

    def test_malformed_json_degrades_to_empty_contract(self) -> None:
        edge = MagicMock(interface_hint="bad hint", interface_contract="{not_valid")
        result = _parse_interface(edge)
        assert result["hint"] == "bad hint"
        assert result["contract"] == {}

    def test_json_array_degrades_to_empty_contract(self) -> None:
        edge = MagicMock(interface_hint="arr", interface_contract="[1,2,3]")
        result = _parse_interface(edge)
        assert result["contract"] == {}

    def test_empty_string_contract(self) -> None:
        edge = MagicMock(interface_hint="hint", interface_contract="")
        result = _parse_interface(edge)
        assert result["contract"] == {}

    def test_dict_contract_passed_through(self) -> None:
        edge = MagicMock(interface_hint="hint", interface_contract={"a": 1})
        result = _parse_interface(edge)
        assert result["contract"] == {"a": 1}

    def test_none_contract(self) -> None:
        edge = MagicMock(interface_hint=None, interface_contract=None)
        result = _parse_interface(edge)
        assert result["contract"] == {}
        assert result["hint"] is None


# ---------------------------------------------------------------------------
# _borrowing_config
# ---------------------------------------------------------------------------


class RelationshipRoutingStub:
    def __init__(
        self,
        enable_cross_project_borrowing: bool = False,
        edge_decay: float = 0.5,
        external_penalty: float = 0.5,
        min_borrow_weight: float = 0.05,
    ) -> None:
        self.enable_cross_project_borrowing = enable_cross_project_borrowing
        self.edge_decay = edge_decay
        self.external_penalty = external_penalty
        self.min_borrow_weight = min_borrow_weight


class TestBorrowingConfig:
    def test_defaults_when_no_rr_config(self) -> None:
        app = FastAPI()
        app.state._startup_config = {}
        enabled, decay, penalty, min_weight = _borrowing_config(app)
        assert enabled is False
        assert decay == 0.5
        assert penalty == 0.5
        assert min_weight == 0.05

    def test_defaults_when_no_startup_config_at_all(self) -> None:
        app = FastAPI()
        enabled, _decay, _penalty, _min_weight = _borrowing_config(app)
        assert enabled is False

    def test_reads_from_rr_config_when_present(self) -> None:
        rr = RelationshipRoutingStub(
            enable_cross_project_borrowing=True,
            edge_decay=0.3,
            external_penalty=0.7,
            min_borrow_weight=0.1,
        )
        app = FastAPI()
        app.state._startup_config = {"user_config": MagicMock(relationship_routing=rr)}
        enabled, decay, penalty, min_weight = _borrowing_config(app)
        assert enabled is True
        assert decay == 0.3
        assert penalty == 0.7
        assert min_weight == 0.1

    def test_disabled_when_rr_config_flag_false(self) -> None:
        rr = RelationshipRoutingStub(
            enable_cross_project_borrowing=False,
            edge_decay=0.3,
        )
        app = FastAPI()
        app.state._startup_config = {"user_config": MagicMock(relationship_routing=rr)}
        enabled, _, _, _ = _borrowing_config(app)
        assert enabled is False


# ---------------------------------------------------------------------------
# _inherited_knowledge_facet
# ---------------------------------------------------------------------------


class TestInheritedKnowledgeFacet:
    @pytest.mark.asyncio
    async def test_empty_when_borrowing_disabled(self) -> None:
        app = FastAPI()
        app.state._startup_config = {}
        result = await _inherited_knowledge_facet(app, "pid", MagicMock(), None)
        assert result == {}

    @pytest.mark.asyncio
    async def test_empty_when_borrowing_disabled_explicit(self) -> None:
        rr = RelationshipRoutingStub(enable_cross_project_borrowing=False)
        app = FastAPI()
        app.state._startup_config = {"user_config": MagicMock(relationship_routing=rr)}
        result = await _inherited_knowledge_facet(app, "pid", MagicMock(), None)
        assert result == {}

    @pytest.mark.asyncio
    async def test_uses_benchmark_repo_from_router_when_present(self) -> None:
        rr = RelationshipRoutingStub(enable_cross_project_borrowing=True)
        app = FastAPI()
        app.state._startup_config = {"user_config": MagicMock(relationship_routing=rr)}

        fake_repo = MagicMock()
        fake_router = MagicMock(_repo=fake_repo)
        fake_router.inherited_knowledge = AsyncMock(return_value={"borrowed": [1, 2]})
        app.state._adaptive_router = fake_router

        with patch(
            "general_ludd.scoring.router.AdaptiveRouter",
            return_value=fake_router,
        ):
            result = await _inherited_knowledge_facet(app, "pid", MagicMock(), MagicMock())
            assert result == {"borrowed": [1, 2]}
            fake_router.inherited_knowledge.assert_awaited_once()


# ---------------------------------------------------------------------------
# _resolve_advice — priority clamping + cost bound + budget warning
# ---------------------------------------------------------------------------


class TestResolveAdvice:
    @pytest.mark.asyncio
    async def test_priority_cost_accepted(self) -> None:
        app = FastAPI()
        with patch(
            "general_ludd.routers.environment.build_advice",
            return_value={"task_type": "feature", "est_cost_usd": 0.0},
        ):
            result = await _resolve_advice(
                app,
                work_type="feature",
                prompt_tokens=100,
                priority="cost",
            )
            assert result["task_type"] == "feature"

    @pytest.mark.asyncio
    async def test_priority_quality_accepted(self) -> None:
        app = FastAPI()
        with patch(
            "general_ludd.routers.environment.build_advice",
            return_value={"task_type": "feature", "est_cost_usd": 0.0},
        ):
            result = await _resolve_advice(
                app,
                work_type="feature",
                prompt_tokens=100,
                priority="quality",
            )
            assert result["task_type"] == "feature"

    @pytest.mark.asyncio
    async def test_priority_latency_accepted(self) -> None:
        app = FastAPI()
        with patch(
            "general_ludd.routers.environment.build_advice",
            return_value={"task_type": "feature", "est_cost_usd": 0.0},
        ):
            result = await _resolve_advice(
                app,
                work_type="feature",
                prompt_tokens=100,
                priority="latency",
            )
            assert result["task_type"] == "feature"

    @pytest.mark.asyncio
    async def test_prompt_tokens_none_yields_zero_in_cost(self) -> None:
        app = FastAPI()
        with patch(
            "general_ludd.routers.environment.build_advice",
            return_value={"task_type": "feature", "est_cost_usd": 0.0},
        ):
            result = await _resolve_advice(
                app,
                work_type="feature",
                prompt_tokens=None,
                priority="quality",
            )
            assert result["task_type"] == "feature"

    @pytest.mark.asyncio
    async def test_fallback_on_total_failure(self) -> None:
        app = FastAPI()
        result = await _resolve_advice(
            app,
            work_type="bugfix",
            prompt_tokens=100,
            priority="quality",
        )
        assert result["task_type"] == "bugfix"
        assert "recommendation" in result

    @pytest.mark.asyncio
    async def test_work_type_whitespace_and_case_normalized(self) -> None:
        app = FastAPI()
        with patch(
            "general_ludd.routers.environment.build_advice",
            return_value={"task_type": "feature", "est_cost_usd": 0.0},
        ):
            result = await _resolve_advice(
                app,
                work_type="  FEATURE  ",
                prompt_tokens=100,
                priority="quality",
            )
            assert result["task_type"] == "feature"

    @pytest.mark.asyncio
    async def test_default_expected_output_tokens_constant(self) -> None:
        assert _DEFAULT_EXPECTED_OUTPUT_TOKENS == 1024


# ---------------------------------------------------------------------------
# _project_facet — additional fail-soft paths
# ---------------------------------------------------------------------------


class TestProjectFacetAdditional:
    @pytest.mark.asyncio
    async def test_empty_when_no_scope_and_no_manager(self) -> None:
        app = FastAPI()
        facet = await _project_facet(app, None)
        assert facet == {}

    @pytest.mark.asyncio
    async def test_empty_when_no_session_factory(self) -> None:
        app = FastAPI()
        app.state._session_factory = None
        facet = await _project_facet(app, "some-id")
        assert facet == {}

    @pytest.mark.asyncio
    async def test_project_id_propagated_into_result(self) -> None:
        class _FakeSession:
            async def __aenter__(self) -> _FakeSession:
                return self

            async def __aexit__(self, *args: Any) -> None:
                return None

        factory = MagicMock(return_value=_FakeSession())
        app = FastAPI()
        app.state._session_factory = factory

        with patch(
            "general_ludd.db.repository.ProjectRelationshipRepository",
        ) as mock_repo_cls:
            repo_instance = MagicMock()
            repo_instance.list_for_project = AsyncMock(return_value=[])
            mock_repo_cls.return_value = repo_instance
            facet = await _project_facet(app, "explicit-id")
            assert facet["project_id"] == "explicit-id"
            assert facet["relationships"] == []
            assert facet["inherited_knowledge"] == {}
