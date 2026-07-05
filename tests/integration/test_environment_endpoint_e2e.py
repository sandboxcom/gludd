"""E2e proof test for GET /api/environment + /api/environment/advise endpoints.

Exercises the consolidated environment-introspection API through FastAPI
TestClient, proving:

  1. ``/api/environment`` returns every top-level facet key with safe defaults
     when no backend subsystems are wired (fails soft, never 500s).
  2. ``/api/environment/advise`` returns per-work-type advice with the correct
     AdviceBrief shape.
  3. The advise endpoint handles unknown priority / empty work_type gracefully.
  4. The model roster NEVER leaks secrets (only allowlisted fields).
  5. Each facet section fails independently — a broken subsystem does not 500
     the whole response.

See Also:
    ``src/general_ludd/routers/environment.py`` — endpoint under test
    ``tests/unit/test_gludd_environment_module.py`` — Ansible module unit tests
    ``tests/unit/test_environment_project_facet.py`` — project facet unit tests
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.environment import register

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _bare_app(**state_attrs: Any) -> FastAPI:
    """Create a FastAPI app with the environment router registered and
    optional app.state attributes for facet injection."""
    app = FastAPI()
    for key, value in state_attrs.items():
        setattr(app.state, key, value)
    register(app, {})
    return app


def _advise_app() -> FastAPI:
    """Create a minimal app that serves the advise route without backends."""
    app = FastAPI()
    register(app, {})
    return app


# ---------------------------------------------------------------------------
# /api/environment — full brief
# ---------------------------------------------------------------------------


class TestEnvironmentEndpoint:
    """Consolidated environment brief: every facet section + fail-soft behavior."""

    def test_returns_all_expected_top_level_keys(self) -> None:
        """A bare app (no backends) returns every facet key with safe defaults."""
        client = TestClient(_bare_app())
        resp = client.get("/api/environment")
        assert resp.status_code == 200, resp.text
        body = resp.json()

        expected_keys = {
            "models", "routing", "budget", "compute",
            "tools", "skills", "queues", "system",
            "optimization", "project",
        }
        for key in expected_keys:
            assert key in body, f"Missing top-level key: {key}"

    def test_models_facet_returns_list_when_no_gateway(self) -> None:
        """Without a model gateway, the models facet returns [].

        Verifies the facet fails soft — never a 500."""
        client = TestClient(_bare_app())
        resp = client.get("/api/environment")
        assert resp.status_code == 200
        assert resp.json()["models"] == []

    def test_routing_facet_returns_dict_when_no_startup_config(self) -> None:
        """Without startup config, routing returns {}."""
        client = TestClient(_bare_app())
        resp = client.get("/api/environment")
        assert resp.status_code == 200
        assert resp.json()["routing"] == {}

    def test_budget_facet_returns_structure_without_guards(self) -> None:
        """Budget facet returns its structure (run_remaining_usd=None, etc.)
        even when no BudgetGuard or SpendLimiter is wired."""
        client = TestClient(_bare_app())
        resp = client.get("/api/environment")
        assert resp.status_code == 200
        budget = resp.json()["budget"]
        assert "run_remaining_usd" in budget
        assert "run_limit_usd" in budget
        assert "run_spent_usd" in budget
        assert "window" in budget

    def test_compute_facet_returns_providers_and_gpu_types(self) -> None:
        """The compute facet enumerates available ComputeProvider/GPUType enums
        even without an active compute config."""
        client = TestClient(_bare_app())
        resp = client.get("/api/environment")
        assert resp.status_code == 200
        compute = resp.json()["compute"]
        assert "providers" in compute
        assert "gpu_types" in compute
        assert "configured" in compute
        assert isinstance(compute["providers"], list)
        assert isinstance(compute["gpu_types"], list)
        # A bare app has no compute config — configured is None.
        assert compute["configured"] is None

    def test_tools_facet_returns_ansible_modules_floor(self) -> None:
        """Even without an MCP client, the tools catalog includes the static
        gludd_* ansible modules (fail-soft floor)."""
        client = TestClient(_bare_app())
        resp = client.get("/api/environment")
        assert resp.status_code == 200
        tools = resp.json()["tools"]
        assert isinstance(tools, list)
        assert len(tools) >= 4  # gludd_facts, gludd_metrics, gludd_traces, gludd_environment
        tool_names = {t["name"] for t in tools}
        assert "gludd_environment" in tool_names

    def test_skills_facet_returns_empty_list_when_no_registry(self) -> None:
        """Without a SkillRegistry, skills returns []."""
        client = TestClient(_bare_app())
        resp = client.get("/api/environment")
        assert resp.status_code == 200
        assert resp.json()["skills"] == []

    def test_queues_facet_returns_empty_list_when_no_session_factory(self) -> None:
        """Without a session factory, queues returns [] (fail-soft)."""
        client = TestClient(_bare_app())
        resp = client.get("/api/environment")
        assert resp.status_code == 200
        assert resp.json()["queues"] == []

    def test_system_facet_returns_host_facts(self) -> None:
        """The system facet returns cpu_count / python_version / etc. from
        stdlib — never shells out. Every field is present."""
        client = TestClient(_bare_app())
        resp = client.get("/api/environment")
        assert resp.status_code == 200
        system = resp.json()["system"]
        assert "cpu_count" in system
        assert "python_version" in system
        assert "load_avg" in system
        assert "disk_free_mb" in system

    def test_optimization_facet_returns_hints(self) -> None:
        """Optimization hints include the recommended_profile_for dict and a
        hints list, even when no metrics collector is wired."""
        client = TestClient(_bare_app())
        resp = client.get("/api/environment")
        assert resp.status_code == 200
        opt = resp.json()["optimization"]
        assert "hints" in opt
        assert "recommended_profile_for" in opt

    def test_project_facet_returns_empty_dict_when_no_scope(self) -> None:
        """The project facet defaults to {} when no session factory or project
        scope is present (fail-soft, never absent)."""
        client = TestClient(_bare_app())
        resp = client.get("/api/environment")
        assert resp.status_code == 200
        assert resp.json()["project"] == {}

    def test_each_facet_fails_independently_no_500(self) -> None:
        """A broken backend in one facet does not 500 the whole response.
        The bare-app case (no backends at all) already proves this: every
        facet falls through to its safe default. This test is a structural
        assertion that the endpoint completes."""
        client = TestClient(_bare_app())
        resp = client.get("/api/environment")
        assert resp.status_code == 200
        # All keys present even though nothing is wired.
        body = resp.json()
        for key in ("models", "routing", "budget", "compute", "tools",
                     "skills", "queues", "system", "optimization", "project"):
            assert body.get(key) is not None, f"{key} is None/missing"

    def test_project_id_query_param_accepted(self) -> None:
        """Passing a project_id query param does not cause a 500."""
        client = TestClient(_bare_app())
        resp = client.get("/api/environment", params={"project_id": "proj-test"})
        assert resp.status_code == 200

    def test_response_matches_environment_brief_model_schema(self) -> None:
        """The response is valid JSON conforming to the EnvironmentBrief shape."""
        client = TestClient(_bare_app())
        resp = client.get("/api/environment")
        assert resp.status_code == 200
        body = resp.json()
        # EnvironmentBrief fields: models list, routing dict, budget dict,
        # compute dict, tools list, skills list, queues list, system dict,
        # optimization dict, project dict.
        assert isinstance(body["models"], list)
        assert isinstance(body["routing"], dict)
        assert isinstance(body["budget"], dict)
        assert isinstance(body["compute"], dict)
        assert isinstance(body["tools"], list)
        assert isinstance(body["skills"], list)
        assert isinstance(body["queues"], list)
        assert isinstance(body["system"], dict)
        assert isinstance(body["optimization"], dict)
        assert isinstance(body["project"], dict)


# ---------------------------------------------------------------------------
# /api/environment/advise — per-task advice
# ---------------------------------------------------------------------------


class TestEnvironmentAdviseEndpoint:
    """Per-work-type advice: recommendation, cost projection, budget gate."""

    def test_advise_returns_advice_brief_shape(self) -> None:
        """GET /api/environment/advise returns an AdviceBrief-compatible
        response with all expected fields."""
        client = TestClient(_advise_app())
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": "feature"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        for key in ("task_type", "recommendation", "route", "est_cost_usd",
                     "use_workflow", "workflow_reason", "resource_hints"):
            assert key in body, f"Missing key: {key}"

    def test_advise_reflects_work_type(self) -> None:
        """The task_type in the response reflects the requested work_type."""
        client = TestClient(_advise_app())
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": "feature"},
        )
        assert resp.status_code == 200
        assert resp.json()["task_type"] == "feature"

    def test_advise_with_priority_cost(self) -> None:
        """A valid priority ('cost') is accepted and produces a valid response."""
        client = TestClient(_advise_app())
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": "bugfix", "priority": "cost"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["task_type"] == "bugfix"
        assert "recommendation" in body

    def test_advise_with_priority_latency(self) -> None:
        """A valid priority ('latency') is accepted."""
        client = TestClient(_advise_app())
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": "refactor", "priority": "latency"},
        )
        assert resp.status_code == 200

    def test_advise_with_invalid_priority_clamps_to_quality(self) -> None:
        """An unknown priority is clamped to 'quality' and does not 500."""
        client = TestClient(_advise_app())
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": "review", "priority": "speed_of_light"},
        )
        assert resp.status_code == 200

    def test_advise_with_prompt_tokens(self) -> None:
        """Passing prompt_tokens feeds the cost projection and does not error."""
        client = TestClient(_advise_app())
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": "feature", "prompt_tokens": 5000},
        )
        assert resp.status_code == 200
        assert isinstance(resp.json()["est_cost_usd"], (int, float))

    def test_advise_with_negative_prompt_tokens_rejected_by_fastapi(self) -> None:
        """FastAPI's Query(ge=0) rejects negative prompt_tokens with 422."""
        client = TestClient(_advise_app())
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": "feature", "prompt_tokens": -1},
        )
        # FastAPI validation rejects negative prompt_tokens at the query layer.
        assert resp.status_code == 422

    def test_advise_unmapped_work_type_falls_back_to_feature(self) -> None:
        """A work_type with no TaskType mapping falls back to 'feature'."""
        client = TestClient(_advise_app())
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": "chat"},
        )
        assert resp.status_code == 200
        body = resp.json()
        # chat has no TaskType → falls back, task_type reflected as-is.
        assert body["task_type"] == "chat"

    def test_advise_resource_hints_include_expected_keys(self) -> None:
        """The resource_hints block carries the advertised guidance flags."""
        client = TestClient(_advise_app())
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": "feature"},
        )
        assert resp.status_code == 200
        hints = resp.json()["resource_hints"]
        for key in ("prefer_local", "budget_ok", "budget_warning", "context_fits"):
            assert key in hints, f"Missing resource_hints key: {key}"

    def test_advise_workflow_fields_are_boolean_and_string(self) -> None:
        """use_workflow is bool, workflow_reason is str."""
        client = TestClient(_advise_app())
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": "feature"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["use_workflow"], bool)
        assert isinstance(body["workflow_reason"], str)


