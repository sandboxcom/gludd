"""TDD tests for STS P5 — SubagentTokenInjector wired into dispatch pipeline."""

from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.agents.dispatcher import AgentDispatcher
from general_ludd.agents.registry import AgentRegistry
from general_ludd.agents.types import AgentTask
from general_ludd.sts.injector import SubagentTokenInjector

# ------------------------------------------------------------------ helpers


def _make_task(**overrides: object) -> AgentTask:
    defaults: dict[str, object] = {
        "task_id": "t-1",
        "description": "test task",
        "agent_name": "research",
        "prompt": "do some research",
        "invoker_name": "orchestrator",
        "parent_task_id": "p-1",
        "project_id": "proj-1",
        "depth": 0,
        "env": {},
        "tools": None,
    }
    defaults.update(overrides)
    return AgentTask(**defaults)  # type: ignore[arg-type]


def _make_dispatcher(
    *,
    injector: SubagentTokenInjector | None = None,
    registry: AgentRegistry | None = None,
) -> AgentDispatcher:
    reg = registry or _make_registry()
    executor = AsyncMock(return_value="ok")
    disp = AgentDispatcher(reg, executor=executor)
    if injector is not None:
        disp.set_sts_injector(injector)
    return disp


def _make_registry() -> AgentRegistry:
    from general_ludd.agents.types import AgentConfig, AgentPermission, AgentType

    registry = AgentRegistry()
    orchestrator = AgentConfig(
        name="orchestrator",
        description="Orchestrator agent",
        type=AgentType.PRIMARY,
        model_profile="sonnet",
        permissions=AgentPermission(
            can_edit=True,
            can_bash=True,
            can_read=True,
            can_dispatch_subagents=True,
            allowed_subagents=["*"],
        ),
    )
    research = AgentConfig(
        name="research",
        description="Research subagent",
        type=AgentType.SUBAGENT,
        model_profile="sonnet",
        permissions=AgentPermission(
            can_edit=False,
            can_bash=False,
            can_read=True,
            can_dispatch_subagents=False,
            allowed_subagents=["*"],
        ),
    )
    registry.register(orchestrator)
    registry.register(research)
    return registry


# ------------------------------------------------------------------ autouse mock


@pytest.fixture(autouse=True)
def _mock_agent_token_model() -> ExitStack:
    """Prevent AgentTokenModel instantiation from triggering SQLAlchemy mapper
    config (the ``order_by=lambda: TodoEventModel.id`` at db/models.py:307
    raises ``NotImplementedError`` during mapper configuration)."""
    stack = ExitStack()
    mock_model = stack.enter_context(patch("general_ludd.db.models.AgentTokenModel"))
    mock_model.side_effect = lambda **kw: type("_Record", (), kw)()
    return stack


# ------------------------------------------------------------------ minter


class _FakeMinter:
    def __init__(self, role_id: str = "role-test", secret_id: str = "sec-test"):
        self._role_id = role_id
        self._secret_id = secret_id

    async def mint(self, agent_id: str, parent_agent_id: str, scope: object = None) -> object:
        return _FakeCredentials(self._role_id, self._secret_id)


class _FakeCredentials:
    def __init__(self, role_id: str, secret_id: str):
        self.role_id = role_id
        self.secret_id = secret_id


# ------------------------------------------------------------------ tests


class TestSubagentTokenInjectorEnrich:
    """P5: SubagentTokenInjector.enrich() mints + stores + injects on dispatch."""

    @pytest.mark.asyncio
    async def test_enrich_sets_env_vars_on_task(self) -> None:
        """enrich() must set GLUDD_STS_ROLE_ID + GLUDD_STS_SECRET_ID on task.env."""
        minter = _FakeMinter("role-a", "sec-a")
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        task = _make_task(task_id="agent-x")

        await injector.enrich(task)

        assert task.env["GLUDD_STS_ROLE_ID"] == "role-a", (
            "P5 gap: enrich() must set GLUDD_STS_ROLE_ID on task.env so the "
            "executor can propagate it to the subagent process"
        )
        assert task.env["GLUDD_STS_SECRET_ID"] == "sec-a", "P5 gap: enrich() must set GLUDD_STS_SECRET_ID on task.env"

    @pytest.mark.asyncio
    async def test_enrich_stores_token_record(self) -> None:
        """enrich() must call store.store() with an AgentTokenModel record."""
        minter = _FakeMinter("role-b", "sec-b")
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        task = _make_task(task_id="agent-store")

        await injector.enrich(task)

        store.store.assert_awaited_once()
        record = store.store.call_args[0][0]
        assert record.role_id == "role-b"
        assert record.role_name == "agent-agent-store"
        assert record.token_id == "tok-agent-store"

    @pytest.mark.asyncio
    async def test_enrich_uses_parent_agent_from_invoker(self) -> None:
        """enrich() derives parent_agent_id from invoker_name."""
        minter = _FakeMinter()
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        task = _make_task(task_id="child", invoker_name="parent-agent")

        await injector.enrich(task)

        record = store.store.call_args[0][0]
        assert record.parent_agent_id == "parent-agent", (
            "P5 gap: enrich() must use invoker_name as parent_agent_id for audit attribution"
        )

    @pytest.mark.asyncio
    async def test_enrich_falls_back_to_parent_task_id(self) -> None:
        """enrich() uses parent_task_id when invoker_name is empty."""
        minter = _FakeMinter()
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        task = _make_task(task_id="orphan", invoker_name="", parent_task_id="grandparent")

        await injector.enrich(task)

        record = store.store.call_args[0][0]
        assert record.parent_agent_id == "grandparent"

    @pytest.mark.asyncio
    async def test_enrich_falls_back_to_root(self) -> None:
        """enrich() uses 'root' when both invoker_name and parent_task_id are empty."""
        minter = _FakeMinter()
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        task = _make_task(task_id="rootless", invoker_name="", parent_task_id="")

        await injector.enrich(task)

        record = store.store.call_args[0][0]
        assert record.parent_agent_id == "root"


