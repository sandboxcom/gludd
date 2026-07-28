"""Tests for sandbox enforcement on tool execution (A-TOOLEXEC-UNSANDBOXED).

Verifies that all tool execution paths go through the sandbox enforcement
layer with path confinement, network restrictions, and resource limits.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import ClassVar
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from general_ludd.sandbox.enforcer import (
    MaxOutputExceededError,
    PathEscapeError,
    SandboxConfig,
    SandboxEnforcer,
    SandboxNotAvailableError,
)
from general_ludd.sandbox.process_executor import ProcessLimits


class TestSandboxEnforcerPathConfinement:
    """Path confinement: no file access outside the jail directory."""

    def test_confine_path_within_jail_returns_resolved_path(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        result = enforcer.confine_path(str(jail / "file.txt"))
        assert result == os.path.realpath(str(jail / "file.txt"))

    def test_confine_path_outside_jail_raises(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError, match="escapes sandbox jail"):
            enforcer.confine_path(str(outside / "secret.txt"))

    def test_confine_path_parent_traversal_blocked(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError, match="escapes sandbox jail"):
            enforcer.confine_path(str(jail / ".." / ".." / "etc" / "passwd"))

    def test_confine_path_symlink_escape_blocked(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        (tmp_path / "outside").mkdir()
        symlink = jail / "link"
        symlink.symlink_to(tmp_path / "outside")
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError, match="escapes sandbox jail"):
            enforcer.confine_path(str(symlink))

    def test_confine_path_null_byte_rejected(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError):
            enforcer.confine_path(str(jail / "file.txt\x00extra"))

    def test_confine_path_unverified_raises_when_fail_closed(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(tmp_path)))
        with pytest.raises(SandboxNotAvailableError, match="not verified"):
            enforcer.confine_path(str(tmp_path / "x"))

    def test_confine_path_unverified_passes_when_fail_open(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), fail_open=True,
        ))
        result = enforcer.confine_path(str(tmp_path / "x"))
        assert result == str(tmp_path / "x")


class TestSandboxEnforcerResourceLimits:
    """Resource limits enforcement via ProcessLimits."""

    def test_default_limits_are_applied(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path),
            memory_mb=256,
            cpu_seconds=120,
            max_processes=30,
        ))
        enforcer.verify_ready()
        limits = enforcer._limits
        assert limits.memory_mb == 256
        assert limits.cpu_seconds == 120
        assert limits.max_processes == 30

    def test_custom_limits_config(self, tmp_path: Path) -> None:
        config = SandboxConfig(
            jail_dir=str(tmp_path),
            memory_mb=1024,
            cpu_seconds=600,
            max_processes=100,
            timeout=900,
        )
        enforcer = SandboxEnforcer(config)
        enforcer.verify_ready()
        assert enforcer._limits.memory_mb == 1024
        assert enforcer._limits.cpu_seconds == 600
        assert enforcer._limits.max_processes == 100
        assert enforcer._executor.timeout == 900


class TestSandboxEnforcerNetworkRestrictions:
    """Network restrictions configuration and behavior."""

    def test_default_config_denies_network(self) -> None:
        config = SandboxConfig()
        assert config.allow_network is False

    def test_allow_network_config(self) -> None:
        config = SandboxConfig(allow_network=True, allowed_hosts=["127.0.0.1"])
        assert config.allow_network is True
        assert "127.0.0.1" in config.allowed_hosts

    def test_network_isolation_preexec_noop_on_windows(self) -> None:
        with patch("os.name", "nt"):
            SandboxEnforcer._isolate_network()

    def test_network_isolation_preexec_posix(self) -> None:
        with (
            patch("os.name", "posix"),
            patch.dict(os.environ, {"GLUDD_SANDBOX_NO_NETWORK": "1"}),
        ):
            SandboxEnforcer._isolate_network()


class TestSandboxEnforcerFailClosed:
    """Fail-closed behavior: sandbox must be verified before execution."""

    def test_execute_unverified_raises(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(tmp_path)))
        with pytest.raises(SandboxNotAvailableError, match="not verified"):
            enforcer.execute("echo hello")

    def test_execute_unverified_warns_fail_open(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), fail_open=True,
        ))
        result = enforcer.execute("echo hello")
        assert result.returncode == 0
        assert "hello" in result.stdout

    def test_verify_ready_nonexistent_dir_faills(self, tmp_path: Path) -> None:
        nonexistent = str(tmp_path / "does-not-exist")
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=nonexistent))
        with pytest.raises(SandboxNotAvailableError, match="Sandbox jail"):
            enforcer.verify_ready()

    def test_verify_ready_nonexistent_dir_warns_fail_open(self, tmp_path: Path) -> None:
        nonexistent = str(tmp_path / "does-not-exist")
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=nonexistent, fail_open=True,
        ))
        enforcer.verify_ready()
        assert enforcer.is_ready


class TestSandboxEnforcerMaxOutput:
    """Maximum output enforcement."""

    def test_max_output_bytes_default(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(tmp_path)))
        enforcer.verify_ready()
        result = enforcer.execute("echo hello")
        assert result.returncode == 0

    def test_max_output_bytes_enforced_fail_closed(self, tmp_path: Path) -> None:
        long_str = "x" * 2000
        subprocess.CompletedProcess(
            args="echo", returncode=0, stdout=long_str, stderr="",
        )
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), max_output_bytes=100,
        ))
        enforcer.verify_ready()
        with (
            patch.object(subprocess, "Popen") as mock_popen,
        ):
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = (long_str, "")
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            with pytest.raises(MaxOutputExceededError, match="exceeds max"):
                enforcer.execute("printf '%s' " + "'" + long_str + "'")

    def test_max_output_enforced_fail_open(self, tmp_path: Path) -> None:
        long_str = "x" * 2000
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path), max_output_bytes=100, fail_open=True,
        ))
        enforcer.verify_ready()
        with (
            patch.object(subprocess, "Popen") as mock_popen,
        ):
            mock_proc = MagicMock()
            mock_proc.communicate.return_value = (long_str, "")
            mock_proc.returncode = 0
            mock_proc.pid = 12345
            mock_popen.return_value = mock_proc
            result = enforcer.execute("echo x")
            assert result.returncode == 0


class TestSandboxEnforcerAutoJail:
    """Auto-creation of jail directory when none is specified."""

    def test_verify_ready_auto_creates_jail(self) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=""))
        enforcer.verify_ready()
        assert enforcer.jail_dir
        assert os.path.isdir(enforcer.jail_dir)
        enforcer._jail_path.rmdir() if enforcer._jail_path else None


class TestSandboxEnforcerExecute:
    """End-to-end command execution through sandbox."""

    def test_execute_simple_command(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(tmp_path)))
        enforcer.verify_ready()
        result = enforcer.execute("echo sandboxed")
        assert result.returncode == 0
        assert "sandboxed" in result.stdout

    def test_execute_with_workdir_in_jail(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        subdir = jail / "sub"
        subdir.mkdir()
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        result = enforcer.execute("pwd", workdir=str(subdir))
        assert result.returncode == 0
        assert str(subdir) in result.stdout.strip()

    def test_execute_workdir_outside_jail_raises(self, tmp_path: Path) -> None:
        jail = tmp_path / "jail"
        jail.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()
        with pytest.raises(PathEscapeError, match="escapes sandbox jail"):
            enforcer.execute("pwd", workdir=str(outside))

    def test_execute_nonzero_exit(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(tmp_path)))
        enforcer.verify_ready()
        result = enforcer.execute("false")
        assert result.returncode != 0

    def test_execute_limits_applied_in_child(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(
            jail_dir=str(tmp_path),
            memory_mb=100,
        ))
        enforcer.verify_ready()
        with patch.object(
            enforcer._executor.__class__, "_apply_limits"
        ) as mock_limits:
            enforcer._apply_sandbox_preexec(enforcer._limits)
            mock_limits.assert_called_once_with(enforcer._limits)


class TestSandboxEnforcerConfig:
    """Configuration defaults and validation."""

    def test_default_config_values(self) -> None:
        config = SandboxConfig()
        assert config.memory_mb == 512
        assert config.cpu_seconds == 300
        assert config.max_output_bytes == 1_000_000
        assert config.max_processes == 50
        assert config.timeout == 300
        assert config.allow_network is False
        assert config.fail_open is False

    def test_config_jail_dir_defaults_empty(self) -> None:
        config = SandboxConfig()
        assert config.jail_dir == ""


class TestSandboxEnforcerErrorTypes:
    """Custom error types are properly structured."""

    def test_sandbox_not_available_error(self) -> None:
        err = SandboxNotAvailableError("test message")
        assert isinstance(err, RuntimeError)
        assert str(err) == "test message"

    def test_path_escape_error(self) -> None:
        err = PathEscapeError("path escape")
        assert isinstance(err, ValueError)
        assert str(err) == "path escape"

    def test_max_output_exceeded_error(self) -> None:
        err = MaxOutputExceededError("too much output")
        assert isinstance(err, RuntimeError)
        assert str(err) == "too much output"


class TestSandboxEnforcerVerifyReady:
    """verify_ready() state transitions."""

    def test_is_ready_false_before_verify(self) -> None:
        enforcer = SandboxEnforcer(SandboxConfig())
        assert not enforcer.is_ready

    def test_is_ready_true_after_verify(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(tmp_path)))
        enforcer.verify_ready()
        assert enforcer.is_ready

    def test_double_verify_is_noop(self, tmp_path: Path) -> None:
        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(tmp_path)))
        enforcer.verify_ready()
        enforcer.verify_ready()
        assert enforcer.is_ready


class TestProcessLimitsIntegration:
    """ProcessLimits dataclass used by SandboxEnforcer."""

    def test_limits_accept_all_fields(self) -> None:
        limits = ProcessLimits(
            memory_mb=512,
            cpu_seconds=300,
            max_file_size=1000,
            max_open_files=256,
            max_processes=50,
        )
        assert limits.memory_mb == 512
        assert limits.cpu_seconds == 300
        assert limits.max_file_size == 1000
        assert limits.max_open_files == 256
        assert limits.max_processes == 50

    def test_limits_default_none(self) -> None:
        limits = ProcessLimits()
        assert limits.memory_mb is None
        assert limits.cpu_seconds is None


class TestLangGraphAgentSandboxIntegration:
    """SandboxEnforcer integration into LangGraphAgentLoop."""

    def test_sandbox_enforcer_stored_on_agent(self) -> None:
        from general_ludd.execution.langgraph_agent import LangGraphAgentLoop

        enforcer = SandboxEnforcer(SandboxConfig(fail_open=True))
        agent = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=MagicMock(),
            sandbox_enforcer=enforcer,
        )
        assert agent._sandbox_enforcer is enforcer

    def test_sandbox_enforcer_defaults_to_none(self) -> None:
        from general_ludd.execution.langgraph_agent import LangGraphAgentLoop

        agent = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=MagicMock(),
        )
        assert agent._sandbox_enforcer is None

    @pytest.mark.asyncio
    async def test_tool_execution_blocked_by_unverified_sandbox(self, tmp_path: Path) -> None:
        from general_ludd.execution.langgraph_agent import LangGraphAgentLoop

        mcp_client = MagicMock()
        mcp_client.list_tools = AsyncMock()

        class FakeTool:
            name = "read_file"
            description = "Read a file"
            input_schema: ClassVar[dict[str, object]] = {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            }

        mcp_client.list_tools.return_value = [FakeTool()]
        mcp_client.call_tool = AsyncMock()

        registry = MagicMock()
        registry.get_tool.return_value = MagicMock(server_id="srv-1")

        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(tmp_path / "does-not-exist")))
        agent = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp_client,
            mcp_registry=registry,
            sandbox_enforcer=enforcer,
        )

        tools = await agent._build_langchain_tools()
        assert len(tools) == 1

        result = await tools[0].ainvoke({"path": "/etc/passwd"})
        assert "Tool error: sandbox not available" in result

    @pytest.mark.asyncio
    async def test_tool_execution_path_confined_by_sandbox(self, tmp_path: Path) -> None:
        from general_ludd.execution.langgraph_agent import LangGraphAgentLoop

        jail = tmp_path / "jail"
        jail.mkdir()
        (jail / "allowed.txt").write_text("hello")

        mcp_client = MagicMock()
        mcp_client.list_tools = AsyncMock()

        class FakeTool:
            name = "read_file"
            description = "Read a file"
            input_schema: ClassVar[dict[str, object]] = {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            }

        mcp_client.list_tools.return_value = [FakeTool()]
        mcp_client.call_tool = AsyncMock()
        mcp_client.call_tool.return_value = "hello world"

        registry = MagicMock()
        registry.get_tool.return_value = MagicMock(server_id="srv-1")

        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()

        agent = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp_client,
            mcp_registry=registry,
            sandbox_enforcer=enforcer,
        )

        tools = await agent._build_langchain_tools()
        result = await tools[0].ainvoke({"path": str(jail / "allowed.txt")})
        assert "hello world" in result

    @pytest.mark.asyncio
    async def test_tool_execution_path_escape_blocked_by_sandbox(
        self, tmp_path: Path,
    ) -> None:
        from general_ludd.execution.langgraph_agent import LangGraphAgentLoop

        jail = tmp_path / "jail"
        jail.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()

        mcp_client = MagicMock()
        mcp_client.list_tools = AsyncMock()

        class FakeTool:
            name = "read_file"
            description = "Read a file"
            input_schema: ClassVar[dict[str, object]] = {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            }

        mcp_client.list_tools.return_value = [FakeTool()]
        mcp_client.call_tool = AsyncMock()

        registry = MagicMock()
        registry.get_tool.return_value = MagicMock(server_id="srv-1")

        enforcer = SandboxEnforcer(SandboxConfig(jail_dir=str(jail)))
        enforcer.verify_ready()

        agent = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp_client,
            mcp_registry=registry,
            sandbox_enforcer=enforcer,
        )

        tools = await agent._build_langchain_tools()
        result = await tools[0].ainvoke({"path": str(outside / "secret.txt")})
        assert "escapes sandbox" in result

    @pytest.mark.asyncio
    async def test_tool_execution_without_enforcer_proceeds_normally(self) -> None:
        from general_ludd.execution.langgraph_agent import LangGraphAgentLoop

        mcp_client = MagicMock()
        mcp_client.list_tools = AsyncMock()

        class FakeTool:
            name = "echo"
            description = "Echo a message"

        mcp_client.list_tools.return_value = [FakeTool()]
        mcp_client.call_tool = AsyncMock()
        mcp_client.call_tool.return_value = "echoed"

        registry = MagicMock()
        registry.get_tool.return_value = MagicMock(server_id="srv-1")

        agent = LangGraphAgentLoop(
            model_gateway=MagicMock(),
            mcp_client=mcp_client,
            mcp_registry=registry,
            sandbox_enforcer=None,
        )

        tools = await agent._build_langchain_tools()
        result = await tools[0].ainvoke({"message": "hello"})
        assert "echoed" in result


class TestSandboxEnforcerNetworkPolicy:
    """SandboxEnforcer works with the existing NetworkPolicy."""

    def test_isolated_config_blocks_network(self) -> None:
        from general_ludd.sandbox.network_policy import NetworkPolicy

        policy = NetworkPolicy.fully_isolated()
        assert policy.is_isolated()

    def test_localhost_config_allows_local(self) -> None:
        from general_ludd.sandbox.network_policy import NetworkPolicy

        policy = NetworkPolicy.allow_localhost()
        assert "127.0.0.1" in policy.allowed_hosts
        assert policy.allow_outbound is True


class TestSandboxEnforcerSecurityPolicy:
    """SandboxEnforcer works with the existing SecurityPolicy."""

    def test_minimal_policy_is_restrictive(self) -> None:
        from general_ludd.sandbox.security_policy import SecurityPolicy

        policy = SecurityPolicy.minimal()
        assert policy.is_restrictive()

    def test_default_docker_policy(self) -> None:
        from general_ludd.sandbox.security_policy import SecurityPolicy

        policy = SecurityPolicy.default_docker()
        assert policy.read_only_root is True
        assert policy.privileged is False
        assert policy.no_new_privileges is True
