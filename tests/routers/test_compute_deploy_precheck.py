"""Tests for the pre-deploy misconfig gate on POST /admin/compute/deploy (SLICE B).

Builds a bare FastAPI app and calls ``compute.register(app, {})`` (PSK auth is
daemon middleware, so a bare app needs no auth headers — mirrors
tests/routers/test_deployments_misconfig.py).

The router's ``_get_deployment_manager`` returns a cached
``app.state._deployment_manager`` by identity, so tests inject a MagicMock with
an ``AsyncMock`` ``deploy`` and assert on its call count.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from general_ludd.infra.compute import ComputeProvider, GPUType
from general_ludd.routers.compute import register

_URL = "/admin/compute/deploy"

# A vllm deploy with gpu_memory_utilization=0.99 trips rule 'a' (critical).
_CRITICAL_REQ: dict[str, Any] = {
    "provider": "aws",
    "gpu_type": "a10",
    "model_name": "org/model",
    "engine": "vllm",
    "gpu_memory_utilization": 0.99,
}


def _mock_instance() -> MagicMock:
    inst = MagicMock()
    inst.instance_id = "i-12345"
    inst.provider = ComputeProvider.AWS
    inst.status = "running"
    inst.ip_address = "1.2.3.4"
    inst.port = 8000
    inst.gpu_type = GPUType.A10
    inst.endpoint_url = "http://1.2.3.4:8000/v1"
    return inst


def _build(
    *, deploy: AsyncMock | None = None, record_failure: MagicMock | None = None
) -> TestClient:
    app = FastAPI()
    register(app, {})
    mgr = MagicMock()
    mgr.deploy = deploy if deploy is not None else AsyncMock(return_value=_mock_instance())
    app.state._deployment_manager = mgr
    if record_failure is not None:
        checker = MagicMock()
        checker.record_failure = record_failure
        router = MagicMock()
        router.health_checker = checker
        app.state._deployment_health_router = router
    return TestClient(app, raise_server_exceptions=False)


def test_critical_misconfig_without_force_returns_422_and_skips_deploy() -> None:
    deploy = AsyncMock(return_value=_mock_instance())
    client = _build(deploy=deploy)

    resp = client.post(_URL, json=_CRITICAL_REQ)

    assert resp.status_code == 422
    # The spend is refused BEFORE the deploy manager is touched.
    deploy.assert_not_called()
    detail = resp.json()["detail"]
    assert any(f["rule_id"] == "a" for f in detail["misconfig"])
    assert detail["remediations"]
    assert detail["remediations"][0]["rule_id"] == "a"


def test_force_true_bypasses_critical_and_calls_deploy() -> None:
    deploy = AsyncMock(return_value=_mock_instance())
    client = _build(deploy=deploy)

    resp = client.post(_URL, json={**_CRITICAL_REQ, "force": True})

    assert resp.status_code == 200
    deploy.assert_called_once()
    body = resp.json()
    assert body["instance_id"] == "i-12345"
    # Non-blocking findings ride along on the success body.
    assert any(f["rule_id"] == "a" for f in body["findings"])


def test_deploy_failure_returns_500_with_findings_and_records_failure() -> None:
    deploy = AsyncMock(side_effect=RuntimeError("terraform blew up"))
    record_failure = MagicMock()
    # force past the critical gate so we actually reach mgr.deploy.
    client = _build(deploy=deploy, record_failure=record_failure)

    resp = client.post(_URL, json={**_CRITICAL_REQ, "force": True})

    assert resp.status_code == 500
    detail = resp.json()["detail"]
    assert detail["error"] == "compute deploy failed"
    assert any(f["rule_id"] == "a" for f in detail["misconfig_findings"])
    # The failure was recorded against the health checker with the model name.
    record_failure.assert_called_once()
    args, kwargs = record_failure.call_args
    assert args[0] == "org/model"
    assert kwargs.get("kind") == "deploy_failure"


def test_deploy_failure_without_health_checker_still_500() -> None:
    # No health router wired -> record path is a no-op, still a clean 500.
    deploy = AsyncMock(side_effect=RuntimeError("boom"))
    client = _build(deploy=deploy)

    resp = client.post(_URL, json={**_CRITICAL_REQ, "force": True})

    assert resp.status_code == 500
    assert "misconfig_findings" in resp.json()["detail"]


def test_clean_config_deploys_and_returns_empty_findings() -> None:
    deploy = AsyncMock(return_value=_mock_instance())
    client = _build(deploy=deploy)

    resp = client.post(
        _URL,
        json={
            "provider": "aws",
            "gpu_type": "a10",
            "model_name": "org/model",
            "engine": "vllm",
            "gpu_memory_utilization": 0.90,
        },
    )

    assert resp.status_code == 200
    deploy.assert_called_once()
    assert resp.json()["findings"] == []


@pytest.mark.parametrize("missing", ["provider", "gpu_type", "model_name"])
def test_missing_required_field_still_422_before_precheck(missing: str) -> None:
    deploy = AsyncMock(return_value=_mock_instance())
    client = _build(deploy=deploy)
    req = {k: v for k, v in _CRITICAL_REQ.items() if k != missing}

    resp = client.post(_URL, json=req)

    assert resp.status_code == 422
    deploy.assert_not_called()
