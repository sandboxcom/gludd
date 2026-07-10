"""Integration: LangChain/LangGraph daemon wiring — routers, graph agent, checkpointer.

Proves langchain integration works end-to-end: config flags, graph agent loop,
playbook routing, model router, and checkpoint save/load — all without real API calls.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import MagicMock, patch

from general_ludd.daemon import create_daemon_app
from general_ludd.execution.graph_checkpointer import TickCheckpointer, get_checkpointer
from general_ludd.execution.langgraph_agent import LangGraphAgentLoop
from general_ludd.models.langchain_router import LangChainModelRouter

# ── helpers ──────────────────────────────────────────────────────────────────

def _build_app(**kw: Any) -> Any:
    with patch.dict(os.environ, {"GLUDD_ALLOW_NO_AUTH": "1"}):
        app = create_daemon_app(**kw)
    return app


# ── test classes ─────────────────────────────────────────────────────────────


class TestLangchainRoutingConfigFlags:
    """Daemon app creates + routing config flags exist on UserConfig."""

    def test_daemon_app_creates_with_langchain_flags_defaulting_off(self) -> None:
        app = _build_app(tick_interval=999.0)
        assert app is not None
        assert app.state is not None

    def test_user_config_has_langchain_routing_fields(self) -> None:
        from general_ludd.config.user_config import UserConfig

        uc = UserConfig()
        assert hasattr(uc, "use_langchain_routing")
        assert hasattr(uc, "use_langchain_retry")
        assert uc.use_langchain_routing is False
        assert uc.use_langchain_retry is False

    def test_user_config_langchain_flags_serialise(self) -> None:
        from general_ludd.config.user_config import UserConfig

        uc = UserConfig(use_langchain_routing=True, use_langchain_retry=True)
        d = uc.model_dump()
        assert d["use_langchain_routing"] is True
        assert d["use_langchain_retry"] is True


class TestLangGraphAgentLoopDirectly:
    """LangGraphAgentLoop lifecycle with mock model gateway — no real API call."""

    def test_creates_agent_loop_with_mock_gateway(self) -> None:
        mock_gateway = MagicMock()
        mock_gateway.call_model.return_value = MagicMock(content="mock response")
        agent = LangGraphAgentLoop(
            model_gateway=mock_gateway,
            chat_model=None,
            mcp_client=None,
        )
        assert agent is not None
        assert agent.is_available() is False

    def test_is_available_false_without_mcp_client(self) -> None:
        mock_gateway = MagicMock()
        agent = LangGraphAgentLoop(
            model_gateway=mock_gateway,
            chat_model=None,
            mcp_client=None,
        )
        assert agent.is_available() is False

    def test_agent_loop_with_mock_model_runs_plain(self) -> None:
        import asyncio

        mock_gateway = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "plain response from mock"
        mock_gateway.call_model.return_value = mock_response

        agent = LangGraphAgentLoop(
            model_gateway=mock_gateway,
            chat_model=None,
            mcp_client=None,
        )

        class FakeJob:
            job_id = "test-job-1"
            model_profile = "default"
            work_type = "code_review"
            project_id = "proj-1"

        job = FakeJob()

        result = asyncio.run(
            agent.run_with_tools(
                job=job,
                system_prompt="You are a helpful assistant.",
                user_prompt="What is 2+2?",
            )
        )
        assert "plain response from mock" in result

    def test_agent_loop_without_mcp_runs_plain_path(self) -> None:
        import asyncio

        mock_gateway = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "no-tools response"
        mock_gateway.call_model.return_value = mock_response

        agent = LangGraphAgentLoop(
            model_gateway=mock_gateway,
            chat_model=None,
            mcp_client=None,
        )

        class FakeJob:
            job_id = "test-job-2"
            model_profile = "default"
            work_type = "bug_fix"
            project_id = "proj-2"

        job = FakeJob()

        result = asyncio.run(
            agent.run_with_tools(
                job=job,
                system_prompt="System prompt here.",
                user_prompt="Fix the test.",
            )
        )
        assert "no-tools response" in result


class TestPlaybookRoutingLangGraph:
    """Playbook routing for langgraph_generate / langgraph_decide feature."""

    def test_langchain_generate_playbook_registered(self) -> None:
        from pathlib import Path

        import yaml

        ROOT = Path(__file__).resolve().parents[2]
        playbook_path = ROOT / "playbooks" / "langchain_generate.yml"
        content = playbook_path.read_text()
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, list)
        assert len(parsed) > 0

    def test_langgraph_decide_playbook_registered(self) -> None:
        from pathlib import Path

        import yaml

        ROOT = Path(__file__).resolve().parents[2]
        playbook_path = ROOT / "playbooks" / "langgraph_decide.yml"
        content = playbook_path.read_text()
        parsed = yaml.safe_load(content)
        assert isinstance(parsed, list)
        assert len(parsed) > 0

    def test_langgraph_decision_role_exists(self) -> None:
        from pathlib import Path

        ROOT = Path(__file__).resolve().parents[2]
        role_dir = (
            ROOT / "collections" / "ansible_collections" / "general_ludd"
            / "agent" / "roles" / "langgraph_decision"
        )
        assert (role_dir / "tasks" / "main.yml").is_file()


class TestLangChainModelRouterRunnableBranch:
    """LangChainModelRouter with RunnableBranch — add routes + resolve."""

    def test_router_creates_empty(self) -> None:
        router = LangChainModelRouter()
        assert router is not None

    def test_router_add_route_and_resolve(self) -> None:
        router = LangChainModelRouter()
        router.add_route(
            condition_fn=lambda d: d.get("kind") == "code_review",
            model_runnable="sonnet",
        )
        router.add_route(
            condition_fn=lambda d: d.get("kind") == "bug_fix",
            model_runnable="haiku",
        )
        result = router.resolve({"kind": "code_review"})
        assert result == "sonnet"

    def test_router_resolve_returns_default_when_no_match(self) -> None:
        router = LangChainModelRouter()
        router.add_route(
            condition_fn=lambda d: d.get("kind") == "code_review",
            model_runnable="sonnet",
        )
        router.set_default("default_model")
        result = router.resolve({"kind": "unknown_kind"})
        assert result == "default_model"

    def test_router_resolve_returns_none_without_default(self) -> None:
        router = LangChainModelRouter()
        router.add_route(
            condition_fn=lambda d: d.get("kind") == "code_review",
            model_runnable="sonnet",
        )
        result = router.resolve({"kind": "unknown_kind"})
        assert result is None

    def test_router_first_matching_condition_wins(self) -> None:
        router = LangChainModelRouter()
        router.add_route(
            condition_fn=lambda d: True,
            model_runnable="first_match",
        )
        router.add_route(
            condition_fn=lambda d: True,
            model_runnable="second_match",
        )
        result = router.resolve({"kind": "anything"})
        assert result == "first_match"

    def test_router_builds_runnable_branch(self) -> None:
        from langchain_core.runnables import RunnableBranch

        router = LangChainModelRouter()
        router.add_route(
            condition_fn=lambda d: d.get("priority") == "high",
            model_runnable="opus",
        )
        router.set_default("sonnet")
        branch = router._build_branches()
        assert isinstance(branch, RunnableBranch)

        result_high = branch.invoke({"priority": "high"})
        assert result_high == "opus"

        result_low = branch.invoke({"priority": "low"})
        assert result_low == "sonnet"


class TestCheckpointerSaveLoadCycle:
    """TickCheckpointer save/load cycle — ephemeral dict backend."""

    def test_put_and_get_with_ephemeral_backend(self) -> None:
        checkpointer = TickCheckpointer(saver=None)
        state = {"phase": "fetch_todos", "todo_count": 5, "project": "main"}
        checkpointer.put("tick-001", state)
        retrieved = checkpointer.get("tick-001")
        assert retrieved is not None
        assert retrieved["phase"] == "fetch_todos"

    def test_get_returns_none_for_unknown_tick(self) -> None:
        checkpointer = TickCheckpointer(saver=None)
        result = checkpointer.get("nonexistent-tick")
        assert result is None

    def test_list_returns_saved_states(self) -> None:
        checkpointer = TickCheckpointer(saver=None)
        checkpointer.put("tick-002", {"phase": "dispatch", "count": 3})
        entries = checkpointer.list("tick-002")
        assert len(entries) >= 1
        assert entries[0]["phase"] == "dispatch"

    def test_delete_thread_removes_state(self) -> None:
        checkpointer = TickCheckpointer(saver=None)
        checkpointer.put("tick-003", {"phase": "review"})
        assert checkpointer.get("tick-003") is not None
        checkpointer.delete_thread("tick-003")
        assert checkpointer.get("tick-003") is None

    def test_prune_removes_stale_entries(self) -> None:
        import time

        checkpointer = TickCheckpointer(saver=None)
        checkpointer.put("tick-004", {"phase": "old"})
        checkpointer._tick_timestamps["tick-004"] = time.time() - 100_000
        pruned = checkpointer.prune(max_age_hours=24.0)
        assert pruned >= 1
        assert checkpointer.get("tick-004") is None

    def test_prune_clears_ephemeral_store(self) -> None:
        checkpointer = TickCheckpointer(saver=None)
        checkpointer.put("tick-005", {"phase": "recent"})
        assert checkpointer.get("tick-005") is not None
        checkpointer.prune(max_age_hours=24.0)
        # Current behaviour: prune clears the ephemeral dict entirely
        assert checkpointer.get("tick-005") is None

    def test_get_checkpointer_factory_returns_tick_checkpointer(self) -> None:
        cp = get_checkpointer(db_url=None)
        assert isinstance(cp, TickCheckpointer)

    def test_factory_uses_inmemory_saver_when_langgraph_available(self) -> None:
        cp = get_checkpointer(db_url=None)
        assert cp.available is True


class TestLangChainRetryGatewayExists:
    """LangChainRetryGateway is importable and has expected shape."""

    def test_can_import_langchain_retry(self) -> None:
        from general_ludd.models.langchain_retry import LangChainRetryGateway

        assert LangChainRetryGateway is not None

    def test_langchain_retry_gateway_creates_instance(self) -> None:
        from general_ludd.models.langchain_retry import LangChainRetryGateway

        mock_gateway = MagicMock()
        gw = LangChainRetryGateway(gateway=mock_gateway)
        assert gw is not None
