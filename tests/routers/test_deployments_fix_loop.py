"""Tests for the SLM fix-loop endpoints on the deployments router.

Covers POST /admin/deployments/suggest-fix and the
fixes/{fix_id}/approve|reject decision endpoints. Builds a bare FastAPI app and
calls ``deployments.register(app, {})`` (PSK auth is daemon middleware, so a bare
app needs no auth headers — mirrors tests/routers/test_deployments_misconfig.py).
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.deployments import register


class _StubResp:
    def __init__(self, content: str) -> None:
        self.content = content


class _StubGateway:
    """Minimal ModelGateway stand-in returning a fixed JSON patch."""

    def __init__(self, patch: dict[str, Any]) -> None:
        self._patch = patch
        self.calls: list[dict[str, Any]] = []

    def call_model(self, profile_id: str, **kwargs: Any) -> _StubResp:
        self.calls.append({"profile_id": profile_id, **kwargs})
        return _StubResp(json.dumps(self._patch))


def _make_client(
    *, gateway: Any = None, redeploy: Any = None
) -> tuple[TestClient, FastAPI]:
    app = FastAPI()
    register(app, {})
    if gateway is not None:
        app.state._model_gateway = gateway
    if redeploy is not None:
        app.state._deployment_redeploy_fn = redeploy
    return TestClient(app), app


# A deployment that fires rule "a" (critical) with a deterministic patch.
_BAD_DEPLOYMENT: dict[str, Any] = {
    "engine": "vllm",
    "gpu_memory_utilization": 0.99,
    "quantization": "awq",
}
_SUGGEST_URL = "/admin/deployments/suggest-fix"


def test_suggest_fix_no_gateway_is_failsoft_deterministic() -> None:
    client, _ = _make_client()  # no _model_gateway on app.state
    resp = client.post(
        _SUGGEST_URL,
        json={
            "deployment": _BAD_DEPLOYMENT,
            "gpu_type": "a100_80",
            "gpu_count": 1,
        },
    )
    assert resp.status_code == 200  # never 503 on a missing model
    data = resp.json()
    assert data["source"] == "deterministic"
    assert data["fix_id"]
    # The deterministic remediation supplies a concrete gpu_memory_utilization.
    assert data["patch"].get("gpu_memory_utilization") is not None


def test_suggest_fix_with_gateway_uses_slm_patch() -> None:
    slm_patch = {"gpu_memory_utilization": 0.85, "slm_marker": "yes"}
    gateway = _StubGateway(slm_patch)
    client, _ = _make_client(gateway=gateway)
    resp = client.post(
        _SUGGEST_URL,
        json={
            "deployment": _BAD_DEPLOYMENT,
            "gpu_type": "a100_80",
            "gpu_count": 1,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "slm"
    assert data["patch"] == slm_patch
    assert gateway.calls  # the gateway was actually consulted


def test_approve_returns_merged_config_and_blocks_double_decide() -> None:
    client, _ = _make_client()
    suggest = client.post(
        _SUGGEST_URL, json={"deployment": _BAD_DEPLOYMENT, "gpu_type": "a100_80"}
    ).json()
    fix_id = suggest["fix_id"]
    patch = suggest["patch"]

    approve = client.post(f"/admin/deployments/fixes/{fix_id}/approve")
    assert approve.status_code == 200
    body = approve.json()
    assert body["status"] == "approved"
    assert body["merged_config"] == {**_BAD_DEPLOYMENT, **patch}

    # Re-deciding an already-approved fix is an error (not 200).
    again = client.post(f"/admin/deployments/fixes/{fix_id}/approve")
    assert again.status_code != 200


def test_reject_records_reason() -> None:
    client, _ = _make_client()
    fix_id = client.post(
        _SUGGEST_URL, json={"deployment": _BAD_DEPLOYMENT, "gpu_type": "a100_80"}
    ).json()["fix_id"]

    reject = client.post(
        f"/admin/deployments/fixes/{fix_id}/reject",
        json={"reason": "operator says no"},
    )
    assert reject.status_code == 200
    body = reject.json()
    assert body["status"] == "rejected"
    assert body["reason"] == "operator says no"


def test_approve_with_retry_invokes_redeploy_with_merged_config() -> None:
    calls: list[dict[str, Any]] = []

    def _redeploy(merged: dict[str, Any]) -> dict[str, Any]:
        calls.append(merged)
        return {"redeployed": True}

    client, _ = _make_client(redeploy=_redeploy)
    suggest = client.post(
        _SUGGEST_URL, json={"deployment": _BAD_DEPLOYMENT, "gpu_type": "a100_80"}
    ).json()
    fix_id = suggest["fix_id"]
    patch = suggest["patch"]

    approve = client.post(
        f"/admin/deployments/fixes/{fix_id}/approve", json={"retry": True}
    )
    assert approve.status_code == 200
    body = approve.json()
    assert body["status"] == "approved"
    assert body["retried"] is True
    assert calls == [{**_BAD_DEPLOYMENT, **patch}]


def test_approve_missing_fix_id_errors() -> None:
    client, _ = _make_client()
    resp = client.post("/admin/deployments/fixes/does-not-exist/approve")
    assert resp.status_code != 200


def test_suggest_fix_with_explicit_findings() -> None:
    client, _ = _make_client()
    resp = client.post(
        _SUGGEST_URL,
        json={
            "deployment": {"engine": "vllm", "gpu_memory_utilization": 0.99},
            "findings": [
                {
                    "rule_id": "a",
                    "severity": "critical",
                    "engine": "vllm",
                    "message": "gpu_memory_utilization too high",
                    "remediation": "lower it",
                    "evidence": {},
                }
            ],
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["source"] == "deterministic"
    assert data["patch"].get("gpu_memory_utilization") is not None
