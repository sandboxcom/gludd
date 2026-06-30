"""TDD tests for general_ludd.ornith.mcp_server.OrnithMCPServer.

These tests MUST fail initially because the implementation does not exist yet.
All ornith binary invocations are mocked — no real subprocess calls are made.
"""

from unittest.mock import MagicMock, patch

from general_ludd.ornith.mcp_server import OrnithMCPServer


def _server(**kwargs):
    return OrnithMCPServer(**kwargs)


def _find_named(items, name):
    for item in items:
        if item.get("name") == name:
            return item
    return None


def test_mcp_server_exposes_solve_tool():
    server = _server()
    tools = server.list_tools()
    assert _find_named(tools, "ornith_solve") is not None


def test_mcp_server_exposes_improve_tool():
    server = _server()
    tools = server.list_tools()
    assert _find_named(tools, "ornith_improve") is not None


def test_mcp_server_exposes_status_resource():
    server = _server()
    resources = server.list_resources()
    assert _find_named(resources, "ornith_status") is not None


def test_mcp_server_exposes_model_info_resource():
    server = _server()
    resources = server.list_resources()
    assert _find_named(resources, "ornith_model_info") is not None


def test_mcp_server_exposes_meta_prompt():
    server = _server()
    prompts = server.list_prompts()
    assert _find_named(prompts, "ornith_meta") is not None


def test_mcp_server_solve_refuses_when_not_installed():
    server = _server(enabled=False)
    result = server.handle_tool_call(
        "ornith_solve",
        {"task_description": "do thing", "repo_context_path": "/tmp/repo"},
    )
    assert result["installed"] is False
    assert result.get("error")
    assert result.get("patch") is None
    assert result.get("summary") is None


def test_mcp_server_status_reflects_not_installed():
    server = _server(enabled=False)
    status = server.handle_resource_read("ornith_status")
    assert status["installed"] is False
    assert "version" in status
    assert "last_call_at" in status
    assert "total_calls" in status
    assert "success_rate" in status


def test_mcp_server_caches_calls_by_argument_hash():
    server = _server(
        enabled=True,
        ornith_binary_path="/usr/local/bin/ornith",
        ornith_model_sha="abc123",
    )

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    fake_proc.stdout = '{"patch": "diff --git a/f b/f", "summary": "ok"}'
    fake_proc.stderr = ""

    args = {
        "task_description": "fix the bug",
        "repo_context_path": "/tmp/repo",
    }

    with patch(
        "general_ludd.ornith.mcp_server.subprocess.run",
        return_value=fake_proc,
    ) as mock_run:
        first = server.handle_tool_call("ornith_solve", dict(args))
        second = server.handle_tool_call("ornith_solve", dict(args))

        assert mock_run.call_count == 1, (
            f"expected exactly 1 subprocess invocation (cache hit), "
            f"got {mock_run.call_count}"
        )
        assert second == first


def test_mcp_solve_argument_schema_requires_task_description():
    server = _server()
    tools = server.list_tools()
    solve = _find_named(tools, "ornith_solve")
    assert solve is not None
    schema = solve.get("inputSchema", {})
    required = schema.get("required", [])
    assert "task_description" in required
    assert "repo_context_path" in required


def test_mcp_improve_argument_schema_requires_artifact_kind():
    server = _server()
    tools = server.list_tools()
    improve = _find_named(tools, "ornith_improve")
    assert improve is not None
    schema = improve.get("inputSchema", {})
    required = schema.get("required", [])
    assert "target_artifact_path" in required
    assert "feedback_yaml" in required
    assert "artifact_kind" in required

    props = schema.get("properties", {})
    kind_prop = props.get("artifact_kind", {})
    kind_enum = kind_prop.get("enum")
    assert kind_enum == ["playbook", "module", "plugin", "rego"]