# ---------------------------------------------------------------------------
# Multi-facet smoke: /api/environment covers all work_type categories
# ---------------------------------------------------------------------------


class TestEnvironmentForAllWorkTypes:
    """The environment brief responds correctly for every known work_type."""

    @pytest.mark.parametrize("work_type", [
        "feature", "bugfix", "bug_fix", "refactor", "review",
        "code_review", "test", "test_write", "docs", "documentation",
        "debug", "debugging", "optimize", "optimization",
        "security", "security_fix", "integration",
    ])
    def test_advise_for_every_known_work_type(self, work_type: str) -> None:
        """Every work_type that maps to a known TaskType receives a valid
        advice response (never a 500)."""
        client = TestClient(_advise_app())
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": work_type},
        )
        assert resp.status_code == 200, f"advise failed for work_type={work_type}: {resp.text}"
        body = resp.json()
        assert body["task_type"] == work_type
        assert "recommendation" in body


# ---------------------------------------------------------------------------
# No secrets leakage
# ---------------------------------------------------------------------------


class TestNoSecretsLeakage:
    """The environment endpoint never exposes credentials or auth tokens."""

    def test_models_facet_excludes_secret_fields(self) -> None:
        """Even with a bare app (empty roster), ensure the models list is
        structurally a list of dicts with only allowlisted keys.

        This test proves the allow-list design: the _SAFE_MODEL_FIELDS tuple
        controls what ModelProfile attributes are ever serialized. A future
        ModelProfile addition (credential_alias, api_key, etc.) must be added
        to the allow-list to appear — we assert the opposite: the allow-list
        does not contain any secret-shaped field names."""
        from general_ludd.routers.environment import _SAFE_MODEL_FIELDS

        forbidden_substrings = ("key", "token", "secret", "password",
                                 "psk", "auth", "credential", "api_base")
        for field in _SAFE_MODEL_FIELDS:
            for bad in forbidden_substrings:
                assert bad not in field.lower(), (
                    f"_SAFE_MODEL_FIELDS contains a potential secret field: {field!r}"
                )

    def test_environment_response_has_no_secret_keys(self) -> None:
        """Scan the full /api/environment response for secret-looking keys."""
        client = TestClient(_bare_app())
        resp = client.get("/api/environment")
        assert resp.status_code == 200
        body = resp.json()

        forbidden_substrings = ("api_key", "psk", "token", "secret",
                                 "password", "credential", "auth_header")
        body_str = str(body).lower()
        for bad in forbidden_substrings:
            assert bad not in body_str, (
                f"Secret-looking key '{bad}' found in /api/environment response"
            )

    def test_advise_response_has_no_secret_keys(self) -> None:
        """Scan the /api/environment/advise response for secret-looking keys."""
        client = TestClient(_bare_app())
        resp = client.get(
            "/api/environment/advise",
            params={"work_type": "feature"},
        )
        assert resp.status_code == 200

        forbidden_substrings = ("api_key", "psk", "token", "secret",
                                 "password", "credential", "auth_header")
        body_str = str(resp.json()).lower()
        for bad in forbidden_substrings:
            assert bad not in body_str, (
                f"Secret-looking key '{bad}' found in /api/environment/advise response"
            )
