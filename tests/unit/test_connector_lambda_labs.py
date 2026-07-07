"""Unit tests for the Lambda Labs GPU cloud connector.

Transport is fully MOCKED via ``httpx.MockTransport`` — no real network is
touched. Every request is captured so auth headers, request bodies and URL
paths can be asserted.
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from general_ludd.connectors.lambda_labs import (
    LambdaInstance,
    LambdaInstanceType,
    LambdaLabsClient,
    LambdaLabsError,
)

_KEY = "lambda-secret-key-DO-NOT-LEAK"
_ENV = "LAMBDALABS_API_KEY"

_INSTANCES_PAYLOAD: dict[str, Any] = {
    "data": [
        {
            "id": "12345",
            "name": "gpu-box-1",
            "status": "running",
            "instance_type": {"name": "gpu_8x_h100_sxm4"},
            "ip": "1.2.3.4",
            "region": {"name": "us-east-1", "description": "Texas, USA"},
            "hostname": "1-2-3-4.sslip.io",
            "ssh_key_names": ["my-key"],
            "file_system_names": [],
        },
        {
            "id": "67890",
            "name": "gpu-box-2",
            "status": "booting",
            "instance_type": {"name": "gpu_1x_a10"},
            "ip": "5.6.7.8",
            "region": {"name": "us-west-2", "description": "California, USA"},
            "hostname": "5-6-7-8.sslip.io",
            "ssh_key_names": ["my-key"],
            "file_system_names": [],
        },
    ]
}

_INSTANCE_TYPES_PAYLOAD: dict[str, Any] = {
    "data": {
        "gpu_8x_h100_sxm4": {
            "instance_type": {
                "name": "gpu_8x_h100_sxm4",
                "specs": {
                    "gpus": 8,
                    "gpu_type": "H100-SXM5-80GB",
                    "vcpus": 112,
                    "memory_gib": 1152,
                    "storage_gib": 7680,
                },
                "context": [],
                "price_cents_per_hour": 2900,
                "regions_with_capacity_available": ["us-east-1"],
            }
        },
        "gpu_8x_a100_sxm4": {
            "instance_type": {
                "name": "gpu_8x_a100_sxm4",
                "specs": {
                    "gpus": 8,
                    "gpu_type": "A100-SXM4-80GB",
                    "vcpus": 96,
                    "memory_gib": 1152,
                    "storage_gib": 7680,
                },
                "context": [],
                "price_cents_per_hour": 1500,
                "regions_with_capacity_available": ["us-west-2"],
            }
        },
    }
}


def _handler_factory(
    captured: dict[str, Any],
    routes: dict[tuple[str, str], httpx.Response],
) -> httpx.MockTransport:
    """Build a MockTransport dispatching on (METHOD, url-path-suffix) -> response.

    Routes are keyed on a path SUFFIX (e.g. ``"/instances"``) so the handler
    matches regardless of the ``/api/v1`` prefix that ``base_url`` contributes
    to the request URL. A suffix matches when the request path equals it or
    ends with ``"/" + suffix``.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        captured["request"] = request
        captured.setdefault("requests", []).append(request)
        method = request.method
        path = request.url.path
        for (route_method, suffix), response in routes.items():
            if method != route_method:
                continue
            if path == suffix or path.endswith("/" + suffix.lstrip("/")):
                return response
        return httpx.Response(404, json={"error": f"no route for {(method, path)}"})

    return httpx.MockTransport(handler)


def _client(
    captured: dict[str, Any],
    routes: dict[tuple[str, str], httpx.Response] | None = None,
    **cfg: Any,
) -> LambdaLabsClient:
    return LambdaLabsClient(cfg or None, transport=_handler_factory(captured, routes or {}))


# --------------------------------------------------------------------------- #
# construction
# --------------------------------------------------------------------------- #
def test_construct_defaults():
    client = LambdaLabsClient()
    assert client.base_url == "https://cloud.lambdalabs.com/api/v1"
    assert client.api_key_env == "LAMBDALABS_API_KEY"


def test_construct_custom_base_url():
    client = LambdaLabsClient({"base_url": "https://gpu.example.com/api/v1"})
    assert client.base_url == "https://gpu.example.com/api/v1"


