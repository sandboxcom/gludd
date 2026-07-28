"""Unit tests for SubagentTokenInjector — enrich, env_vars, and dispatch wiring."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.agents.types import AgentTask
from general_ludd.sts.injector import SubagentTokenInjector


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


class _FakeCredentials:
    def __init__(self, role_id: str, secret_id: str):
        self.role_id = role_id
        self.secret_id = secret_id


class _FakeMinter:
    def __init__(self, role_id: str = "role-test", secret_id: str = "sec-test"):
        self._role_id = role_id
        self._secret_id = secret_id

    async def mint(self, agent_id: str, parent_agent_id: str, scope: object = None) -> _FakeCredentials:
        return _FakeCredentials(self._role_id, self._secret_id)


@pytest.fixture(autouse=True)
def _mock_agent_token_model():
    with patch("general_ludd.db.models.AgentTokenModel", new_callable=MagicMock) as mock_model:
        mock_model.side_effect = lambda **kw: type("_Record", (), kw)()
        yield


class TestSubagentTokenInjectorInit:
    def test_stores_minter(self):
        minter = _FakeMinter()
        injector = SubagentTokenInjector(minter, MagicMock(), MagicMock())
        assert injector._minter is minter

    def test_stores_store(self):
        store = MagicMock()
        injector = SubagentTokenInjector(_FakeMinter(), store, MagicMock())
        assert injector._store is store

    def test_stores_dispatcher(self):
        dispatcher = MagicMock()
        injector = SubagentTokenInjector(_FakeMinter(), MagicMock(), dispatcher)
        assert injector._dispatcher is dispatcher


class TestSubagentTokenInjectorEnrich:
    @pytest.mark.asyncio
    async def test_enrich_sets_env_vars_on_task(self):
        minter = _FakeMinter("role-a", "sec-a")
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        task = _make_task(task_id="agent-x")

        await injector.enrich(task)

        assert task.env["GLUDD_STS_ROLE_ID"] == "role-a"
        assert task.env["GLUDD_STS_SECRET_ID"] == "sec-a"

    @pytest.mark.asyncio
    async def test_enrich_stores_token_record(self):
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
    async def test_enrich_uses_parent_agent_from_invoker(self):
        minter = _FakeMinter()
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        task = _make_task(task_id="child", invoker_name="parent-agent")

        await injector.enrich(task)

        record = store.store.call_args[0][0]
        assert record.parent_agent_id == "parent-agent"

    @pytest.mark.asyncio
    async def test_enrich_falls_back_to_parent_task_id(self):
        minter = _FakeMinter()
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        task = _make_task(task_id="orphan", invoker_name="", parent_task_id="grandparent")

        await injector.enrich(task)

        record = store.store.call_args[0][0]
        assert record.parent_agent_id == "grandparent"

    @pytest.mark.asyncio
    async def test_enrich_falls_back_to_root(self):
        minter = _FakeMinter()
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        task = _make_task(task_id="rootless", invoker_name="", parent_task_id="")

        await injector.enrich(task)

        record = store.store.call_args[0][0]
        assert record.parent_agent_id == "root"

    @pytest.mark.asyncio
    async def test_enrich_does_not_mutate_other_task_fields(self):
        minter = _FakeMinter("role-safe", "sec-safe")
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        task = _make_task(task_id="stable", agent_name="research", description="hi")

        await injector.enrich(task)

        assert task.task_id == "stable"
        assert task.agent_name == "research"
        assert task.description == "hi"

    @pytest.mark.asyncio
    async def test_enrich_preserves_existing_env_keys(self):
        minter = _FakeMinter("role-merge", "sec-merge")
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        task = _make_task()
        task.env["PRE_EXISTING_KEY"] = "keep-me"

        await injector.enrich(task)

        assert task.env["PRE_EXISTING_KEY"] == "keep-me"

    @pytest.mark.asyncio
    async def test_enrich_scope_hash_is_empty_string(self):
        minter = _FakeMinter()
        store = AsyncMock()
        injector = SubagentTokenInjector(minter, store, MagicMock())
        task = _make_task(task_id="agent-scope")

        await injector.enrich(task)

        record = store.store.call_args[0][0]
        assert record.scope_hash == ""


class TestSubagentTokenInjectorEnvVars:
    @pytest.mark.asyncio
    async def test_env_vars_returns_correct_keys(self):
        minter = _FakeMinter("role-env", "sec-env")
        injector = SubagentTokenInjector(minter, MagicMock(), MagicMock())

        result = await injector.env_vars(agent_id="a1", parent_agent_id="p1")

        assert result == {
            "GLUDD_STS_ROLE_ID": "role-env",
            "GLUDD_STS_SECRET_ID": "sec-env",
            "GLUDD_STS_TOKEN_ID": "tok-a1",
        }

    @pytest.mark.asyncio
    async def test_env_vars_returns_empty_when_minter_none(self):
        injector = SubagentTokenInjector(None, MagicMock(), MagicMock())  # type: ignore[arg-type]

        result = await injector.env_vars(agent_id="a1", parent_agent_id="p1")

        assert result == {}

    @pytest.mark.asyncio
    async def test_env_vars_calls_minter_mint(self):
        minter = MagicMock()
        minter.mint = AsyncMock(return_value=_FakeCredentials("r", "s"))
        injector = SubagentTokenInjector(minter, MagicMock(), MagicMock())

        await injector.env_vars(agent_id="a42", parent_agent_id="p42")

        minter.mint.assert_awaited_once_with(
            agent_id="a42",
            parent_agent_id="p42",
        )

    @pytest.mark.asyncio
    async def test_env_vars_token_id_prefixed_with_tok(self):
        minter = _FakeMinter("role-tok", "sec-tok")
        injector = SubagentTokenInjector(minter, MagicMock(), MagicMock())

        result = await injector.env_vars(agent_id="my-agent", parent_agent_id="p1")

        assert result["GLUDD_STS_TOKEN_ID"] == "tok-my-agent"

    @pytest.mark.asyncio
    async def test_env_vars_values_are_strings(self):
        minter = _FakeMinter("role-str", "sec-str")
        injector = SubagentTokenInjector(minter, MagicMock(), MagicMock())

        result = await injector.env_vars(agent_id="x", parent_agent_id="y")

        for value in result.values():
            assert isinstance(value, str)
