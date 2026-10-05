"""Behavioral coverage for policy-aware LangGraph MCP tool execution."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import general_ludd.execution._langgraph_tool_executor as subject


class SandboxUnavailableError(RuntimeError):
    """Test-only error matching the executor's configured sandbox failure type."""


async def _await_result(awaitable: object, *, timeout: float) -> object:
    del timeout
    return await awaitable  # type: ignore[misc]


def _executor(
    *,
    client: Any | None = None,
    auditor: Any = None,
    sandbox_enforcer: Any = None,
    wait_for: Any = _await_result,
) -> tuple[Any, Any]:
    bound_client = client or SimpleNamespace(
        call_tool=AsyncMock(return_value={"ok": True})
    )
    return (
        subject.make_mcp_executor(
            tool_name="write_file",
            server_id="filesystem",
            client=bound_client,
            timeout=2.5,
            auditor=auditor,
            sandbox_enforcer=sandbox_enforcer,
            sandbox_error=SandboxUnavailableError,
            wait_for=wait_for,
        ),
        bound_client,
    )


@pytest.mark.asyncio
async def test_success_confines_paths_audits_and_records_result() -> None:
    """Successful calls apply path policy before audit and preserve the tool result."""
    events: list[str] = []

    def note_audit(*_args: object, **_kwargs: object) -> None:
        events.append("audit")

    def note_success(*_args: object) -> None:
        events.append("success")

    sandbox = SimpleNamespace(
        verify_ready=MagicMock(side_effect=lambda: events.append("ready")),
        confine_path=MagicMock(side_effect=lambda _path: events.append("confine")),
    )
    auditor = SimpleNamespace(
        audit=MagicMock(side_effect=note_audit),
        record_success=MagicMock(side_effect=note_success),
        record_error=MagicMock(),
    )
    execute, client = _executor(sandbox_enforcer=sandbox, auditor=auditor)
    payload = {"path": "/workspace/result.txt", "text": "hello"}

    result = await execute(payload)

    assert result == "{'ok': True}"
    assert events == ["ready", "confine", "audit", "success"]
    sandbox.confine_path.assert_called_once_with("/workspace/result.txt")
    client.call_tool.assert_awaited_once_with("filesystem", "write_file", payload)


@pytest.mark.asyncio
async def test_sandbox_readiness_and_path_escape_fail_closed() -> None:
    """Neither an unavailable sandbox nor an escaping path reaches the MCP client."""
    unavailable = SimpleNamespace(
        verify_ready=MagicMock(side_effect=SandboxUnavailableError("not mounted")),
        confine_path=MagicMock(),
    )
    execute, client = _executor(sandbox_enforcer=unavailable)
    message = await execute(path="/workspace/file")
    assert "sandbox not available" in message
    client.call_tool.assert_not_awaited()

    escaping = SimpleNamespace(
        verify_ready=MagicMock(),
        confine_path=MagicMock(side_effect=ValueError("outside root")),
    )
    execute, client = _executor(sandbox_enforcer=escaping)
    message = await execute(file_path="/etc/passwd")
    assert "escapes sandbox" in message
    client.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_auditor_denial_returns_actionable_non_retry_error() -> None:
    """A policy denial is final and includes its classification and reason."""
    auditor = SimpleNamespace(
        audit=MagicMock(
            return_value=SimpleNamespace(
                allowed=False,
                classification="credential_access",
                reason="secret path",
            )
        ),
        record_success=MagicMock(),
        record_error=MagicMock(),
    )
    execute, client = _executor(auditor=auditor)

    message = await execute(path="token.txt")

    assert "credential_access" in message
    assert "secret path" in message
    assert "Do not retry" in message
    client.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_timeout_records_specific_audit_failure() -> None:
    """Timeouts retain the configured duration in both audit and user output."""
    auditor = SimpleNamespace(
        audit=MagicMock(return_value=None),
        record_success=MagicMock(),
        record_error=MagicMock(),
    )

    async def timeout_wait(awaitable: Any, *, timeout: float) -> object:
        del timeout
        awaitable.close()
        raise TimeoutError

    execute, _client = _executor(auditor=auditor, wait_for=timeout_wait)

    message = await execute(query="hello")

    assert message == "Tool error: 'write_file' timed out after 2.5s"
    auditor.record_error.assert_called_once_with(
        "write_file", {"query": "hello"}, "timeout after 2.5s"
    )


@pytest.mark.asyncio
async def test_client_error_is_recorded_and_no_input_defaults_to_empty_mapping() -> None:
    """Ordinary client failures are reported, while empty calls send an empty payload."""
    client = SimpleNamespace(call_tool=AsyncMock(side_effect=RuntimeError("offline")))
    auditor = SimpleNamespace(
        audit=MagicMock(return_value=None),
        record_success=MagicMock(),
        record_error=MagicMock(),
    )
    execute, _ = _executor(client=client, auditor=auditor)

    message = await execute()

    assert message == "Tool error: offline"
    client.call_tool.assert_awaited_once_with("filesystem", "write_file", {})
    auditor.record_error.assert_called_once_with("write_file", {}, "offline")
