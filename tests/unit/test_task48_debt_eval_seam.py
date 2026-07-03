"""Task #48: wire the DebtEvaluator into the real execute dispatch seam.

These tests exercise ``EventLoop._dispatch_execute_job`` end-to-end (the same
path :func:`test_w74_mq_section_reaches_gateway` traces) and assert the
config-gated, default-OFF, NON-FATAL plan-time technical-debt hook:

* flag OFF (no ``debt_eval.enabled``) -> the evaluator is NEVER called and the
  dispatched prompt is unchanged (byte-for-byte identical dispatch).
* flag ON + a ``defer`` finding -> a BACKLOG child todo is created on the job
  session (parent_todo_id == the todo, created_by == "debt_evaluator", tagged
  ``tech-debt``).
* flag ON + a ``fold_in`` finding -> the executed prompt gains the
  "### Fold-in scope" header + gap; NO backlog todo is created.
* the evaluator raising -> dispatch still completes (the model is still
  invoked); the tick does not error.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from general_ludd.db.models import Base
from general_ludd.db.repository import TodoRepository
from general_ludd.event_loop.loop import EventLoop
from general_ludd.planning.debt_evaluator import DebtFinding, DebtFindings
from general_ludd.schemas.todo import TodoStatus

# ---------------------------------------------------------------------------
# Fixtures / fakes (mirroring test_w74_mq_section_reaches_gateway)
# ---------------------------------------------------------------------------


class _FakeTodo:
    """Minimal todo satisfying every attribute access in _dispatch_execute_job."""

    def __init__(
        self,
        todo_id: str = "TODO-48",
        work_type: str = "code",
        assigned_agent: str = "coder",
        prompt_profile: str | None = "test_profile.j2",
        queue: str = "core",
        priority: str = "medium",
        title: str = "Add retry to fetcher",
        description: str = "Wire retry into the fetch path",
        project_id: str | None = None,
        model_profile: str | None = None,
        resource_profile: str | None = "low_resource",
        plan_artifact: str | None = None,
    ) -> None:
        self.todo_id = todo_id
        self.work_type = work_type
        self.assigned_agent = assigned_agent
        self.prompt_profile = prompt_profile
        self.queue = queue
        self.priority = priority
        self.title = title
        self.description = description
        self.project_id = project_id
        self.model_profile = model_profile
        self.resource_profile = resource_profile
        self.plan_artifact = plan_artifact


class _FakePromptRegistry:
    PROMPT_BODY = "ORIGINAL TASK PROMPT: do the work"

    def render(self, profile: str, **kwargs: Any) -> str:
        return self.PROMPT_BODY


class _FakeGatewayResponse:
    content = "Model response content"


class _FakeModelGateway:
    """Records call_model invocations so the dispatched prompt can be asserted."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def call_model(
        self, profile_id: str, *, messages: list[dict[str, str]], **kwargs: Any
    ) -> Any:
        self.calls.append({"profile_id": profile_id, "messages": messages})
        return _FakeGatewayResponse()


def _make_loop(gateway: _FakeModelGateway, config: dict[str, Any]) -> EventLoop:
    """EventLoop wired for the runner path + model gateway (no DB session)."""
    mock_runner = MagicMock()
    mock_runner.prepare_job_dirs.return_value = {"root": "/tmp/test-task48-jobs"}
    mock_runner.write_vars.return_value = None
    mock_runner.run_playbook.return_value = None
    return EventLoop(
        config=config,
        runner=mock_runner,
        model_gateway=gateway,
        prompt_registry=_FakePromptRegistry(),
    )


def _passthrough_prepare(
    self_caps: Any, system_prompt: str, history: list[dict]
) -> list[dict]:
    result: list[dict] = []
    if system_prompt and system_prompt.strip():
        result.append({"role": "system", "content": system_prompt})
    result.extend(history)
    return result


def _defer_finding() -> DebtFinding:
    return DebtFinding(
        gap="add a caching layer for fetch results",
        kind="missing_feature",
        why_it_matters="repeated fetches are wasteful",
        recommendation="defer",
        feature_creep_rationale="new capability beyond this task",
        effort="large",
        touched_files=["src/pkg/cache.py"],
    )


def _fold_in_finding() -> DebtFinding:
    return DebtFinding(
        gap="handle None response before parse",
        kind="sharp_edge",
        why_it_matters="a None response would crash parse()",
        recommendation="fold_in",
        feature_creep_rationale="in-scope sharp edge",
        effort="small",
        touched_files=["src/pkg/fetch.py"],
    )


