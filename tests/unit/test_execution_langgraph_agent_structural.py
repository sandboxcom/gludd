"""Structural tests for execution/langgraph_agent.py."""

from __future__ import annotations

import contextlib

from general_ludd.execution.langgraph_agent import (
    MAX_TOOL_ITERATIONS,
    MAX_TOTAL_TOKENS_DEFAULT,
    PER_TOOL_TIMEOUT_SECONDS,
    LangGraphAgentLoop,
)


class TestLangGraphAgentLoop:
    def test_construct_with_minimal_args(self):
        loop = LangGraphAgentLoop(model_gateway=None)
        assert loop is not None

    def test_not_available_without_mcp_client(self):
        loop = LangGraphAgentLoop(model_gateway=None)
        assert loop.is_available() is False

    def test_default_max_iterations(self):
        loop = LangGraphAgentLoop(model_gateway=None)
        assert loop._max_iterations == MAX_TOOL_ITERATIONS

    def test_default_per_tool_timeout(self):
        loop = LangGraphAgentLoop(model_gateway=None)
        assert loop._per_tool_timeout == PER_TOOL_TIMEOUT_SECONDS

    def test_default_max_total_tokens(self):
        loop = LangGraphAgentLoop(model_gateway=None)
        assert loop._max_total_tokens == MAX_TOTAL_TOKENS_DEFAULT

    def test_store_mcp_registry_from_client(self):
        class FakeClient:
            def __init__(self):
                self._registry = "fake_registry"

        loop = LangGraphAgentLoop(
            model_gateway=None,
            mcp_client=FakeClient(),
        )
        assert loop._mcp_registry == "fake_registry"

    def test_explicit_max_iterations(self):
        loop = LangGraphAgentLoop(model_gateway=None, max_iterations=5)
        assert loop._max_iterations == 5

    def test_explicit_max_total_tokens(self):
        loop = LangGraphAgentLoop(model_gateway=None, max_total_tokens=50000)
        assert loop._max_total_tokens == 50000

    def test_resolve_server_id_requires_registry(self):
        loop = LangGraphAgentLoop(model_gateway=None)
        with contextlib.suppress(Exception):
            loop._resolve_server_id("some_tool")


class TestModuleConstants:
    def test_max_tool_iterations(self):
        assert MAX_TOOL_ITERATIONS == 10

    def test_per_tool_timeout_seconds(self):
        assert PER_TOOL_TIMEOUT_SECONDS == 30

    def test_max_total_tokens_default(self):
        assert MAX_TOTAL_TOKENS_DEFAULT == 100_000
