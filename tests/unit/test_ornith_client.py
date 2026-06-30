"""Tests for general_ludd.ornith.client.OrnithClient.

TDD: these tests are written FIRST and must FAIL until the implementation
exists. They mock everything (transport, sts_registry, audit_recorder,
permission_spec) and never touch a real socket or ornith process.
"""

import pathlib
from unittest.mock import MagicMock

import pytest

# --- Stub classes (no imports from general_ludd to avoid collection errors) ---

class StubPermissionSpec:
    """Mimics PermissionSpec: configurable capability + intersection."""

    def __init__(self, capabilities: dict[tuple[str, str], bool] | None = None,
                 allow_all: bool = True):
        self._capabilities = capabilities or {}
        self._allow_all = allow_all

    def has_capability(self, resource: str, action: str) -> bool:
        if (resource, action) in self._capabilities:
            return self._capabilities[(resource, action)]
        return self._allow_all

    def intersect(self, other):
        # minimal stub: return self for test purposes
        return self


class StubStsRegistry:
    """Mimics sts_registry.mint."""

    def __init__(self, token: str = "sts-token-abc"):
        self._token = token
        self.mint = MagicMock(return_value=self._token)


class StubAuditRecorder:
    """Mimics audit_recorder.record -> returns row id."""

    def __init__(self, row_id: str = "audit-row-1"):
        self._row_id = row_id
        self.record = MagicMock(return_value=self._row_id)


class StubTransport:
    """Mimics the MCP transport layer. call_tool returns the JSON-RPC result."""

    def __init__(self, result: dict | None = None):
        self._default_result = result or {
            "patch": "diff --git a/foo b/foo",
            "summary": "fixed it",
            "iterations_used": 3,
            "tokens_consumed": 1024,
        }
        self.call_tool = MagicMock(return_value=self._default_result)


# --- Fixtures ---

@pytest.fixture
def socket_path():
    return pathlib.Path("/tmp/fake-ornith.sock")


@pytest.fixture
def capable_spec():
    # allow_all=True means has_capability returns True for anything not overridden
    return StubPermissionSpec(allow_all=True)


@pytest.fixture
def sts_registry():
    return StubStsRegistry(token="sts-token-xyz")


@pytest.fixture
def audit_recorder():
    return StubAuditRecorder(row_id="row-42")


@pytest.fixture
def transport():
    return StubTransport()


@pytest.fixture
def client(socket_path, capable_spec, sts_registry, audit_recorder, transport):
    # Import happens inside the fixture so collection does not error before
    # the implementation exists.
    from general_ludd.ornith.client import OrnithClient
    return OrnithClient(
        mcp_socket_path=socket_path,
        permission_spec=capable_spec,
        sts_registry=sts_registry,
        audit_recorder=audit_recorder,
        transport=transport,
    )


def _make_client(spec, socket_path, sts_registry, audit_recorder, transport):
    from general_ludd.ornith.client import OrnithClient
    return OrnithClient(
        mcp_socket_path=socket_path,
        permission_spec=spec,
        sts_registry=sts_registry,
        audit_recorder=audit_recorder,
        transport=transport,
    )


# --- solve() tests ---

def test_solve_calls_mcp_server_with_sts_token(
    client, transport, sts_registry
):
    result = client.solve(
        task_description="fix the bug",
        repo_context_path="/repo",
    )

    # STS token minted with the ornith principal
    sts_registry.mint.assert_called_once()
    args, kwargs = sts_registry.mint.call_args
    # accept either positional or keyword form
    principal = args[0] if args else kwargs.get("principal")
    assert principal == "agent:ornith", (
        f"expected principal 'agent:ornith', got {principal!r}"
    )

    # transport.call_tool invoked with name="ornith_solve"
    transport.call_tool.assert_called_once()
    name = transport.call_tool.call_args.args[0]
    assert name == "ornith_solve", f"expected 'ornith_solve', got {name!r}"

    # arguments include the task_description
    call_kwargs = transport.call_tool.call_args.kwargs
    arguments = call_kwargs.get("arguments") or (
        transport.call_tool.call_args.args[1]
        if len(transport.call_tool.call_args.args) > 1 else {}
    )
    assert isinstance(arguments, dict)
    assert arguments.get("task_description") == "fix the bug"

    # the STS token must appear somewhere in the call arguments
    assert any(
        v == "sts-token-xyz" for v in arguments.values()
    ), "STS token not forwarded in MCP arguments"

    # result shape
    assert isinstance(result, dict)
    assert "iterations_used" in result


def test_solve_refuses_when_permission_spec_lacks_ornith_capability(
    socket_path, sts_registry, audit_recorder, transport
):
    spec = StubPermissionSpec(
        capabilities={("agent:ornith", "solve"): False},
        allow_all=False,
    )
    client = _make_client(spec, socket_path, sts_registry, audit_recorder, transport)

    with pytest.raises(PermissionError):
        client.solve(task_description="x", repo_context_path="/repo")

    transport.call_tool.assert_not_called()
    sts_registry.mint.assert_not_called()


def test_solve_enforces_max_iterations_constraint(client):
    # default cap is 50; 51 must be rejected
    with pytest.raises(ValueError):
        client.solve(
            task_description="x",
            repo_context_path="/repo",
            max_iterations=51,
        )


def test_solve_captures_audit_record(client, audit_recorder, sts_registry):
    client.solve(task_description="audit me", repo_context_path="/repo")

    audit_recorder.record.assert_called_once()
    kwargs = audit_recorder.record.call_args.kwargs
    assert kwargs.get("actor") == "agent:ornith"
    assert "outcome" in kwargs, "audit record missing 'outcome' field"
    assert "sts_token" in kwargs, "audit record missing 'sts_token' field"
    assert kwargs.get("sts_token") == "sts-token-xyz"


# --- improve() tests ---

def test_improve_calls_ornith_improve_tool(client, transport):
    result = client.improve(
        target_artifact_path="/repo/playbook.yml",
        feedback_yaml="severity: high",
        artifact_kind="playbook",
    )

    transport.call_tool.assert_called_once()
    name = transport.call_tool.call_args.args[0]
    assert name == "ornith_improve", f"expected 'ornith_improve', got {name!r}"

    call_kwargs = transport.call_tool.call_args.kwargs
    arguments = call_kwargs.get("arguments") or (
        transport.call_tool.call_args.args[1]
        if len(transport.call_tool.call_args.args) > 1 else {}
    )
    assert isinstance(arguments, dict)
    assert arguments.get("target_artifact_path") == "/repo/playbook.yml"
    assert arguments.get("artifact_kind") == "playbook"

    assert isinstance(result, dict)


def test_improve_rejects_invalid_artifact_kind(client):
    with pytest.raises(ValueError):
        client.improve(
            target_artifact_path="/x",
            feedback_yaml="s: low",
            artifact_kind="invalid",
        )


def test_improve_refuses_without_capability(
    socket_path, sts_registry, audit_recorder, transport
):
    spec = StubPermissionSpec(
        capabilities={("agent:ornith", "improve"): False},
        allow_all=False,
    )
    client = _make_client(spec, socket_path, sts_registry, audit_recorder, transport)

    with pytest.raises(PermissionError):
        client.improve(
            target_artifact_path="/x",
            feedback_yaml="s: low",
            artifact_kind="playbook",
        )