class TestDispatchOneStsInjection:
    """P5: AgentDispatcher.dispatch_one() injects STS tokens via the injector."""

    @pytest.mark.asyncio
    async def test_dispatch_one_calls_injector_enrich(self) -> None:
        """dispatch_one() calls injector.enrich(task) when sts_injector is set."""
        minter = _FakeMinter("role-inj", "sec-inj")
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        dispatcher = _make_dispatcher(injector=injector)
        task = _make_task()

        result = await dispatcher.dispatch_one(task)

        assert result.status == "completed"
        assert task.env.get("GLUDD_STS_ROLE_ID") == "role-inj", (
            "P5 gap: dispatch_one() must call injector.enrich() so STS env vars are set before the executor runs"
        )
        assert task.env.get("GLUDD_STS_SECRET_ID") == "sec-inj"

    @pytest.mark.asyncio
    async def test_dispatch_one_noops_without_injector(self) -> None:
        """dispatch_one() works fine when sts_injector is None (no STS)."""
        dispatcher = _make_dispatcher(injector=None)
        task = _make_task()

        result = await dispatcher.dispatch_one(task)

        assert result.status == "completed"
        assert "GLUDD_STS_ROLE_ID" not in task.env, (
            "P5 gap: when sts_injector is None, dispatch_one() must still work (STS is optional infrastructure)"
        )

    @pytest.mark.asyncio
    async def test_dispatch_one_injects_before_executor(self) -> None:
        """STS env vars must be present on task BEFORE the executor is called."""
        capture: dict[str, object] = {}

        async def _capturing_executor(task: AgentTask) -> str:
            capture["role_id_at_exec"] = task.env.get("GLUDD_STS_ROLE_ID")
            capture["secret_id_at_exec"] = task.env.get("GLUDD_STS_SECRET_ID")
            return "done"

        minter = _FakeMinter("role-pre", "sec-pre")
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        reg = _make_registry()
        dispatcher = AgentDispatcher(reg, executor=_capturing_executor)
        dispatcher.set_sts_injector(injector)
        task = _make_task()

        await dispatcher.dispatch_one(task)

        assert capture["role_id_at_exec"] == "role-pre", (
            "P5 gap: executor must see GLUDD_STS_ROLE_ID in task.env. "
            "If it doesn't, the injector ran AFTER the executor."
        )
        assert capture["secret_id_at_exec"] == "sec-pre"


class TestSetStsInjectorApi:
    """P5: set_sts_injector() API works correctly."""

    def test_set_sts_injector_stores_reference(self) -> None:
        """set_sts_injector() stores the injector for use by dispatch_one."""
        dispatcher = _make_dispatcher()
        injector = SubagentTokenInjector(_FakeMinter(), AsyncMock(), MagicMock())

        dispatcher.set_sts_injector(injector)

        assert dispatcher._sts_injector is injector, (
            "P5 gap: set_sts_injector() must store the injector reference "
            "on the dispatcher so dispatch_one() can call enrich()"
        )

    @pytest.mark.asyncio
    async def test_set_sts_injector_is_callable_with_any_object(self) -> None:
        """set_sts_injector() accepts duck-typed injector objects (late binding)."""
        dispatcher = _make_dispatcher()

        class DuckInjector:
            async def enrich(self, task: object) -> None:
                return None

        duck = DuckInjector()
        dispatcher.set_sts_injector(duck)

        task = _make_task()
        result = await dispatcher.dispatch_one(task)
        assert result.status == "completed"


class TestEnrichNonOptionalMinimalRegression:
    """P5: regression — enrich() handles edge cases without crashing."""

    @pytest.mark.asyncio
    async def test_enrich_does_not_mutate_other_task_fields(self) -> None:
        """enrich() only adds to env; does not clobber task_id, agent_name, etc."""
        minter = _FakeMinter("role-safe", "sec-safe")
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        task = _make_task(task_id="stable", agent_name="research", description="hi")

        await injector.enrich(task)

        assert task.task_id == "stable"
        assert task.agent_name == "research"
        assert task.description == "hi"

    @pytest.mark.asyncio
    async def test_enrich_preserves_existing_env_keys(self) -> None:
        """enrich() must not overwrite existing env keys the caller sets."""
        minter = _FakeMinter("role-merge", "sec-merge")
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        task = _make_task()
        task.env["PRE_EXISTING_KEY"] = "keep-me"

        await injector.enrich(task)

        assert task.env["PRE_EXISTING_KEY"] == "keep-me", (
            "P5 gap: enrich() must merge its env vars — never replace the entire env dict"
        )
