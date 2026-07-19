"""Unit tests for scenario_generator.py — PATTERN_KEYWORDS, ScenarioGenerator, and step handlers."""

from __future__ import annotations

from general_ludd.agents.test_generation.code_path_analyzer import ClassSymbol, ModuleSymbols, Symbol
from general_ludd.agents.test_generation.knowledge.test_scenarios import E2E_SCENARIOS, E2EScenario
from general_ludd.agents.test_generation.scenario_generator import (
    PATTERN_KEYWORDS,
    ScenarioGenerator,
    ScenarioStep,
    _auth_steps,
    _concurrent_steps,
    _crud_steps,
    _daemon_steps,
    _default_steps,
    _timeout_steps,
)


def _make_symbol(name: str, *, is_public: bool = True) -> Symbol:
    return Symbol(name=name, line_start=1, line_end=1, is_public=is_public)


def _make_class_symbol(name: str, *, is_public: bool = True) -> ClassSymbol:
    return ClassSymbol(name=name, line_start=1, line_end=1, is_public=is_public, methods=[])


def _make_module(name: str = "test_module", functions=None, classes=None) -> ModuleSymbols:
    return ModuleSymbols(name=name, functions=functions or [], classes=classes or [])


class TestPATTERN_KEYWORDS:
    def test_crud_keywords_includes_create_delete(self):
        assert "create" in PATTERN_KEYWORDS["crud_lifecycle"]
        assert "delete" in PATTERN_KEYWORDS["crud_lifecycle"]

    def test_auth_keywords_includes_login_token(self):
        assert "login" in PATTERN_KEYWORDS["auth_flow"]
        assert "token" in PATTERN_KEYWORDS["auth_flow"]

    def test_timeout_keywords_includes_retry_backoff(self):
        assert "retry" in PATTERN_KEYWORDS["timeout_handling"]
        assert "backoff" in PATTERN_KEYWORDS["timeout_handling"]

    def test_concurrent_keywords_includes_lock_atomic(self):
        assert "lock" in PATTERN_KEYWORDS["concurrent_edits"]
        assert "atomic" in PATTERN_KEYWORDS["concurrent_edits"]

    def test_daemon_keywords_includes_init_shutdown(self):
        assert "init" in PATTERN_KEYWORDS["daemon_restart"]
        assert "shutdown" in PATTERN_KEYWORDS["daemon_restart"]


