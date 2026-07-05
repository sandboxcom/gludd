"""Integration tests for G13 structured task spec daemon wiring.

Proves acceptance_criteria flows through the API (POST → DB → GET),
TaskDefinition schema validation works, structured task spec parsing
from sprint documents, and _format_acceptance_criteria works from loop.py.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import pytest_asyncio
import yaml
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import Base
from general_ludd.db.repository import TodoRepository
from general_ludd.event_loop.loop import _format_acceptance_criteria
from general_ludd.routers.todos import register
from general_ludd.schemas.task_definition import TaskDefinition


@pytest_asyncio.fixture
async def test_app():
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        from general_ludd.daemon import _daemon_state

        _daemon_state["todos"] = []
        _daemon_state["tick_metrics"] = {}
        _daemon_state["quality_gate"] = {}

        app = FastAPI()
        app.state._session_factory = factory
        app.state._db_engine = engine
        app.state._config_dir = None
        app.state._startup_config = {}
        app.state.log_level = "info"
        app.state.tick_interval = 1.0
        app.state.event_loop = None
        app.state._templates_dir = None
        app.state._playbooks_dir = None

        register(app, _daemon_state)
        yield app, engine, factory
    finally:
        await engine.dispose()


class TestAcceptanceCriteriaApiFlow:
    @pytest.mark.asyncio
    async def test_acceptance_criteria_flows_post_to_get(self, test_app) -> None:
        app, _engine, factory = test_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/todos",
                json={
                    "title": "Implement payment gateway",
                    "acceptance_criteria": [
                        "Users can pay with credit card",
                        "Payment confirmation email is sent",
                        "Failed payments show error message",
                        "PCI compliance validation passes",
                    ],
                },
            )
            assert resp.status_code == 201
            data = resp.json()
            todo_id = data["todo_id"]

            resp_get = await client.get("/api/todos", params={"todo_id": todo_id})
            assert resp_get.status_code == 200
            todos = resp_get.json()
            matching = [t for t in todos if t["todo_id"] == todo_id]
            assert len(matching) == 1
            todo = matching[0]
            assert "acceptance_criteria" in todo
            criteria = todo["acceptance_criteria"]
            assert len(criteria) == 4
            assert "PCI compliance validation passes" in criteria

            async with factory() as session:
                repo = TodoRepository(session)
                db_todo = await repo.get_by_id(todo_id)
                assert db_todo is not None
                raw = db_todo.acceptance_criteria
                parsed = json.loads(raw) if isinstance(raw, str) else raw
                assert len(parsed) == 4
                assert "Users can pay with credit card" in parsed

    @pytest.mark.asyncio
    async def test_empty_acceptance_criteria_defaults_to_list(self, test_app) -> None:
        app, _engine, _factory = test_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/todos",
                json={"title": "Simple task without criteria"},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["acceptance_criteria"] == []

    @pytest.mark.asyncio
    async def test_definition_of_done_persisted_with_criteria(self, test_app) -> None:
        app, _engine, factory = test_app
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/todos",
                json={
                    "title": "Deploy monitoring stack",
                    "acceptance_criteria": [
                        "Dashboards load in under 2 seconds",
                        "Alerts fire within 30 seconds of threshold breach",
                    ],
                    "definition_of_done": "Monitoring deployed to production, dashboards verified, alerts tested",
                },
            )
            assert resp.status_code == 201
            data = resp.json()
            todo_id = data["todo_id"]
            assert len(data["acceptance_criteria"]) == 2
            assert data["definition_of_done"] == "Monitoring deployed to production, dashboards verified, alerts tested"

            async with factory() as session:
                repo = TodoRepository(session)
                db_todo = await repo.get_by_id(todo_id)
                assert db_todo is not None
                expected_dod = (
                    "Monitoring deployed to production, dashboards verified, alerts tested"
                )
                assert db_todo.definition_of_done == expected_dod


class TestTaskDefinitionSchemaValidation:
    def test_valid_task_definition_required_fields(self) -> None:
        td = TaskDefinition(name="Implement login")
        assert td.name == "Implement login"
        assert td.description == ""
        assert td.acceptance_criteria == []
        assert td.definition_of_done == ""

    def test_task_definition_name_must_not_be_empty(self) -> None:
        with pytest.raises(ValidationError):
            TaskDefinition(name="")

    def test_task_definition_name_must_not_be_whitespace(self) -> None:
        with pytest.raises(ValidationError):
            TaskDefinition(name="   ")

    def test_task_definition_name_stripped(self) -> None:
        td = TaskDefinition(name="  Add rate limiter  ")
        assert td.name == "Add rate limiter"

    def test_task_definition_with_all_fields(self) -> None:
        td = TaskDefinition(
            name="Build CI pipeline",
            description="Set up GitHub Actions CI pipeline with lint, test, and deploy jobs",
            target_agent="build",
            queue="ci",
            work_type="code",
            priority=2,
            tags=["ci", "infrastructure"],
            dependencies=["install-dependencies"],
            acceptance_criteria=[
                "Lint job runs on every PR",
                "Tests pass before merge",
                "Deploy job runs on main branch push",
            ],
            definition_of_done="CI pipeline green, all jobs passing, deployment verified",
            test_commands=["make lint", "make test", "make typecheck"],
            model_profile="sonnet",
            prompt_profile="builder",
            resource_profile="high_resource",
            risk_level="medium",
            vars={"pipeline_name": "ci-cd-main"},
        )
        assert td.name == "Build CI pipeline"
        assert td.priority == 2
        assert len(td.acceptance_criteria) == 3
        assert td.definition_of_done == "CI pipeline green, all jobs passing, deployment verified"
        assert len(td.test_commands) == 3
        assert td.model_profile == "sonnet"
        assert td.prompt_profile == "builder"
        assert td.risk_level == "medium"

    def test_task_definition_to_todo_conversion(self) -> None:
        td = TaskDefinition(
            name="Add export feature",
            description="CSV and JSON export endpoints",
            target_agent="backend",
            queue="core",
            work_type="code",
            priority=1,
            acceptance_criteria=[
                "CSV export returns valid CSV",
                "JSON export returns valid JSON",
            ],
            definition_of_done="Export endpoints tested and documented",
            test_commands=["make test-exports"],
        )
        todo = td.to_todo()
        assert todo.title == "Add export feature"
        assert todo.description == "CSV and JSON export endpoints"
        assert todo.assigned_agent == "backend"
        assert todo.acceptance_criteria == ["CSV export returns valid CSV", "JSON export returns valid JSON"]
        assert todo.definition_of_done == "Export endpoints tested and documented"


class TestStructuredTaskSpecParsing:
    def test_parse_sprint_document_as_task_definitions(self) -> None:
        sprint_yaml = {
            "tasks": [
                {
                    "name": "Create user model",
                    "description": "SQLAlchemy User model",
                    "acceptance_criteria": ["User model has email and password fields"],
                },
                {
                    "name": "Build login endpoint",
                    "description": "POST /auth/login",
                    "acceptance_criteria": [
                        "Valid credentials return JWT",
                        "Invalid credentials return 401",
                    ],
                    "definition_of_done": "Login endpoint tested with unit and integration tests",
                },
                {
                    "name": "Add password reset flow",
                    "description": "Forgot password → email → reset",
                    "acceptance_criteria": [
                        "User receives reset email",
                        "Reset link expires after 1 hour",
                        "New password meets complexity requirements",
                    ],
                },
            ]
        }

        task_defs = [TaskDefinition(**item) for item in sprint_yaml["tasks"]]
        assert len(task_defs) == 3

        assert task_defs[0].name == "Create user model"
        assert task_defs[0].acceptance_criteria == ["User model has email and password fields"]

        assert task_defs[1].name == "Build login endpoint"
        assert len(task_defs[1].acceptance_criteria) == 2
        assert task_defs[1].definition_of_done == "Login endpoint tested with unit and integration tests"

        assert task_defs[2].name == "Add password reset flow"
        assert len(task_defs[2].acceptance_criteria) == 3

    def test_load_task_definitions_from_yaml_file(self) -> None:
        from general_ludd.config.task_loader import load_task_definitions

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump(
                {
                    "tasks": [
                        {
                            "name": "Database migration",
                            "description": "Add new columns to users table",
                            "acceptance_criteria": [
                                "Migration runs without errors",
                                "Rollback restores previous schema",
                            ],
                            "definition_of_done": "Migration tested on staging, verified on production clone",
                        },
                        {
                            "name": "API versioning",
                            "description": "Add v2 API with backwards compatibility",
                            "acceptance_criteria": [
                                "v1 endpoints continue to work",
                                "v2 endpoints return new fields",
                            ],
                        },
                    ]
                },
                f,
            )
            f.flush()
            path = f.name

        try:
            task_defs = load_task_definitions(path)
            assert len(task_defs) == 2

            assert task_defs[0].name == "Database migration"
            assert len(task_defs[0].acceptance_criteria) == 2
            assert "Migration runs without errors" in task_defs[0].acceptance_criteria
            assert task_defs[0].definition_of_done == "Migration tested on staging, verified on production clone"

            assert task_defs[1].name == "API versioning"
            assert len(task_defs[1].acceptance_criteria) == 2
        finally:
            Path(path).unlink(missing_ok=True)

    def test_load_task_definitions_empty_file(self) -> None:
        from general_ludd.config.task_loader import load_task_definitions

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            f.write("")
            f.flush()
            path = f.name

        try:
            task_defs = load_task_definitions(path)
            assert task_defs == []
        finally:
            Path(path).unlink(missing_ok=True)

    def test_load_task_definitions_no_tasks_key(self) -> None:
        from general_ludd.config.task_loader import load_task_definitions

        with tempfile.NamedTemporaryFile(mode="w", suffix=".yml", delete=False) as f:
            yaml.dump({"other_key": "value"}, f)
            f.flush()
            path = f.name

        try:
            task_defs = load_task_definitions(path)
            assert task_defs == []
        finally:
            Path(path).unlink(missing_ok=True)


class TestFormatAcceptanceCriteria:
    def test_format_valid_json_array(self) -> None:
        result = _format_acceptance_criteria(
            json.dumps(["Login page loads", "Error messages display", "Rate limiting works"])
        )
        expected = "- Login page loads\n- Error messages display\n- Rate limiting works"
        assert result == expected

    def test_format_none_returns_empty(self) -> None:
        assert _format_acceptance_criteria(None) == ""

    def test_format_empty_string_returns_empty(self) -> None:
        assert _format_acceptance_criteria("") == ""

    def test_format_empty_list_returns_empty(self) -> None:
        assert _format_acceptance_criteria("[]") == ""

    def test_format_invalid_json_returns_raw(self) -> None:
        raw = "not valid json at all"
        assert _format_acceptance_criteria(raw) == raw

    def test_format_single_criterion(self) -> None:
        result = _format_acceptance_criteria(json.dumps(["All tests pass"]))
        assert result == "- All tests pass"

    def test_format_hyphenated_criteria(self) -> None:
        criteria = [
            "Server starts on port 8080",
            "Healthcheck returns 200",
            "Graceful shutdown within 30 seconds",
        ]
        result = _format_acceptance_criteria(json.dumps(criteria))
        lines = result.split("\n")
        assert len(lines) == 3
        assert lines[0] == "- Server starts on port 8080"
        assert lines[1] == "- Healthcheck returns 200"
        assert lines[2] == "- Graceful shutdown within 30 seconds"

    def test_format_criteria_with_special_characters(self) -> None:
        criteria = [
            "Users can log in with email & password",
            "Rate-limited after 5 failed attempts (configurable)",
        ]
        result = _format_acceptance_criteria(json.dumps(criteria))
        expected = (
            "- Users can log in with email & password\n"
            "- Rate-limited after 5 failed attempts (configurable)"
        )
        assert result == expected

    def test_format_number_input_returns_raw(self) -> None:
        assert _format_acceptance_criteria("42") == "42"
