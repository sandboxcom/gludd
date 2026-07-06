"""Unit tests for daemon_wiring build_dispatch_handlers."""

from __future__ import annotations

from unittest.mock import MagicMock

from general_ludd.daemon_wiring import (
    build_dispatch_handlers,
    make_collection_handler,
    make_mcp_handler,
    make_role_handler,
    make_skill_handler,
    make_spend_guarded_executor,
)


class TestBuildDispatchHandlers:
    def test_returns_all_keys(self):
        handlers = build_dispatch_handlers(
            mcp_client=MagicMock(),
            skill_registry=MagicMock(),
            agent_dispatcher=MagicMock(),
            runner_adapter=MagicMock(),
        )
        assert set(handlers.keys()) == {
            "mcp_handler", "skill_handler", "role_handler", "collection_handler"
        }

    def test_all_none_subsystems(self):
        handlers = build_dispatch_handlers(
            mcp_client=None,
            skill_registry=None,
            agent_dispatcher=None,
            runner_adapter=None,
        )
        assert handlers["mcp_handler"] is None
        assert handlers["skill_handler"] is None
        assert handlers["role_handler"] is None
        assert handlers["collection_handler"] is None

    def test_mixed_subsystems(self):
        handlers = build_dispatch_handlers(
            mcp_client=MagicMock(),
            skill_registry=None,
            agent_dispatcher=MagicMock(),
            runner_adapter=None,
        )
        assert callable(handlers["mcp_handler"])
        assert handlers["skill_handler"] is None
        assert callable(handlers["role_handler"])
        assert handlers["collection_handler"] is None


class TestMakeMcpHandler:
    def test_none_client_returns_none(self):
        assert make_mcp_handler(None) is None

    def test_mcp_client_returns_callable(self):
        handler = make_mcp_handler(MagicMock())
        assert callable(handler)


class TestMakeSkillHandler:
    def test_none_registry_returns_none(self):
        assert make_skill_handler(None) is None

    def test_registry_returns_callable(self):
        handler = make_skill_handler(MagicMock())
        assert callable(handler)


class TestMakeRoleHandler:
    def test_none_dispatcher_returns_none(self):
        assert make_role_handler(None) is None

    def test_dispatcher_returns_callable(self):
        handler = make_role_handler(MagicMock())
        assert callable(handler)


class TestMakeCollectionHandler:
    def test_none_adapter_returns_none(self):
        handler = make_collection_handler(None)
        assert handler is None

    def test_adapter_returns_callable(self):
        adapter = MagicMock()
        adapter.private_data_dir = "/tmp/gludd"
        handler = make_collection_handler(adapter)
        assert callable(handler)


class TestMakeSpendGuardedExecutor:
    @staticmethod
    async def _dummy_executor(*args, **kwargs):
        return "result"

    async def test_no_limiter_is_passthrough(self):
        wrapped = make_spend_guarded_executor(
            executor=self._dummy_executor, spend_limiter=None
        )
        result = await wrapped()
        assert result == "result"

    async def test_with_limiter_admits_and_calls(self):
        limiter = MagicMock()
        limiter.try_charge.return_value = True
        wrapped = make_spend_guarded_executor(
            executor=self._dummy_executor,
            spend_limiter=limiter,
            projected_cost_usd=0.001,
        )
        result = await wrapped("arg1", key="val")
        assert result == "result"
        limiter.try_charge.assert_called_once_with(0.001, kind="token")

    async def test_with_limiter_denies_and_defers(self):
        limiter = MagicMock()
        limiter.try_charge.return_value = False
        wrapped = make_spend_guarded_executor(
            executor=self._dummy_executor,
            spend_limiter=limiter,
            projected_cost_usd=0.001,
        )
        result = await wrapped()
        assert result == "deferred:spend_limit_exceeded"