class TestScenarioGenerator:
    def test_crud_keywords_map_to_crud_lifecycle_scenario(self):
        gen = ScenarioGenerator()
        mod = _make_module(functions=[_make_symbol("create_user"), _make_symbol("delete_record")])
        results = gen.generate(mod)
        assert len(results) == 1
        assert results[0].name == "crud_lifecycle"

    def test_auth_keywords_map_to_auth_flow_scenario(self):
        gen = ScenarioGenerator()
        mod = _make_module(functions=[_make_symbol("login_handler"), _make_symbol("token_refresh")])
        results = gen.generate(mod)
        assert len(results) == 1
        assert results[0].name == "auth_flow"

    def test_timeout_keywords_map_to_timeout_handling_scenario(self):
        gen = ScenarioGenerator()
        mod = _make_module(functions=[_make_symbol("retry_handler"), _make_symbol("backoff_manager")])
        results = gen.generate(mod)
        assert len(results) == 1
        assert results[0].name == "timeout_handling"

    def test_concurrent_keywords_map_to_concurrent_edits_scenario(self):
        gen = ScenarioGenerator()
        mod = _make_module(functions=[_make_symbol("lock_acquire"), _make_symbol("atomic_write")])
        results = gen.generate(mod)
        assert len(results) == 1
        assert results[0].name == "concurrent_edits"

    def test_daemon_keywords_map_to_daemon_restart_scenario(self):
        gen = ScenarioGenerator()
        mod = _make_module(functions=[_make_symbol("init_daemon"), _make_symbol("shutdown_handler")])
        results = gen.generate(mod)
        assert len(results) == 1
        assert results[0].name == "daemon_restart"

    def test_mixed_keywords_produce_multiple_scenarios(self):
        gen = ScenarioGenerator()
        mod = _make_module(functions=[
            _make_symbol("create_resource"),
            _make_symbol("login_handler"),
            _make_symbol("retry_connection"),
        ])
        results = gen.generate(mod)
        names = {r.name for r in results}
        assert names == {"crud_lifecycle", "auth_flow", "timeout_handling"}

    def test_no_matching_keywords_produces_no_scenarios(self):
        gen = ScenarioGenerator()
        mod = _make_module(functions=[_make_symbol("unrelated_func"), _make_symbol("helper_thing")])
        results = gen.generate(mod)
        assert results == []

    def test_empty_module_produces_no_scenarios(self):
        gen = ScenarioGenerator()
        mod = _make_module(functions=[], classes=[])
        results = gen.generate(mod)
        assert results == []

    def test_private_functions_are_excluded(self):
        gen = ScenarioGenerator()
        mod = _make_module(functions=[
            _make_symbol("create_record", is_public=True),
            _make_symbol("_create_internal", is_public=False),
        ])
        results = gen.generate(mod)
        assert len(results) == 1
        assert results[0].name == "crud_lifecycle"
        assert results[0].coverage_targets == ["create_record"]

    def test_public_class_matches_keywords(self):
        gen = ScenarioGenerator()
        mod = _make_module(functions=[], classes=[_make_class_symbol("create_handler")])
        results = gen.generate(mod)
        assert len(results) == 1
        assert results[0].name == "crud_lifecycle"
        assert results[0].coverage_targets == ["create_handler"]

    def test_private_class_is_excluded(self):
        gen = ScenarioGenerator()
        mod = _make_module(functions=[], classes=[
            _make_class_symbol("_internal_create", is_public=False),
        ])
        results = gen.generate(mod)
        assert results == []

    def test_custom_scenario_catalog_accepted(self):
        custom = [E2EScenario(name="crud_lifecycle", description="custom crud", tags=["custom"])]
        gen = ScenarioGenerator(scenario_catalog=custom)
        mod = _make_module(functions=[_make_symbol("create_user")])
        results = gen.generate(mod)
        assert len(results) == 1
        assert results[0].description == "custom crud"

    def test_generated_scenarios_have_all_required_fields(self):
        gen = ScenarioGenerator()
        mod = _make_module(functions=[_make_symbol("create_resource")])
        results = gen.generate(mod)
        assert len(results) == 1
        scenario = results[0]
        assert hasattr(scenario, "name")
        assert hasattr(scenario, "description")
        assert hasattr(scenario, "steps")
        assert hasattr(scenario, "coverage_targets")
        assert len(scenario.steps) > 0
        step = scenario.steps[0]
        assert isinstance(step, ScenarioStep)
        assert hasattr(step, "action")
        assert hasattr(step, "target")
        assert hasattr(step, "expected_result")

    def test_default_catalog_is_e2e_scenarios(self):
        gen = ScenarioGenerator()
        assert gen._catalog is E2E_SCENARIOS


class TestDirectStepHandlers:
    def test_crud_steps_returns_post_delete_setup_actions(self):
        steps = _crud_steps([_make_symbol("create_user")])
        actions = [s.action for s in steps]
        assert "POST" in actions
        assert "DELETE" in actions
        assert "Setup" in actions

    def test_crud_steps_with_empty_symbols_uses_default_target(self):
        steps = _crud_steps([])
        assert steps[1].target == "/api/resource"

    def test_auth_steps_has_login_and_protected_targets(self):
        steps = _auth_steps([_make_symbol("login_handler")])
        targets = [s.target for s in steps]
        assert "/api/auth/login" in targets
        assert "/api/protected/resource" in targets

    def test_timeout_steps_returns_setup_and_teardown_actions(self):
        steps = _timeout_steps([_make_symbol("retry_handler")])
        actions = [s.action for s in steps]
        assert "Setup" in actions
        assert "Teardown" in actions

    def test_timeout_steps_with_empty_symbols_uses_default_slow_target(self):
        steps = _timeout_steps([])
        assert steps[1].target == "/api/endpoint/slow"

    def test_concurrent_steps_returns_simulate_action(self):
        steps = _concurrent_steps([_make_symbol("lock_acquire")])
        actions = [s.action for s in steps]
        assert "Simulate" in actions

    def test_daemon_steps_returns_stop_and_start_actions(self):
        steps = _daemon_steps([_make_symbol("init_daemon")])
        actions = [s.action for s in steps]
        assert "Stop" in actions
        assert "Start" in actions

    def test_default_steps_returns_setup_and_teardown_actions(self):
        steps = _default_steps([_make_symbol("unknown_func")])
        actions = [s.action for s in steps]
        assert "Setup" in actions
        assert "Teardown" in actions

    def test_default_steps_with_empty_symbols_uses_module_target(self):
        steps = _default_steps([])
        assert steps[1].target == "module"
