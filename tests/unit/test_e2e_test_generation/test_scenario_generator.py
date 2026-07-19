"""Unit tests for ScenarioGenerator."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from general_ludd.agents.test_generation.code_path_analyzer import (
    CodePathAnalyzer,
    ModuleSymbols,
    Symbol,
)
from general_ludd.agents.test_generation.knowledge.test_scenarios import E2EScenario
from general_ludd.agents.test_generation.scenario_generator import (
    GeneratedScenario,
    ScenarioGenerator,
    ScenarioStep,
)


@pytest.fixture
def generator() -> ScenarioGenerator:
    return ScenarioGenerator()


@pytest.fixture
def analyzer() -> CodePathAnalyzer:
    return CodePathAnalyzer()


def write_temp_module(content: str) -> str:
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(content)
        return str(f.name)


class TestGenerateForCrudModule:
    def test_matches_create_update_delete(self, generator: ScenarioGenerator) -> None:
        syms = ModuleSymbols(
            name="crud_module.py",
            functions=[
                Symbol(name="create_user", line_start=1, line_end=5, is_public=True),
                Symbol(name="update_user", line_start=7, line_end=12, is_public=True),
                Symbol(name="delete_user", line_start=14, line_end=18, is_public=True),
            ],
            classes=[],
        )
        result = generator.generate(syms)
        assert len(result) == 1
        scenario = result[0]
        assert scenario.name == "crud_lifecycle"
        assert len(scenario.steps) >= 5
        assert sorted(scenario.coverage_targets) == sorted(["create_user", "update_user", "delete_user"])

    def test_steps_cover_full_crud_cycle(self, generator: ScenarioGenerator) -> None:
        syms = ModuleSymbols(
            name="crud.py",
            functions=[
                Symbol(name="create_item", line_start=1, line_end=3, is_public=True),
            ],
            classes=[],
        )
        result = generator.generate(syms)
        assert len(result) == 1
        steps = result[0].steps
        actions = [s.action for s in steps]
        assert "POST" in actions
        assert "GET" in actions
        assert "PUT" in actions
        assert "DELETE" in actions

    def test_skips_private_functions(self, generator: ScenarioGenerator) -> None:
        syms = ModuleSymbols(
            name="mod.py",
            functions=[
                Symbol(name="_create_internal", line_start=1, line_end=5, is_public=False),
                Symbol(name="delete_record", line_start=7, line_end=12, is_public=True),
            ],
            classes=[],
        )
        result = generator.generate(syms)
        assert len(result) == 1
        assert result[0].coverage_targets == ["delete_record"]


class TestGenerateForAuthModule:
    def test_matches_auth_login_token(self, generator: ScenarioGenerator) -> None:
        syms = ModuleSymbols(
            name="auth_module.py",
            functions=[
                Symbol(name="login_user", line_start=1, line_end=5, is_public=True),
                Symbol(name="refresh_token", line_start=7, line_end=11, is_public=True),
                Symbol(name="validate_session", line_start=13, line_end=17, is_public=True),
            ],
            classes=[],
        )
        result = generator.generate(syms)
        assert len(result) == 1
        scenario = result[0]
        assert scenario.name == "auth_flow"
        assert len(scenario.steps) >= 4
        assert sorted(scenario.coverage_targets) == sorted(
            ["login_user", "refresh_token", "validate_session"]
        )

    def test_auth_steps_include_token_and_401(self, generator: ScenarioGenerator) -> None:
        syms = ModuleSymbols(
            name="auth.py",
            functions=[
                Symbol(name="login", line_start=1, line_end=3, is_public=True),
            ],
            classes=[],
        )
        result = generator.generate(syms)
        assert len(result) == 1
        expected_results = [s.expected_result for s in result[0].steps]
        assert any("401" in er for er in expected_results)
        assert any("token" in er.lower() for er in expected_results)


class TestNoMatchReturnsEmpty:
    def test_plain_module_no_match(self, generator: ScenarioGenerator) -> None:
        syms = ModuleSymbols(
            name="utils.py",
            functions=[
                Symbol(name="compute", line_start=1, line_end=5, is_public=True),
                Symbol(name="transform", line_start=7, line_end=11, is_public=True),
            ],
            classes=[],
        )
        result = generator.generate(syms)
        assert result == []

    def test_empty_module(self, generator: ScenarioGenerator) -> None:
        syms = ModuleSymbols(name="empty.py", functions=[], classes=[])
        result = generator.generate(syms)
        assert result == []


class TestMultiSymbolModule:
    def test_multiple_scenarios_from_same_module(self, generator: ScenarioGenerator) -> None:
        syms = ModuleSymbols(
            name="service.py",
            functions=[
                Symbol(name="create_record", line_start=1, line_end=5, is_public=True),
                Symbol(name="delete_record", line_start=7, line_end=11, is_public=True),
                Symbol(name="auth_login", line_start=13, line_end=17, is_public=True),
                Symbol(name="retry_request", line_start=19, line_end=23, is_public=True),
                Symbol(name="acquire_lock", line_start=25, line_end=29, is_public=True),
                Symbol(name="init_daemon", line_start=31, line_end=35, is_public=True),
            ],
            classes=[],
        )
        result = generator.generate(syms)
        assert len(result) == 5
        scenario_names = {s.name for s in result}
        assert scenario_names == {
            "crud_lifecycle",
            "auth_flow",
            "timeout_handling",
            "concurrent_edits",
            "daemon_restart",
        }

    def test_multiple_symbols_per_scenario(self, generator: ScenarioGenerator) -> None:
        syms = ModuleSymbols(
            name="db.py",
            functions=[
                Symbol(name="create_table", line_start=1, line_end=5, is_public=True),
                Symbol(name="update_row", line_start=7, line_end=11, is_public=True),
                Symbol(name="delete_row", line_start=13, line_end=17, is_public=True),
                Symbol(name="insert_batch", line_start=19, line_end=23, is_public=True),
                Symbol(name="remove_index", line_start=25, line_end=29, is_public=True),
            ],
            classes=[],
        )
        result = generator.generate(syms)
        assert len(result) == 1
        assert len(result[0].coverage_targets) == 5


class TestWithRealAnalyzer:
    def test_analyzer_feeds_generator(self, generator: ScenarioGenerator, analyzer: CodePathAnalyzer) -> None:
        content = """\
