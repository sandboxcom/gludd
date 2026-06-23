"""TDD tests for the self-update FastAPI router (Phase 2 Step 2).

The router exposes two PSK-gated admin endpoints that wrap the pure
self-update pipeline (classifier -> apply ladder / priority backlog):

  * POST /admin/self-update/plan    -> classify -> apply_plan -> result
  * POST /admin/self-update/enqueue -> classify -> to_todo_spec -> create todo

PSK gating is applied by the daemon middleware (same pattern as
``routers/self_improve.py``); these unit tests exercise the endpoint logic
directly via ``create_daemon_app``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from general_ludd.daemon import create_daemon_app
from general_ludd.self_update.apply import ApplyOutcome, ApplyResult, AuditRecord
from general_ludd.self_update.model import (
    ApplyTier,
    ChangeKind,
    SelfUpdatePlan,
    SelfUpdateRequest,
    Subsystem,
)


@pytest.fixture
def app(tmp_path):
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    return create_daemon_app(config_dir=str(config_dir))


@pytest.fixture
def transport(app):
    return ASGITransport(app=app)


def _plan() -> SelfUpdatePlan:
    return SelfUpdatePlan(
        subsystem=Subsystem.CONFIG,
        change_kind=ChangeKind.VALUE_EDIT,
        target_files=("config/general-ludd.yml",),
        apply_tier=ApplyTier.CONFIG,
        requires_approval=False,
        rationale="routed to subsystem=config (kw-score=2)",
        confidence=0.75,
    )


def _audit() -> AuditRecord:
    return AuditRecord(
        outcome=ApplyOutcome.APPLIED,
        subsystem=Subsystem.CONFIG.value,
        change_kind=ChangeKind.VALUE_EDIT.value,
        apply_tier=ApplyTier.CONFIG.value,
        target_files=("config/general-ludd.yml",),
        requested_by="operator",
        reason="auto-applied config-tier change",
        approved=False,
    )


class TestSelfUpdatePlanEndpoint:
    @pytest.mark.asyncio
    async def test_plan_returns_applied_result(self, transport):
        with patch(
            "general_ludd.routers.self_update.classify", return_value=_plan()
        ) as mock_classify, patch(
            "general_ludd.routers.self_update.apply_plan",
            return_value=ApplyResult(
                outcome=ApplyOutcome.APPLIED,
                audit=_audit(),
                landed_files=("config/general-ludd.yml",),
            ),
        ) as mock_apply:
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/admin/self-update/plan",
                    json={
                        "raw_text": "increase the spend window to 2h",
                        "requested_by": "operator",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok"
                assert data["outcome"] == ApplyOutcome.APPLIED
                assert data["applied"] is True
                assert data["subsystem"] == Subsystem.CONFIG.value
                assert data["change_kind"] == ChangeKind.VALUE_EDIT.value
                assert data["apply_tier"] == ApplyTier.CONFIG.value
                assert "config/general-ludd.yml" in data["landed_files"]
                assert data["audit"]["outcome"] == ApplyOutcome.APPLIED

                mock_classify.assert_called_once()
                mock_apply.assert_called_once()

    @pytest.mark.asyncio
    async def test_plan_classifies_raw_text_into_request(self, transport):
        captured: dict[str, object] = {}

        def _capture(request: SelfUpdateRequest) -> SelfUpdatePlan:
            captured["request"] = request
            return _plan()

        with patch(
            "general_ludd.routers.self_update.classify", side_effect=_capture
        ), patch(
            "general_ludd.routers.self_update.apply_plan",
            return_value=ApplyResult(outcome=ApplyOutcome.APPLIED, audit=_audit()),
        ):
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/admin/self-update/plan",
                    json={
                        "raw_text": "tune the spend cap",
                        "requested_by": "ops",
                        "approval_token": "tok-123",
                    },
                )
                assert resp.status_code == 200
                request = captured["request"]
                assert isinstance(request, SelfUpdateRequest)
                assert request.raw_text == "tune the spend cap"
                assert request.requested_by == "ops"
                assert request.approval_token == "tok-123"

    @pytest.mark.asyncio
    async def test_plan_accepts_text_alias_field(self, transport):
        captured: dict[str, object] = {}

        def _capture(request: SelfUpdateRequest) -> SelfUpdatePlan:
            captured["request"] = request
            return _plan()

        with patch(
            "general_ludd.routers.self_update.classify", side_effect=_capture
        ), patch(
            "general_ludd.routers.self_update.apply_plan",
            return_value=ApplyResult(outcome=ApplyOutcome.APPLIED, audit=_audit()),
        ):
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/admin/self-update/plan",
                    json={"text": "bump the spend cap"},
                )
                assert resp.status_code == 200
                assert captured["request"].raw_text == "bump the spend cap"

    @pytest.mark.asyncio
    async def test_plan_is_fail_soft_on_classifier_error(self, transport):
        with patch(
            "general_ludd.routers.self_update.classify",
            side_effect=RuntimeError("classifier blew up"),
        ):
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/admin/self-update/plan",
                    json={"raw_text": "anything"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "error"
                assert "classifier blew up" in data["error"]

    @pytest.mark.asyncio
    async def test_plan_is_fail_soft_on_apply_error(self, transport):
        with patch(
            "general_ludd.routers.self_update.classify", return_value=_plan()
        ), patch(
            "general_ludd.routers.self_update.apply_plan",
            side_effect=ValueError("apply ladder failed"),
        ):
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/admin/self-update/plan",
                    json={"raw_text": "anything"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "error"
                assert "apply ladder failed" in data["error"]


class TestSelfUpdateEnqueueEndpoint:
    @pytest.mark.asyncio
    async def test_enqueue_builds_todo_spec_and_appends(self, transport):
        spec = {
            "title": "self-update: config/value_edit",
            "description": "tune the spend cap",
            "priority": 85,
            "queue": "self_update",
            "work_type": "infra",
            "risk_level": "low",
            "tags": ["self-update", "tier:config"],
            "created_by": "ops",
            "approval_policy": "none",
        }
        from general_ludd.daemon import _daemon_state

        _daemon_state.setdefault("todos", [])
        _daemon_state["todos"].clear()

        with patch(
            "general_ludd.routers.self_update.classify", return_value=_plan()
        ), patch(
            "general_ludd.routers.self_update.to_todo_spec", return_value=spec
        ):
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/admin/self-update/enqueue",
                    json={
                        "raw_text": "tune the spend cap",
                        "requested_by": "ops",
                    },
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "ok"
                assert data["queued"] is True
                assert data["spec"] == spec
                assert spec in _daemon_state["todos"]

    @pytest.mark.asyncio
    async def test_enqueue_passes_project_id_into_spec(self, transport):
        captured: dict[str, object] = {}

        def _capture(
            plan: SelfUpdatePlan, request: SelfUpdateRequest, **kw: object
        ) -> dict[str, object]:
            captured.update(kw)
            return {"title": "x", "priority": 1, "queue": "self_update"}

        with patch(
            "general_ludd.routers.self_update.classify", return_value=_plan()
        ), patch(
            "general_ludd.routers.self_update.to_todo_spec", side_effect=_capture
        ):
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/admin/self-update/enqueue",
                    json={
                        "raw_text": "tune the spend cap",
                        "project_id": "proj-7",
                    },
                )
                assert resp.status_code == 200
                assert captured.get("project_id") == "proj-7"

    @pytest.mark.asyncio
    async def test_enqueue_is_fail_soft_on_classifier_error(self, transport):
        with patch(
            "general_ludd.routers.self_update.classify",
            side_effect=RuntimeError("nope"),
        ):
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/admin/self-update/enqueue",
                    json={"raw_text": "anything"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "error"
                assert "nope" in data["error"]

    @pytest.mark.asyncio
    async def test_enqueue_is_fail_soft_on_spec_error(self, transport):
        with patch(
            "general_ludd.routers.self_update.classify", return_value=_plan()
        ), patch(
            "general_ludd.routers.self_update.to_todo_spec",
            side_effect=KeyError("missing"),
        ):
            async with AsyncClient(
                transport=transport, base_url="http://test"
            ) as client:
                resp = await client.post(
                    "/admin/self-update/enqueue",
                    json={"raw_text": "anything"},
                )
                assert resp.status_code == 200
                data = resp.json()
                assert data["status"] == "error"


class TestSelfUpdateRouterRegistration:
    def test_router_is_registered_on_daemon_app(self, app):
        paths = {route.path for route in app.routes}
        assert "/admin/self-update/plan" in paths
        assert "/admin/self-update/enqueue" in paths
