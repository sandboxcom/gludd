"""Tests for capability guard wiring: check_capability + RequireCapability per-route.

Verifies:
1. check_capability correctly gates on resource + action
2. _psk_admin_default_spec grants all required admin capabilities
3. Denied capabilities are properly checked
4. Missing auth_spec on request.state returns 403
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from general_ludd.security.capability_guard import RequireCapability
from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    PermissionSubject,
    _psk_admin_default_spec,
    check_capability,
)


class TestCheckCapability:
    def test_granted_action_passes(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[
                Capability(resource="admin:account", actions=["delete", "create"]),
            ],
        )
        assert check_capability(spec, "admin:account", "delete") is True

    def test_missing_action_fails(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[
                Capability(resource="admin:account", actions=["read"]),
            ],
        )
        assert check_capability(spec, "admin:account", "delete") is False

    def test_unknown_resource_fails(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[],
        )
        assert check_capability(spec, "admin:account", "delete") is False

    def test_different_resource_fails(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[
                Capability(resource="admin:other", actions=["delete"]),
            ],
        )
        assert check_capability(spec, "admin:account", "delete") is False

    def test_denied_resource_rejected(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[
                Capability(resource="admin:account", actions=["delete", "create"]),
            ],
            denied=[
                Capability(resource="admin:account", actions=["delete"]),
            ],
        )
        assert check_capability(spec, "admin:account", "delete") is False
        assert check_capability(spec, "admin:account", "create") is True

    def test_denied_with_empty_actions_blocks_all(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[
                Capability(resource="admin:account", actions=["delete", "create"]),
            ],
            denied=[
                Capability(resource="admin:account", actions=[]),
            ],
        )
        assert check_capability(spec, "admin:account", "delete") is False
        assert check_capability(spec, "admin:account", "create") is False


class TestPskAdminDefaultSpec:
    def test_grants_all_admin_capabilities(self):
        spec = _psk_admin_default_spec()
        assert spec.agent_type == "psk-admin"
        assert spec.subject == PermissionSubject.HUMAN

        granted: dict[str, set[str]] = {}
        for cap in spec.capabilities:
            granted.setdefault(cap.resource, set()).update(cap.actions)

        assert "delete" in granted.get("admin:account", set())
        assert "create" in granted.get("admin:account", set())
        assert "cleanup" in granted.get("admin:account", set())
        assert "backup" in granted.get("admin:account", set())
        assert "revoke" in granted.get("admin:sts", set())
        assert "write" in granted.get("admin:permissions", set())
        assert "destroy" in granted.get("admin:compute", set())

    def test_psk_admin_passes_check_capability(self):
        spec = _psk_admin_default_spec()
        assert check_capability(spec, "admin:sts", "revoke") is True
        assert check_capability(spec, "admin:compute", "destroy") is True
        assert check_capability(spec, "admin:permissions", "write") is True
        assert check_capability(spec, "admin:account", "delete") is True
        assert check_capability(spec, "admin:account", "create") is True
        assert check_capability(spec, "admin:account", "cleanup") is True

    def test_psk_admin_denied_unknown_action(self):
        spec = _psk_admin_default_spec()
        assert check_capability(spec, "admin:account", "superadmin") is False
        assert check_capability(spec, "unknown:resource", "read") is False


class TestRequireCapabilityIntegration:
    @pytest.fixture
    def app(self):
        app = FastAPI()
        guard = RequireCapability(resource="admin:sts", action="revoke")

        @app.post("/admin/sts/revoke", dependencies=[Depends(guard)])
        async def sts_revoke():
            return {"revoked": True}

        return app

    def test_no_auth_spec_returns_403(self, app):
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/admin/sts/revoke", content=b"{}")

        assert resp.status_code == 403
        assert resp.json()["detail"] == {
            "error": "forbidden: no_auth_spec",
            "required": "admin:sts:revoke",
        }

    def test_insufficient_capability_returns_403(self, app):
        @app.middleware("http")
        async def restricted_auth(request: Request, call_next):
            request.state.auth_spec = PermissionSpec(
                agent_type="restricted",
                capabilities=[
                    Capability(resource="admin:sts", actions=["list"]),
                ],
            )
            return await call_next(request)

        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/admin/sts/revoke", content=b"{}")

        assert resp.status_code == 403
        assert resp.json()["detail"] == {
            "error": "forbidden: insufficient_capability",
            "required": "admin:sts:revoke",
        }

    def test_matching_capability_allows_request(self, app):
        @app.middleware("http")
        async def authorized(request: Request, call_next):
            request.state.auth_spec = PermissionSpec(
                agent_type="operator",
                capabilities=[
                    Capability(resource="admin:sts", actions=["revoke"]),
                ],
            )
            return await call_next(request)

        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/admin/sts/revoke", content=b"{}")

        assert resp.status_code == 200
        assert resp.json() == {"revoked": True}