def test_custom_api_key_env(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    monkeypatch.setenv("MY_LAMBDA_KEY", _KEY)
    captured: dict[str, Any] = {}
    routes = {("GET", "/instances"): httpx.Response(200, json=_INSTANCES_PAYLOAD)}
    client = LambdaLabsClient(
        {"api_key_env": "MY_LAMBDA_KEY"},
        transport=_handler_factory(captured, routes),
    )
    result = client.list_instances()
    assert len(result) == 2
    assert captured["request"].headers["Authorization"] == f"Bearer {_KEY}"


def test_ssrf_rejects_internal_base_url():
    with pytest.raises(ValueError):
        LambdaLabsClient({"base_url": "http://127.0.0.1:8080"})
    with pytest.raises(ValueError):
        LambdaLabsClient({"base_url": "http://169.254.169.254"})
    with pytest.raises(ValueError):
        LambdaLabsClient({"base_url": "http://10.1.2.3/api/v1"})


# --------------------------------------------------------------------------- #
# list_instances
# --------------------------------------------------------------------------- #
def test_list_instances_parses_data(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {("GET", "/instances"): httpx.Response(200, json=_INSTANCES_PAYLOAD)}
    client = _client(captured, routes)
    result = client.list_instances()
    assert len(result) == 2
    inst = result[0]
    assert inst["id"] == "12345"
    assert inst["name"] == "gpu-box-1"
    assert inst["status"] == "running"
    assert inst["instance_type"]["name"] == "gpu_8x_h100_sxm4"
    assert inst["region"]["name"] == "us-east-1"
    assert inst["ip"] == "1.2.3.4"
    assert inst["ssh_key_names"] == ["my-key"]
    assert result[1]["status"] == "booting"


def test_list_instances_sends_bearer_auth(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {("GET", "/instances"): httpx.Response(200, json=_INSTANCES_PAYLOAD)}
    client = _client(captured, routes)
    client.list_instances()
    req = captured["request"]
    assert req.headers["Authorization"] == f"Bearer {_KEY}"
    assert req.url.path.endswith("/instances")


def test_list_instances_empty(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {("GET", "/instances"): httpx.Response(200, json={"data": []})}
    client = _client(captured, routes)
    assert client.list_instances() == []


def test_list_instances_missing_key_raises(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    captured: dict[str, Any] = {}
    routes = {("GET", "/instances"): httpx.Response(200, json=_INSTANCES_PAYLOAD)}
    client = _client(captured, routes)
    with pytest.raises(LambdaLabsError, match="not set"):
        client.list_instances()


def test_list_instances_500_raises(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {("GET", "/instances"): httpx.Response(500, json={"error": "boom"})}
    client = _client(captured, routes)
    with pytest.raises(LambdaLabsError, match="HTTP 500"):
        client.list_instances()


# --------------------------------------------------------------------------- #
# list_instance_types
# --------------------------------------------------------------------------- #
def test_list_instance_types_parses_h100_and_a100(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {("GET", "/instance-types"): httpx.Response(200, json=_INSTANCE_TYPES_PAYLOAD)}
    client = _client(captured, routes)
    types = client.list_instance_types()
    assert len(types) == 2
    names = {t["name"] for t in types}
    assert "gpu_8x_h100_sxm4" in names
    assert "gpu_8x_a100_sxm4" in names

    h100 = next(t for t in types if t["name"] == "gpu_8x_h100_sxm4")
    assert h100["specs"]["gpus"] == 8
    assert h100["specs"]["gpu_type"] == "H100-SXM5-80GB"
    assert h100["price_cents_per_hour"] == 2900
    assert "us-east-1" in h100["regions_with_capacity_available"]

    a100 = next(t for t in types if t["name"] == "gpu_8x_a100_sxm4")
    assert a100["specs"]["gpu_type"] == "A100-SXM4-80GB"
    assert a100["price_cents_per_hour"] == 1500
    assert "us-west-2" in a100["regions_with_capacity_available"]


def test_list_instance_types_empty(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {("GET", "/instance-types"): httpx.Response(200, json={"data": {}})}
    client = _client(captured, routes)
    assert client.list_instance_types() == []


# --------------------------------------------------------------------------- #
# launch_instance
# --------------------------------------------------------------------------- #
def test_launch_instance_returns_instance(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {
        ("POST", "/instance-operations/launch"): httpx.Response(
            200, json={"data": {"instance_ids": ["99999"]}}
        )
    }
    client = _client(captured, routes)
    inst = client.launch_instance("gpu_8x_h100_sxm4", "my-new-box", "us-east-1")
    assert inst["id"] == "99999"
    assert inst["name"] == "my-new-box"

    req = captured["request"]
    body = json.loads(req.content)
    assert body["instance_type"] == "gpu_8x_h100_sxm4"
    assert body["name"] == "my-new-box"
    assert body["region_name"] == "us-east-1"
    assert body["quantity"] == 1


def test_launch_instance_with_ssh_keys_and_quantity(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {
        ("POST", "/instance-operations/launch"): httpx.Response(
            200, json={"data": {"instance_ids": ["aaa", "bbb"]}}
        )
    }
    client = _client(captured, routes)
    inst = client.launch_instance(
        "gpu_8x_a100_sxm4",
        "fleet",
        "us-west-2",
        ssh_key_names=["dev-key"],
        quantity=2,
    )
    assert inst["id"] == "aaa"
    body = json.loads(captured["request"].content)
    assert body["ssh_key_names"] == ["dev-key"]
    assert body["quantity"] == 2


def test_launch_instance_no_ids_raises(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {
        ("POST", "/instance-operations/launch"): httpx.Response(
            200, json={"data": {"instance_ids": []}}
        )
    }
    client = _client(captured, routes)
    with pytest.raises(LambdaLabsError, match="instance_ids"):
        client.launch_instance("gpu_8x_h100_sxm4", "x", "us-east-1")


# --------------------------------------------------------------------------- #
# terminate_instance
# --------------------------------------------------------------------------- #
def test_terminate_instance(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {
        ("POST", "/instance-operations/terminate"): httpx.Response(204),
    }
    client = _client(captured, routes)
    client.terminate_instance("12345")  # must not raise
    req = captured["request"]
    body = json.loads(req.content)
    assert body == {"instance_ids": ["12345"]}


def test_terminate_instance_error(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {
        ("POST", "/instance-operations/terminate"): httpx.Response(500, json={"error": "boom"}),
    }
    client = _client(captured, routes)
    with pytest.raises(LambdaLabsError, match="HTTP 500"):
        client.terminate_instance("12345")


# --------------------------------------------------------------------------- #
# health
# --------------------------------------------------------------------------- #
def test_health_ok(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {("GET", "/instances"): httpx.Response(200, json={"data": []})}
    client = _client(captured, routes)
    result = client.health()
    assert result["ok"] is True
    assert result["reachable"] is True
    assert result["api_key_valid"] is True


def test_health_unauthorized(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {("GET", "/instances"): httpx.Response(401, json={"error": "bad key"})}
    client = _client(captured, routes)
    result = client.health()
    assert result["ok"] is False
    assert result["api_key_valid"] is False
    assert result["reachable"] is True


def test_health_missing_key(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    captured: dict[str, Any] = {}
    routes = {("GET", "/instances"): httpx.Response(200, json={"data": []})}
    client = _client(captured, routes)
    result = client.health()
    assert result["ok"] is False
    assert result["api_key_valid"] is None


def test_health_never_raises(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)

    def boom(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    client = LambdaLabsClient(transport=httpx.MockTransport(boom))
    result = client.health()
    assert result["ok"] is False
    assert result["reachable"] is False


# --------------------------------------------------------------------------- #
# error / safety invariants
# --------------------------------------------------------------------------- #
def test_api_key_never_leaked_in_errors(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {("GET", "/instances"): httpx.Response(500, json={"error": "boom"})}
    client = _client(captured, routes)
    with pytest.raises(LambdaLabsError) as exc_info:
        client.list_instances()
    assert _KEY not in str(exc_info.value)


def test_malformed_json_raises(monkeypatch):
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {
        ("GET", "/instances"): httpx.Response(
            200, content=b"not json", headers={"content-type": "text/plain"}
        )
    }
    client = _client(captured, routes)
    with pytest.raises(LambdaLabsError, match="malformed JSON"):
        client.list_instances()


def test_typeddicts_importable_and_returned(monkeypatch):
    """The public TypedDicts are importable and instances/types are dicts."""
    monkeypatch.setenv(_ENV, _KEY)
    captured: dict[str, Any] = {}
    routes = {
        ("GET", "/instances"): httpx.Response(200, json=_INSTANCES_PAYLOAD),
        ("GET", "/instance-types"): httpx.Response(200, json=_INSTANCE_TYPES_PAYLOAD),
    }
    client = _client(captured, routes)
    instances = client.list_instances()
    types = client.list_instance_types()
    assert isinstance(instances[0], dict)
    assert isinstance(types[0], dict)
    assert LambdaInstance is not None
    assert LambdaInstanceType is not None
