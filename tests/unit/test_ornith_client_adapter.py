"""Tests for general_ludd.ornith.client_adapter.OrnithMCPClientAdapter.

Mocks MCPStdioClient so no real subprocess is spawned.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.mcp.config import MCPServerConfig

# -- Fixtures ---------------------------------------------------------------


@pytest.fixture
def mock_transport():
    """Mock MCPStdioClient with async call_tool / read_resource."""
    transport = MagicMock()
    transport.start = AsyncMock()
    transport.stop = AsyncMock()
    transport.call_tool = AsyncMock(return_value={
        "content": [{"type": "text", "text": '{"patch": "diff --git a/x b/x"}', "iterations_used": 3}],
    })
    transport.list_resources = AsyncMock(return_value=[
        {"name": "ornith_status", "mimeType": "application/json"},
        {"name": "ornith_model_info", "mimeType": "application/json"},
    ])
    transport.read_resource = AsyncMock(return_value={
        "contents": [{"uri": "ornith://ornith_status", "mimeType": "application/json", "text": '{"installed": true}'}],
    })
    return transport


@pytest.fixture
def adapter(mock_transport):
    from general_ludd.ornith.client_adapter import OrnithMCPClientAdapter
    a = OrnithMCPClientAdapter(ornith_binary="ornith", enabled=True)
    # Inject mock transport so no subprocess is started.
    tr = mock_transport
    # Need start to use our mock instead of creating a real MCPStdioClient.
    a._transport = tr
    a._config = a.build_config()
    a._enabled = True
    return a


@pytest.fixture
def disabled_adapter():
    from general_ludd.ornith.client_adapter import OrnithMCPClientAdapter
    return OrnithMCPClientAdapter(enabled=False)


# -- build_config tests ----------------------------------------------------


class TestBuildConfig:
    def test_returns_mcp_server_config_for_enabled(self):
        from general_ludd.ornith.client_adapter import OrnithMCPClientAdapter
        a = OrnithMCPClientAdapter(ornith_binary="/usr/bin/ornith", enabled=True)
        config = a.build_config()
        assert isinstance(config, MCPServerConfig)
        assert config.server_id == "ornith"
        assert config.command == ["/usr/bin/ornith", "--json"]
        assert config.timeout_seconds == 600
        assert config.enabled is True

    def test_returns_mcp_server_config_for_disabled(self):
        from general_ludd.ornith.client_adapter import OrnithMCPClientAdapter
        a = OrnithMCPClientAdapter(enabled=False)
        config = a.build_config()
        assert config.enabled is False
        assert config.server_id == "ornith"


# -- start / stop tests ----------------------------------------------------


class TestStartStop:
    @pytest.mark.asyncio
    async def test_start_noop_when_disabled(self, disabled_adapter):
        await disabled_adapter.start()
        assert disabled_adapter.transport is None

    @pytest.mark.asyncio
    async def test_start_creates_transport_when_enabled(self):
        from general_ludd.ornith.client_adapter import OrnithMCPClientAdapter
        a = OrnithMCPClientAdapter(ornith_binary="ornith", enabled=True)

        with patch(
            "general_ludd.ornith.client_adapter.MCPStdioClient",
            autospec=True,
        ) as MockStdio:
            transport_instance = MockStdio.return_value
            transport_instance.start = AsyncMock()

            await a.start()
            MockStdio.assert_called_once_with(a._config)
            transport_instance.start.assert_awaited_once()
            assert a._transport is transport_instance

    @pytest.mark.asyncio
    async def test_stop_terminates_transport(self, adapter, mock_transport):
        await adapter.stop()
        mock_transport.stop.assert_awaited_once()
        assert adapter.transport is None

    @pytest.mark.asyncio
    async def test_stop_noop_when_no_transport(self, disabled_adapter):
        await disabled_adapter.stop()


# -- solve() tests ---------------------------------------------------------


class TestSolve:
    @pytest.mark.asyncio
    async def test_calls_ornith_solve_tool(self, adapter, mock_transport):
        result = await adapter.solve(
            task_description="fix the bug",
            repo_context_path="/workspace/repo",
            max_iterations=15,
            target_files=["src/main.py"],
        )
        mock_transport.call_tool.assert_awaited_once_with(
            "ornith_solve",
            {
                "task_description": "fix the bug",
                "repo_context_path": "/workspace/repo",
                "max_iterations": 15,
                "target_files": ["src/main.py"],
            },
        )
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_solve_defaults(self, adapter, mock_transport):
        await adapter.solve(task_description="t", repo_context_path="/r")
        mock_transport.call_tool.assert_awaited_once()
        call_args = mock_transport.call_tool.await_args
        assert call_args is not None
        _, arguments = call_args.args
        assert arguments["max_iterations"] == 10
        assert arguments["target_files"] == []

    @pytest.mark.asyncio
    async def test_solve_raises_when_not_started(self):
        from general_ludd.ornith.client_adapter import OrnithMCPClientAdapter
        a = OrnithMCPClientAdapter(enabled=True)
        with pytest.raises(RuntimeError, match="not started"):
            await a.solve(task_description="x", repo_context_path="/r")


# -- improve() tests -------------------------------------------------------


class TestImprove:
    @pytest.mark.asyncio
    async def test_calls_ornith_improve_tool(self, adapter, mock_transport):
        result = await adapter.improve(
            target_artifact_path="/repo/playbook.yml",
            feedback_yaml="severity: high",
            artifact_kind="playbook",
        )
        mock_transport.call_tool.assert_awaited_once_with(
            "ornith_improve",
            {
                "target_artifact_path": "/repo/playbook.yml",
                "feedback_yaml": "severity: high",
                "artifact_kind": "playbook",
            },
        )
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_improve_raises_when_not_started(self):
        from general_ludd.ornith.client_adapter import OrnithMCPClientAdapter
        a = OrnithMCPClientAdapter(enabled=True)
        with pytest.raises(RuntimeError, match="not started"):
            await a.improve(
                target_artifact_path="/x",
                feedback_yaml="s: low",
                artifact_kind="playbook",
            )


# -- list_resources tests --------------------------------------------------


class TestListResources:
    @pytest.mark.asyncio
    async def test_lists_resources(self, adapter, mock_transport):
        resources = await adapter.list_resources()
        mock_transport.list_resources.assert_awaited_once()
        assert len(resources) == 2
        names = [r["name"] for r in resources]
        assert "ornith_status" in names
        assert "ornith_model_info" in names

    @pytest.mark.asyncio
    async def test_raises_when_not_started(self):
        from general_ludd.ornith.client_adapter import OrnithMCPClientAdapter
        a = OrnithMCPClientAdapter(enabled=True)
        with pytest.raises(RuntimeError, match="not started"):
            await a.list_resources()


# -- read_resource tests ---------------------------------------------------


class TestReadResource:
    @pytest.mark.asyncio
    async def test_reads_resource_by_uri(self, adapter, mock_transport):
        result = await adapter.read_resource("ornith://ornith_status")
        mock_transport.read_resource.assert_awaited_once_with(
            "ornith://ornith_status"
        )
        assert "contents" in result

    @pytest.mark.asyncio
    async def test_read_model_info(self, adapter, mock_transport):
        mock_transport.read_resource = AsyncMock(return_value={
            "contents": [{
                "uri": "ornith://ornith_model_info",
                "mimeType": "application/json",
                "text": '{"model_sha": "abc123", "capabilities": ["code_gen"]}',
            }],
        })
        result = await adapter.read_resource("ornith://ornith_model_info")
        assert result["contents"][0]["uri"] == "ornith://ornith_model_info"

    @pytest.mark.asyncio
    async def test_raises_when_not_started(self):
        from general_ludd.ornith.client_adapter import OrnithMCPClientAdapter
        a = OrnithMCPClientAdapter(enabled=True)
        with pytest.raises(RuntimeError, match="not started"):
            await a.read_resource("ornith://ornith_status")


# -- config property -------------------------------------------------------


class TestConfigProperty:
    def test_config_set_after_start(self, adapter):
        assert adapter.config is not None
        assert adapter.config.server_id == "ornith"

    def test_config_none_if_disabled(self):
        from general_ludd.ornith.client_adapter import OrnithMCPClientAdapter
        a = OrnithMCPClientAdapter(enabled=False)
        assert a.config is None

    def test_config_none_if_not_started(self):
        from general_ludd.ornith.client_adapter import OrnithMCPClientAdapter
        a = OrnithMCPClientAdapter(enabled=True)
        assert a.config is None
