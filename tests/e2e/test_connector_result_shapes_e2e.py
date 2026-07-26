"""E2E contracts for cross-platform connector runner/transport result shapes.

These tests keep injected command runners deterministic on every host.  Windows
and macOS runners may return either captured stdout or a subprocess-style
``(returncode, stdout, stderr)`` tuple.  Podman's transport deliberately returns
an object response because it carries status, headers, and raw bytes together.
The tests pin those public injection contracts without requiring the host
platform binaries or a running Podman socket.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

import pytest


@pytest.mark.parametrize(
    ("module_name", "runner_result", "expected"),
    [
        ("general_ludd.connectors.windows_wmi", "stdout-only", (0, "stdout-only", "")),
        ("general_ludd.connectors.windows_wmi", (7, "", "denied"), (7, "", "denied")),
        ("general_ludd.connectors.macos_security", "stdout-only", (0, "stdout-only", "")),
        ("general_ludd.connectors.macos_security", (9, "partial", "failed"), (9, "partial", "failed")),
    ],
)
def test_platform_runner_result_shapes_are_normalized(module_name, runner_result, expected):
    """String and subprocess tuple runners produce one canonical triple."""
    module = __import__(module_name, fromlist=["_run"])

    result = module._run(lambda _argv: runner_result, ["probe"])

    assert result == expected


@pytest.mark.parametrize("module_name", [
    "general_ludd.connectors.windows_wmi",
    "general_ludd.connectors.macos_security",
])
def test_platform_runner_rejects_unrecognized_object_result(module_name):
    """A malformed fake fails loudly instead of silently corrupting records."""
    module = __import__(module_name, fromlist=["_run"])

    with pytest.raises(TypeError, match="runner must return"):
        module._run(lambda _argv: object(), ["probe"])


@dataclass
class _PodmanResponse:
    """Minimal object response matching Podman's transport protocol."""

    status: int
    headers: dict[str, str]
    body: bytes


def test_podman_object_transport_response_normalizes_inventory():
    """Podman consumes object responses and emits canonical inventory records."""
    from general_ludd.connectors.podman import PodmanSource

    payload = [{"Id": "abc123", "Names": ["/api"], "State": "running", "Status": "Up", "Image": "demo:1"}]

    def transport(method, path, query, base_url, timeout):
        assert (method, path) == ("GET", "/containers/json")
        assert query is None
        assert base_url.startswith("unix://")
        assert timeout == 10.0
        return _PodmanResponse(200, {"content-type": "application/json"}, json.dumps(payload).encode())

    records = PodmanSource(transport=transport).query({"mode": "ps"})

    assert records[0]["source"] == "podman"
    assert records[0]["labels"] == {
        "container_id": "abc123",
        "container_name": "api",
        "image": "demo:1",
    }
    assert records[0]["level_or_status"] == "running"


def test_podman_object_transport_response_preserves_log_stream_and_timestamp():
    """The same object response contract handles Docker-compatible log bytes."""
    from general_ludd.connectors.podman import PodmanSource

    body = b"2026-01-02T03:04:05Z ready\n"

    def transport(method, path, query, _base_url, _timeout):
        assert method == "GET"
        assert path == "/containers/c1/logs"
        assert query == {"stdout": "1", "stderr": "1", "timestamps": "1", "tail": "100"}
        return _PodmanResponse(200, {}, body)

    records = PodmanSource(transport=transport).query({"mode": "logs", "container_id": "c1"})

    assert records == [{
        "ts": "2026-01-02T03:04:05Z",
        "source": "podman",
        "kind": "logs",
        "level_or_status": "stdout",
        "message": "ready",
        "value": None,
        "labels": {"container_id": "c1", "container_name": "", "stream": "stdout"},
        "raw": "2026-01-02T03:04:05Z ready",
    }]
