"""G13: prove acceptance_criteria and definition_of_done flow into dispatch prompt."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.event_loop.loop import EventLoop


class FakeTodo:
    """Minimal todo that exposes the structured-task-spec fields the wiring reads."""

    def __init__(self, **kwargs):
        self.todo_id = kwargs.get("todo_id", "TODO-0001")
        self.title = kwargs.get("title", "Test Todo")
        self.description = kwargs.get("description", "Test description")
        self.work_type = kwargs.get("work_type", "code")
        self.queue = kwargs.get("queue", "core")
        self.priority = kwargs.get("priority", 5)
        self.project_id = kwargs.get("project_id")
        self.prompt_profile = kwargs.get("prompt_profile")
        self.model_profile = kwargs.get("model_profile")
        self.acceptance_criteria = kwargs.get("acceptance_criteria")
        self.definition_of_done = kwargs.get("definition_of_done")


def _make_loop_for_dispatch(**kwargs):
    session = AsyncMock()
    db_result = MagicMock()
    db_result.scalars.return_value.all.return_value = []
    session.execute.return_value = db_result
    session.add = MagicMock()
    session.delete = AsyncMock()
    session.flush = AsyncMock()

    http_client = AsyncMock()
    http_client.post.return_value = MagicMock(status_code=202)

    variable_repo = AsyncMock()
    variable_repo.load_vars_for_project.return_value = {}

    return EventLoop(
        worker_base_url="http://worker:8000",
        config=kwargs.pop("config", {}),
        session=session,
        http_client=http_client,
        todo_repo=kwargs.pop("todo_repo", AsyncMock()),
        task_return_repo=kwargs.pop("task_return_repo", AsyncMock()),
        budget_guard=kwargs.pop("budget_guard", AsyncMock()),
        variable_repo=variable_repo,
        **kwargs,
    )


class TestTaskSpecWiring:
    @pytest.mark.asyncio
    async def test_acceptance_criteria_flows_into_task_context(self):
        """When a todo carries acceptance_criteria (JSON string), the dispatch
        prompt task_context includes a formatted version."""
        loop = _make_loop_for_dispatch()
        todo = FakeTodo(
            todo_id="TODO-G13-01",
            acceptance_criteria=json.dumps(
                ["Must pass all unit tests", "Must be under 100 lines"]
            ),
        )

        captured_context: dict = {}

        def capture_resolve(prompt_registry, prompt_profile, **task_context):
            captured_context.update(task_context)
            return "resolved prompt text"

        with patch(
            "general_ludd.event_loop.loop._resolve_prompt_text_static",
            new=capture_resolve,
        ):
            await loop._dispatch_execute_job(todo)

        assert "acceptance_criteria" in captured_context, (
            f"acceptance_criteria missing from task_context; "
            f"keys: {list(captured_context.keys())}"
        )
        criteria = captured_context["acceptance_criteria"]
        assert isinstance(criteria, str), f"expected str, got {type(criteria)}"
        assert "Must pass all unit tests" in criteria
        assert "Must be under 100 lines" in criteria

    @pytest.mark.asyncio
    async def test_definition_of_done_flows_into_task_context(self):
        """When a todo carries definition_of_done, it lands in task_context."""
        loop = _make_loop_for_dispatch()
        dod = "All tests pass, lint is clean, gate is green"
        todo = FakeTodo(todo_id="TODO-G13-02", definition_of_done=dod)

        captured_context: dict = {}

        def capture_resolve(prompt_registry, prompt_profile, **task_context):
            captured_context.update(task_context)
            return "resolved"

        with patch(
            "general_ludd.event_loop.loop._resolve_prompt_text_static",
            new=capture_resolve,
        ):
            await loop._dispatch_execute_job(todo)

        assert "definition_of_done" in captured_context
        assert captured_context["definition_of_done"] == dod

    @pytest.mark.asyncio
    async def test_both_fields_flow_together(self):
        """Both structured-task-spec fields flow into the dispatched prompt."""
        loop = _make_loop_for_dispatch()
        ac = json.dumps(["C1: no regressions", "C2: typecheck passes"])
        dod = "Gate is green"
        todo = FakeTodo(
            todo_id="TODO-G13-03",
            acceptance_criteria=ac,
            definition_of_done=dod,
            title="Wire G13 task spec",
            description="Wire structured task spec into dispatch pipeline",
        )

        captured_context: dict = {}

        def capture_resolve(prompt_registry, prompt_profile, **task_context):
            captured_context.update(task_context)
            return "resolved"

        with patch(
            "general_ludd.event_loop.loop._resolve_prompt_text_static",
            new=capture_resolve,
        ):
            await loop._dispatch_execute_job(todo)

        assert captured_context["todo_title"] == "Wire G13 task spec"
        assert (
            captured_context["todo_description"]
            == "Wire structured task spec into dispatch pipeline"
        )
        assert "C1: no regressions" in captured_context["acceptance_criteria"]
        assert "C2: typecheck passes" in captured_context["acceptance_criteria"]
        assert captured_context["definition_of_done"] == "Gate is green"

    @pytest.mark.asyncio
    async def test_null_acceptance_criteria_produces_empty_string(self):
        """None / empty acceptance_criteria -> empty string in context."""
        loop = _make_loop_for_dispatch()
        todo = FakeTodo(
            todo_id="TODO-G13-04",
            acceptance_criteria=None,
            definition_of_done=None,
        )

        captured_context: dict = {}

        def capture_resolve(prompt_registry, prompt_profile, **task_context):
            captured_context.update(task_context)
            return "resolved"

        with patch(
            "general_ludd.event_loop.loop._resolve_prompt_text_static",
            new=capture_resolve,
        ):
            await loop._dispatch_execute_job(todo)

        assert captured_context.get("acceptance_criteria", "") == ""
        assert captured_context.get("definition_of_done", "") == ""

    @pytest.mark.asyncio
    async def test_fallback_prompt_path_includes_structured_spec(self):
        """When prompt_profile is None (fallback synthesis), the task_context
        is populated BEFORE the fallback does its inline prompt synthesis."""
        loop = _make_loop_for_dispatch()
        ac = json.dumps(["AC: docstring completeness"])
        dod = "Verified by make test"
        todo = FakeTodo(
            todo_id="TODO-G13-05",
            acceptance_criteria=ac,
            definition_of_done=dod,
            prompt_profile=None,
        )

        captured_context: dict = {}

        def capture_resolve(prompt_registry, prompt_profile, **task_context):
            captured_context.update(task_context)
            return None  # triggers fallback

        with patch(
            "general_ludd.event_loop.loop._resolve_prompt_text_static",
            new=capture_resolve,
        ):
            await loop._dispatch_execute_job(todo)

        # The fallback runs AFTER the resolve and builds prompt_text from
        # the same task_context local dict, but our capture already has
        # the values that were passed.
        assert "acceptance_criteria" in captured_context
        assert "definition_of_done" in captured_context
