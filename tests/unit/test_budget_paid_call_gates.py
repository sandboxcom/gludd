"""Budget enforcement on the four previously-ungated paid-model-call paths.

A verified audit found four paid-model-call paths that bypassed budget
enforcement on master:

  B1 — ModelGateway accepted a ``budget_guard`` but NO construction site passed
       one, so ``self._budget_guard`` was always None and ``record_spend`` from
       ``_invoke_and_bill`` never fired.
  B3 — worker ``/jobs/execute`` called the model with no budget pre-check.
  B4 — ExecutionEngine.execute called ``call_model`` with no pre-check and took
       no budget param.
  B5 — routers/models.py ``/admin/models/call`` called the gateway with no
       pre-check.

These tests prove each newly-gated path REFUSES when the guard is exhausted and
PROCEEDS when it has headroom, and that the gateway construction sites now wire
the guard through (B1).
"""

from __future__ import annotations

import os
import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.controllers.budget import RunBudgetGuard
from general_ludd.execution.engine import ExecutionEngine
from general_ludd.models.gateway import ModelGateway, ModelProfile
from general_ludd.schemas.job import JobSpec

# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _exhausted_guard() -> RunBudgetGuard:
    """A RunBudgetGuard already over its run budget (check_all_limits -> deny)."""
    guard = RunBudgetGuard(run_budget_usd=1.0)
    guard.record_spend(5.0)  # total 5.0 > 1.0 -> check_run_budget denies
    assert guard.check_all_limits(0.0)["allowed"] is False
    return guard


def _headroom_guard() -> RunBudgetGuard:
    """A RunBudgetGuard with plenty of headroom (check_all_limits -> allow)."""
    guard = RunBudgetGuard(run_budget_usd=100.0)
    assert guard.check_all_limits(0.0)["allowed"] is True
    return guard


def _fake_gateway_returning(content: str) -> Any:
    gw = MagicMock()
    gw.call_model = MagicMock(return_value=MagicMock(content=content))
    return gw


def _code_job() -> JobSpec:
    return JobSpec(
        job_id="JOB-BUD-001",
        todo_id="TODO-BUD-001",
        playbook="code",
        queue="core",
        work_type="code",  # a generation work type
        prompt_text="Write a function",
        project_id="proj-1",
    )


# --------------------------------------------------------------------------- #
# B1 — ModelGateway construction sites now pass a budget_guard                 #
# --------------------------------------------------------------------------- #


def test_b1_gateway_stores_injected_budget_guard() -> None:
    """The gateway must retain the guard so _invoke_and_bill can record spend."""
    guard = _headroom_guard()
    gw = ModelGateway(profiles=[], budget_guard=guard)
    assert gw._budget_guard is guard


def test_b1_worker_build_gateway_passes_guard(monkeypatch: Any) -> None:
    """build_gateway_from_config threads the run budget guard into the gateway."""
    from general_ludd.worker import app as worker_app

    sentinel_guard = _headroom_guard()
    monkeypatch.setattr(
        worker_app, "build_budget_guard_from_config", lambda: sentinel_guard
    )

    captured: dict[str, Any] = {}

    real_gateway_cls = worker_app.ModelGateway

    def _spy_gateway(*args: Any, **kwargs: Any) -> Any:
        captured.update(kwargs)
        return real_gateway_cls(*args, **kwargs)

    monkeypatch.setattr(worker_app, "ModelGateway", _spy_gateway)

    # Force config to yield exactly one profile so a gateway is built.
    from types import SimpleNamespace

    uc = SimpleNamespace(
        model_profiles={
            "default": ModelProfile(
                model_profile_id="default",
                provider="openai",
                model_name="gpt-x",
            )
        },
        budget={},
    )
    monkeypatch.setattr(
        "general_ludd.config.loader.load_user_config", lambda: uc
    )

    gw = worker_app.build_gateway_from_config()
    assert gw is not None
    assert captured.get("budget_guard") is sentinel_guard


# --------------------------------------------------------------------------- #
# B3 — worker /jobs/execute pre-checks the budget                             #
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@patch("general_ludd.worker.app.get_runner")
async def test_b3_worker_execute_refuses_when_over_budget(
    mock_get_runner: MagicMock,
) -> None:
    from general_ludd.worker.app import create_app

    adapter = MagicMock()
    adapter.list_playbooks.return_value = ["code"]
    mock_get_runner.return_value = adapter

    gw = _fake_gateway_returning("should not be called")
    app = create_app(gateway=gw, budget_guard=_exhausted_guard())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/jobs/execute",
            json={
                "job_id": "JOB-BUD-001",
                "todo_id": "TODO-BUD-001",
                "playbook": "code",
                "queue": "core",
                "work_type": "code",
                "prompt_text": "x",
            },
        )

    assert resp.status_code == 429
    assert "budget" in resp.json()["detail"].lower()
    # The paid model call must never have fired.
    gw.call_model.assert_not_called()


