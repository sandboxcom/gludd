"""Tests for the deprecated self-update router (self_update/router.py).

Covers :class:`UpdateRequestRouter` with injected ``path_exists`` so all
routing is exercised without the real filesystem. Also tests the exported
dataclasses (:class:`UpdateRequest`, :class:`UpdateTarget`, :class:`UpdatePlan`)
and top-level helpers.

Run: make test-iso TESTFILE='tests/unit/test_self_update_router_class.py'
"""

from __future__ import annotations

from general_ludd.self_update.router import (
    DEFAULT_SUBSYSTEM_MAP,
    UpdatePlan,
    UpdateRequest,
    UpdateRequestRouter,
    UpdateTarget,
)


class TestUpdateRequestRouterBasics:
    def test_constructor_defaults_to_default_subsystem_map(self) -> None:
        router = UpdateRequestRouter()
        assert router._map is DEFAULT_SUBSYSTEM_MAP

    def test_constructor_accepts_custom_map(self) -> None:
        custom: dict[str, object] = {"test": {"kind": "config", "keywords": ["foo"], "paths": ["/x"]}}
        router = UpdateRequestRouter(subsystem_map=custom)
        assert router._map is custom

    def test_constructor_accepts_custom_path_exists(self) -> None:
        calls: list[str] = []

        def fake_exists(p: str) -> bool:
            calls.append(p)
            return True

        router = UpdateRequestRouter(path_exists=fake_exists)
        router.route("update gludd: increase my budget")
        assert len(calls) > 0


