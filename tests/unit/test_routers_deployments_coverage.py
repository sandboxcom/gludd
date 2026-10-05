"""Branch coverage for deployment remediation and approval lifecycles."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.responses import Response

from general_ludd.models.deployment_health import DeploymentStatus
from general_ludd.routers import deployments
from general_ludd.security.permissions import Capability, PermissionSpec


def _client(*, checker: object | None = None) -> tuple[FastAPI, TestClient]:
    """Build one capability-authorized deployment router client."""
    app = FastAPI()
    if checker is not None:
        app.state._deployment_health_router = SimpleNamespace(health_checker=checker)
    deployments.register(app, {})

    @app.middleware("http")
    async def authorized(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request.state.auth_spec = PermissionSpec(
            agent_type="operator",
            capabilities=[Capability(resource="admin:deploy", actions=["write"])],
        )
        return await call_next(request)

    return app, TestClient(app)


def test_remediate_initializes_untracked_deployment() -> None:
    """Create a status when remediation targets an untracked deployment."""
    checker = MagicMock()
    checker.force_remediate.return_value = False
    checker.get_status.return_value = DeploymentStatus(
        deployment_id="new",
        healthy=True,
        consecutive_failures=0,
        last_error=None,
        last_check=1.0,
    )
    _app, client = _client(checker=checker)
    with client:
        response = client.post("/admin/deployments/new/remediate")
    assert response.status_code == 200
    assert response.json()["healthy"] is True
    assert checker.get_status.call_count == 2


def test_gpu_info_and_slm_suggestion_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    """Use explicit GPU context and accept a non-empty SLM patch."""
    app, client = _client()
    app.state._model_gateway = object()
    monkeypatch.setattr(
        deployments,
        "make_fix_suggestion_fn",
        lambda _gateway: lambda _deployment, _findings: {"gpu_memory_utilization": 0.8},
    )
    with client:
        checked = client.post(
            "/admin/deployments/misconfig-check",
            json={"deployment": {"engine": "vllm"}, "gpu_info": {"memory_gb": 80}},
        )
        suggested = client.post(
            "/admin/deployments/suggest-fix",
            json={
                "deployment": {"engine": "vllm"},
                "findings": [{"rule_id": "GPU001", "evidence": "not-a-mapping"}],
            },
        )
    assert checked.status_code == 200
    assert suggested.status_code == 200
    assert suggested.json()["source"] == "slm"
    assert suggested.json()["patch"] == {"gpu_memory_utilization": 0.8}


def test_suggest_fix_rechecks_with_explicit_gpu_info() -> None:
    """Re-derive findings from caller-supplied GPU metadata."""
    _app, client = _client()
    with client:
        response = client.post(
            "/admin/deployments/suggest-fix",
            json={"deployment": {"engine": "vllm"}, "gpu_info": {"memory_gb": 80}},
        )
    assert response.status_code == 200
    assert response.json()["source"] == "deterministic"


@pytest.mark.parametrize("mode", ["success", "failure", "missing"])
def test_approve_retry_is_observable_and_fail_soft(mode: str) -> None:
    """Report successful, failed, and unavailable redeploy hooks."""
    app, client = _client()
    proposal = app.state._fix_approval_manager.propose({"engine": "vllm"}, {"replicas": 2})
    if mode == "success":
        app.state._deployment_redeploy_fn = lambda merged: {"applied": merged["replicas"]}
    elif mode == "failure":
        def fail_redeploy(_merged: dict[str, object]) -> None:
            raise RuntimeError("redeploy unavailable")

        app.state._deployment_redeploy_fn = fail_redeploy

    with client:
        response = client.post(
            f"/admin/deployments/fixes/{proposal.fix_id}/approve",
            json={"retry": True},
        )
    assert response.status_code == 200
    data = response.json()
    if mode == "success":
        assert data["retried"] is True
        assert data["retry_result"] == {"applied": 2}
    elif mode == "failure":
        assert data["retried"] is False
        assert data["retry_error"] == "redeploy unavailable"
    else:
        assert data["retried"] is False
        assert "no redeploy hook" in data["note"]