@pytest.mark.asyncio
@patch("general_ludd.worker.app._invoke_gateway_for_job")
@patch("general_ludd.worker.app.get_runner")
async def test_b3_worker_execute_proceeds_with_headroom(
    mock_get_runner: MagicMock,
    mock_invoke: MagicMock,
) -> None:
    from general_ludd.worker.app import create_app

    tmp = tempfile.mkdtemp()
    adapter = MagicMock()
    adapter.list_playbooks.return_value = ["code"]
    adapter.prepare_job_dirs.return_value = {"root": os.path.join(tmp, "JOB")}
    adapter.write_vars.return_value = os.path.join(tmp, "JOB", "vars")
    adapter.run_playbook.return_value = {"rc": 0, "output": "ok", "events": []}
    mock_get_runner.return_value = adapter
    mock_invoke.return_value = "generated text"

    gw = _fake_gateway_returning("ok")
    app = create_app(gateway=gw, budget_guard=_headroom_guard())

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post(
            "/jobs/execute",
            json={
                "job_id": "JOB-BUD-OK",
                "todo_id": "TODO-BUD-OK",
                "playbook": "code",
                "queue": "core",
                "work_type": "code",
                "prompt_text": "x",
            },
        )

    assert resp.status_code == 200
    # With headroom the generation path runs.
    mock_invoke.assert_called_once()


# --------------------------------------------------------------------------- #
# B4 — ExecutionEngine.execute pre-checks the budget                          #
# --------------------------------------------------------------------------- #


def test_b4_execution_engine_refuses_when_over_budget() -> None:
    gw = _fake_gateway_returning("diff content")
    engine = ExecutionEngine(
        model_gateway=gw,
        workspace_path=tempfile.mkdtemp(),
        budget_guard=_exhausted_guard(),
    )

    result = engine.execute(_code_job())

    assert result.exit_code == 1
    assert "budget" in (result.result_summary or "").lower()
    gw.call_model.assert_not_called()


def test_b4_execution_engine_proceeds_with_headroom() -> None:
    diff = (
        "```\n--- a/src/main.py\n+++ b/src/main.py\n"
        "@@ -1 +1 @@\n-print('hello')\n+print('hello world')\n```"
    )
    gw = _fake_gateway_returning(diff)
    engine = ExecutionEngine(
        model_gateway=gw,
        workspace_path=tempfile.mkdtemp(),
        budget_guard=_headroom_guard(),
    )

    engine.execute(_code_job())

    gw.call_model.assert_called_once()


def test_b4_execution_engine_noop_gate_when_no_guard() -> None:
    """No injected guard => prior behavior: the call proceeds (no-op gate)."""
    diff = (
        "```\n--- a/src/main.py\n+++ b/src/main.py\n"
        "@@ -1 +1 @@\n-a\n+b\n```"
    )
    gw = _fake_gateway_returning(diff)
    engine = ExecutionEngine(
        model_gateway=gw, workspace_path=tempfile.mkdtemp()
    )

    engine.execute(_code_job())

    gw.call_model.assert_called_once()


# --------------------------------------------------------------------------- #
# B5 — routers/models.py /admin/models/call pre-checks the budget             #
# --------------------------------------------------------------------------- #


def _build_models_router_app(guard: Any, gateway: Any) -> Any:
    from fastapi import FastAPI

    from general_ludd.routers import models as models_router

    app = FastAPI()
    app.state._model_gateway = gateway
    app.state._budget_guard = guard
    models_router.register(app, {})
    return app


def test_b5_admin_models_call_refuses_when_over_budget() -> None:
    from fastapi.testclient import TestClient

    gw = MagicMock()
    gw.list_profiles.return_value = [
        ModelProfile(
            model_profile_id="default", provider="openai", model_name="gpt-x"
        )
    ]
    gw.call_model = MagicMock(return_value=MagicMock(content="nope", usage_metadata={}))

    app = _build_models_router_app(_exhausted_guard(), gw)
    client = TestClient(app)

    resp = client.post("/admin/models/call", json={"prompt": "hi"})

    assert resp.status_code == 429
    assert "budget" in resp.json()["detail"].lower()
    gw.call_model.assert_not_called()


def test_b5_admin_models_call_proceeds_with_headroom() -> None:
    from fastapi.testclient import TestClient

    gw = MagicMock()
    gw.list_profiles.return_value = [
        ModelProfile(
            model_profile_id="default", provider="openai", model_name="gpt-x"
        )
    ]
    gw.call_model = MagicMock(
        return_value=MagicMock(content="hello", usage_metadata={})
    )

    app = _build_models_router_app(_headroom_guard(), gw)
    client = TestClient(app)

    resp = client.post("/admin/models/call", json={"prompt": "hi"})

    assert resp.status_code == 200
    assert resp.json()["text"] == "hello"
    gw.call_model.assert_called_once()
