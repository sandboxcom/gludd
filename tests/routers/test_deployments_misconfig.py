"""Tests for the POST /admin/deployments/misconfig-check endpoint.

Builds a bare FastAPI app and calls ``deployments.register(app, {})`` (PSK auth
is daemon middleware, so a bare app needs no auth headers — mirrors
tests/unit/test_observe_router.py).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.routers.deployments import register


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    register(app, {})
    return TestClient(app)


_URL = "/admin/deployments/misconfig-check"


def test_bad_vllm_util_fires_rule_a_critical_with_patch(client: TestClient) -> None:
    resp = client.post(
        _URL,
        json={
            "deployment": {
                "engine": "vllm",
                "gpu_memory_utilization": 0.99,
                "quantization": "awq",
            },
            "gpu_type": "a100_80",  # ampere: non-fp8
            "gpu_count": 1,
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_critical"] is True
    rule_a = [f for f in data["findings"] if f["rule_id"] == "a"]
    assert len(rule_a) == 1
    assert rule_a[0]["severity"] == "critical"
    # A matching remediation with a concrete config_patch is returned.
    rem_a = [r for r in data["remediations"] if r["rule_id"] == "a"]
    assert len(rem_a) == 1
    assert rem_a[0]["config_patch"].get("gpu_memory_utilization") is not None


def test_bogus_engine_is_unknown_engine_critical(client: TestClient) -> None:
    resp = client.post(_URL, json={"deployment": {"engine": "bogus"}})
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_critical"] is True
    assert any(f["rule_id"] == "unknown-engine" for f in data["findings"])


def test_malformed_deployment_list_is_critical_not_500(client: TestClient) -> None:
    resp = client.post(_URL, json={"deployment": []})
    # Never 500: malformed input surfaces as a critical finding at HTTP 200.
    assert resp.status_code == 200
    data = resp.json()
    assert data["has_critical"] is True
    assert any(f["rule_id"] == "malformed-deployment" for f in data["findings"])


def test_explicit_gpu_info_takes_precedence(client: TestClient) -> None:
    # fp8 on an explicitly non-fp8 gpu_info -> rule e critical.
    resp = client.post(
        _URL,
        json={
            "deployment": {"engine": "vllm", "quantization": "fp8"},
            "gpu_info": {"arch": "ampere", "supports_fp8": False, "vram_gb": 40.0},
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert any(f["rule_id"] == "e" for f in data["findings"])