class TestUpdateRequestRouterRouting:
    def test_budget_keyword_routes_to_budget_subsystem(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("increase the spend window to 2h")
        assert plan.target.subsystem == "budget"

    def test_model_keyword_routes_to_model_subsystem(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("change the model profile for openai")
        assert plan.target.subsystem == "model"

    def test_lint_keyword_routes_to_lint_subsystem(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("tighten the lint ratchet")
        assert plan.target.subsystem == "lint"

    def test_secret_keyword_routes_to_secret_subsystem(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("rotate the vault credential")
        assert plan.target.subsystem == "secret"

    def test_connector_keyword_routes_to_connector_subsystem(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("add a new observability connector")
        assert plan.target.subsystem == "connector"

    def test_scheduler_keyword_routes_to_scheduler_subsystem(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("increase dispatch concurrency")
        assert plan.target.subsystem == "scheduler"

    def test_role_keyword_routes_to_role_subsystem(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("update the project_init role")
        assert plan.target.subsystem == "role"

    def test_no_match_failsafe(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("blargle florb nope")
        assert plan.target.subsystem == "unknown"
        assert plan.target.kind == "config"
        assert plan.target.paths == []
        assert "human routing" in plan.rationale


class TestUpdateRequestRouterPrefixStripping:
    def test_strips_update_gludd_colon_prefix(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("update gludd: increase the spend budget")
        assert plan.target.subsystem == "budget"

    def test_strips_update_gludd_dash_prefix(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("update gludd - increase the spend budget")
        assert plan.target.subsystem == "budget"

    def test_strips_update_gludd_no_separator_prefix(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("update gludd increase the spend budget")
        assert plan.target.subsystem == "budget"

    def test_no_prefix_present(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("increase the spend budget")
        assert plan.target.subsystem == "budget"


class TestUpdateRequestRouterKindDecision:
    def test_config_subsystem_defaults_to_config_kind(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("increase the spend budget")
        assert plan.target.kind == "config"

    def test_role_subsystem_defaults_to_role_kind(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("update the project_init role")
        assert plan.target.kind == "role"

    def test_scheduler_subsystem_defaults_to_code_kind(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("change dispatch concurrency")
        assert plan.target.kind == "code"

    def test_behaviour_marker_escalates_to_code(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("rewrite how the budget limiter works")
        assert plan.target.kind == "code"

    def test_behaviour_marker_no_code_paths_stays_config(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("refactor the lint ratchet")
        assert plan.target.kind == "config"


class TestUpdateRequestRouterPlanFields:
    def test_config_plan_has_low_risk_and_high_priority(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("increase the spend budget")
        assert plan.target.kind == "config"
        assert plan.risk == "low"
        assert plan.priority > 5

    def test_code_plan_has_high_risk_and_low_priority(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("increase dispatch concurrency")
        assert plan.target.kind == "code"
        assert plan.risk == "high"
        assert plan.priority < 6

    def test_role_plan_has_medium_risk(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("update the project_init role")
        assert plan.target.kind == "role"
        assert plan.risk == "medium"

    def test_change_summary_is_original_text(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("  increase budget  ")
        assert plan.change_summary == "increase budget"

    def test_capability_is_assigned(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("increase the spend budget")
        assert plan.capability_required == "config_write"

    def test_code_plan_assigned_code_self_modify_capability(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("increase dispatch concurrency")
        assert plan.capability_required == "code_self_modify"


class TestUpdateRequestRouterPathResolution:
    def test_existing_paths_are_included(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: p == "config/ratchet.yml")
        plan = router.route("increase the spend budget")
        assert len(plan.target.paths) > 0

    def test_nonexistent_paths_filtered_out(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: False)
        plan = router.route("increase the spend budget")
        assert plan.target.paths == []
        assert plan.target.subsystem == "unknown"

    def test_role_path_resolves_to_role_directory(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("update the project_init role")
        assert any("project_init" in p for p in plan.target.paths)

    def test_code_plan_uses_code_paths(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("rewrite how the budget limiter works")
        assert plan.target.kind == "code"
        assert any("spend_limiter" in p for p in plan.target.paths)


class TestUpdateRequestRouterLongestMatch:
    def test_longest_keyword_match_wins(self) -> None:
        def fake_exists(p: str) -> bool:
            return True

        router = UpdateRequestRouter(path_exists=fake_exists)
        plan = router.route("add a budget for model profiles")
        assert plan.target.subsystem in {"budget", "model"}
        assert plan.target.subsystem != "unknown"


class TestUpdateRequestRouterFailsafe:
    def test_failsafe_target_is_config_with_no_paths(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: False)
        plan = router.route("increase the spend budget")
        assert plan.target.subsystem == "unknown"
        assert plan.target.kind == "config"
        assert plan.target.paths == []

    def test_failsafe_is_high_risk(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("blargle florb nope")
        assert plan.target.subsystem == "unknown"
        assert plan.risk == "high"

    def test_failsafe_rationale_mentions_human_routing(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("blargle florb nope")
        assert "human routing" in plan.rationale

    def test_failsafe_rationale_mentions_no_code_write_guessed(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("blargle florb nope")
        assert "no code write" in plan.rationale.lower()


class TestUpdateRequestRouterRoleNameExtraction:
    def test_the_name_role_pattern(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("fix the project_init role")
        assert plan.target.subsystem == "role"
        assert any("project_init" in p for p in plan.target.paths)

    def test_role_name_pattern(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("role my_custom_role")
        assert plan.target.subsystem == "role"
        assert any("my_custom_role" in p for p in plan.target.paths)

    def test_role_without_specific_name_falls_back_to_config_paths(self) -> None:
        router = UpdateRequestRouter(path_exists=lambda p: True)
        plan = router.route("update the role")
        assert plan.target.subsystem == "role"
        assert "collections" in plan.target.paths[0]


class TestDataclassInstantiation:
    def test_update_request_holds_text(self) -> None:
        req = UpdateRequest(text="hello")
        assert req.text == "hello"

    def test_update_target_defaults(self) -> None:
        target = UpdateTarget(kind="config")
        assert target.kind == "config"
        assert target.paths == []
        assert target.subsystem == ""

    def test_update_plan_holds_all_fields(self) -> None:
        target = UpdateTarget(kind="config", paths=["/x"], subsystem="budget")
        plan = UpdatePlan(
            target=target,
            change_summary="increase spend",
            capability_required="config_write",
            priority=8,
            risk="low",
            rationale="test",
        )
        assert plan.change_summary == "increase spend"
        assert plan.capability_required == "config_write"
        assert plan.priority == 8
        assert plan.risk == "low"
        assert plan.rationale == "test"


class TestSubsystemMapStructure:
    def test_each_entry_has_kind_keywords_paths(self) -> None:
        for name, spec in DEFAULT_SUBSYSTEM_MAP.items():
            assert "kind" in spec, f"{name} missing kind"
            assert "keywords" in spec, f"{name} missing keywords"
            assert "paths" in spec, f"{name} missing paths"

    def test_keywords_are_nonempty(self) -> None:
        for name, spec in DEFAULT_SUBSYSTEM_MAP.items():
            kws = spec.get("keywords", [])
            assert len(kws) > 0, f"{name} has empty keywords"

    def test_paths_are_nonempty(self) -> None:
        for name, spec in DEFAULT_SUBSYSTEM_MAP.items():
            paths = spec.get("paths", [])
            assert len(paths) > 0, f"{name} has empty paths"