def _all_gateway_content(gateway: _FakeModelGateway) -> str:
    assert len(gateway.calls) == 1, f"expected 1 gateway call, got {len(gateway.calls)}"
    return "\n".join(m["content"] for m in gateway.calls[0]["messages"])


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestDebtEvalSeam:
    @pytest.mark.asyncio
    async def test_flag_off_evaluator_never_called_prompt_unchanged(self) -> None:
        """No debt_eval.enabled -> evaluator not invoked; dispatch prompt unchanged."""
        gateway = _FakeModelGateway()
        loop = _make_loop(gateway, config={})  # flag ABSENT
        spy = MagicMock()
        loop._debt_evaluator = spy
        todo = _FakeTodo()

        with patch(
            "general_ludd.agents.capabilities.AgentCapabilities.prepare_messages",
            _passthrough_prepare,
        ):
            await loop._dispatch_execute_job(todo)

        spy.evaluate.assert_not_called()
        content = _all_gateway_content(gateway)
        assert _FakePromptRegistry.PROMPT_BODY in content
        assert "Fold-in scope" not in content

    @pytest.mark.asyncio
    async def test_flag_on_defer_creates_backlog_child_todo(self) -> None:
        """defer finding -> BACKLOG child todo persisted on the job session."""
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        gateway = _FakeModelGateway()
        loop = _make_loop(gateway, config={"debt_eval": {"enabled": True}})
        loop._debt_evaluator = MagicMock()
        loop._debt_evaluator.evaluate.return_value = DebtFindings(
            findings=[_defer_finding()]
        )
        todo = _FakeTodo()

        try:
            async with factory() as session:
                with patch(
                    "general_ludd.agents.capabilities.AgentCapabilities.prepare_messages",
                    _passthrough_prepare,
                ):
                    await loop._dispatch_execute_job(todo, _session_override=session)

                repo = TodoRepository(session)
                backlog = await repo.list_by_status(TodoStatus.BACKLOG)
                assert len(backlog) == 1, "expected exactly one BACKLOG child todo"
                child = backlog[0]
                assert child.parent_todo_id == todo.todo_id
                assert child.created_by == "debt_evaluator"
                tags = child.tags if isinstance(child.tags, str) else json.dumps(child.tags)
                assert "tech-debt" in tags
                assert child.title.startswith("[tech-debt]")
        finally:
            await engine.dispose()

        # The evaluator WAS consulted.
        loop._debt_evaluator.evaluate.assert_called_once()

    @pytest.mark.asyncio
    async def test_flag_on_fold_in_addendum_in_prompt_no_todo(self) -> None:
        """fold_in finding -> prompt gains Fold-in header + gap; NO backlog todo."""
        engine = create_async_engine("sqlite+aiosqlite://", echo=False)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        gateway = _FakeModelGateway()
        loop = _make_loop(gateway, config={"debt_eval": {"enabled": True}})
        finding = _fold_in_finding()
        loop._debt_evaluator = MagicMock()
        loop._debt_evaluator.evaluate.return_value = DebtFindings(findings=[finding])
        todo = _FakeTodo()

        try:
            async with factory() as session:
                with patch(
                    "general_ludd.agents.capabilities.AgentCapabilities.prepare_messages",
                    _passthrough_prepare,
                ):
                    await loop._dispatch_execute_job(todo, _session_override=session)

                repo = TodoRepository(session)
                backlog = await repo.list_by_status(TodoStatus.BACKLOG)
                assert backlog == [], "fold_in must not create a backlog todo"
        finally:
            await engine.dispose()

        content = _all_gateway_content(gateway)
        assert "### Fold-in scope" in content
        assert finding.gap in content

    @pytest.mark.asyncio
    async def test_evaluator_raising_is_non_fatal(self) -> None:
        """Evaluator raising -> dispatch still completes; the model is still called."""
        gateway = _FakeModelGateway()
        loop = _make_loop(gateway, config={"debt_eval": {"enabled": True}})
        loop._debt_evaluator = MagicMock()
        loop._debt_evaluator.evaluate.side_effect = RuntimeError("boom")
        todo = _FakeTodo()

        with patch(
            "general_ludd.agents.capabilities.AgentCapabilities.prepare_messages",
            _passthrough_prepare,
        ):
            await loop._dispatch_execute_job(todo)

        # Dispatch proceeded despite the evaluator blowing up.
        assert len(gateway.calls) == 1
        content = _all_gateway_content(gateway)
        assert _FakePromptRegistry.PROMPT_BODY in content