def create_entry(data: dict) -> int:
    return 1

def update_entry(id: int, data: dict) -> None:
    pass

def delete_entry(id: int) -> None:
    pass

def login(user: str, pw: str) -> str:
    return "token"

class SessionManager:
    def start(self):
        pass

    def stop(self):
        pass
"""
        path = write_temp_module(content)
        try:
            module_syms = analyzer.analyze(path)
            result = generator.generate(module_syms)
            assert len(result) >= 1
            crud = next((s for s in result if s.name == "crud_lifecycle"), None)
            assert crud is not None
            assert sorted(crud.coverage_targets) == sorted(["create_entry", "update_entry", "delete_entry"])
        finally:
            Path(path).unlink(missing_ok=True)

    def test_session_manager_triggers_auth(self, generator: ScenarioGenerator, analyzer: CodePathAnalyzer) -> None:
        content = """\
class SessionManager:
    \"\"\"Handles user sessions and token management.\"\"\"
    def login_user(self, creds: dict) -> str:
        return "token"

    def logout(self) -> None:
        pass

    def refresh_token(self, old: str) -> str:
        return "new_token"
"""
        path = write_temp_module(content)
        try:
            module_syms = analyzer.analyze(path)
            result = generator.generate(module_syms)
            auth = next((s for s in result if s.name == "auth_flow"), None)
            assert auth is not None
            assert "SessionManager" in auth.coverage_targets
        finally:
            Path(path).unlink(missing_ok=True)

    def test_class_with_public_methods(self, generator: ScenarioGenerator, analyzer: CodePathAnalyzer) -> None:
        content = """\
class TimeoutController:
    def apply_timeout(self, deadline: float) -> None:
        pass

    def retry_backoff(self, attempt: int) -> float:
        return 2.0

    def _internal(self):
        pass
"""
        path = write_temp_module(content)
        try:
            module_syms = analyzer.analyze(path)
            result = generator.generate(module_syms)
            timeout = next((s for s in result if s.name == "timeout_handling"), None)
            assert timeout is not None
            assert "TimeoutController" in timeout.coverage_targets
        finally:
            Path(path).unlink(missing_ok=True)


class TestScenarioStepShape:
    def test_step_dataclass(self) -> None:
        step = ScenarioStep(
            action="POST",
            target="/api/resource",
            expected_result="201 Created",
            assertions=["status == 201"],
        )
        assert step.action == "POST"
        assert step.target == "/api/resource"
        assert step.expected_result == "201 Created"
        assert step.assertions == ["status == 201"]

    def test_generated_scenario_dataclass(self) -> None:
        steps = [ScenarioStep(action="Invoke", target="fn", expected_result="ok")]
        scenario = GeneratedScenario(
            name="test",
            description="A test scenario",
            steps=steps,
            coverage_targets=["fn"],
        )
        assert scenario.name == "test"
        assert scenario.description == "A test scenario"
        assert len(scenario.steps) == 1
        assert scenario.coverage_targets == ["fn"]


class TestCustomCatalog:
    def test_custom_catalog_overrides_default(self) -> None:
        custom = [
            E2EScenario(
                name="custom_flow",
                description="Custom scenario",
                steps=["step1", "step2"],
                tags=["custom"],
            ),
        ]
        generator = ScenarioGenerator(scenario_catalog=custom)
        syms = ModuleSymbols(
            name="mod.py",
            functions=[Symbol(name="create_thing", line_start=1, line_end=3, is_public=True)],
            classes=[],
        )
        result = generator.generate(syms)
        assert result == []
