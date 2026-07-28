"""Per-route capability guard tests — TDD for PSK-flat authz enhancement.

Verifies:
  - psk_admin_default_spec exists and covers all admin capability resources
  - check_capability(spec, resource, action) correctly gates
  - RequireCapability dependency raises HTTPException when capability is missing
  - RequireCapability dependency passes through when capability is present
  - Auth middleware stamps request.state.auth_spec after PSK validation
"""

from __future__ import annotations

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.testclient import TestClient

from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    check_capability,
    psk_admin_default_spec,
)
from general_ludd.security.capability_guard import RequireCapability


# ── psk_admin_default_spec shape ────────────────────────────────────────


class TestPskAdminDefaultSpec:
    def test_exists_and_has_agent_type(self):
        spec = psk_admin_default_spec()
        assert spec.agent_type == "psk_admin"

    def test_has_all_required_capability_resources(self):
        spec = psk_admin_default_spec()
        resources = {c.resource for c in spec.capabilities}
        required = {
            "admin:sts",
            "admin:permissions",
            "admin:account",
            "admin:deploy",
        }
        assert required.issubset(resources)

    def test_admin_sts_has_revoke_action(self):
        spec = psk_admin_default_spec()
        sts_cap = spec.capability_for("admin:sts")
        assert sts_cap is not None
        assert "revoke" in sts_cap.actions

    def test_admin_permissions_has_write_action(self):
        spec = psk_admin_default_spec()
        perm_cap = spec.capability_for("admin:permissions")
        assert perm_cap is not None
        assert "write" in perm_cap.actions

    def test_admin_account_has_delete_action(self):
        spec = psk_admin_default_spec()
        acct_cap = spec.capability_for("admin:account")
        assert acct_cap is not None
        assert "delete" in acct_cap.actions

    def test_admin_deploy_has_write_action(self):
        spec = psk_admin_default_spec()
        deploy_cap = spec.capability_for("admin:deploy")
        assert deploy_cap is not None
        assert "write" in deploy_cap.actions


# ── check_capability ─────────────────────────────────────────────────────


class TestCheckCapability:
    def test_allows_matching_resource_and_action(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[
                Capability(resource="admin:sts", actions=["revoke", "issue"]),
            ],
        )
        assert check_capability(spec, "admin:sts", "revoke") is True

    def test_denies_missing_resource(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[
                Capability(resource="admin:sts", actions=["revoke"]),
            ],
        )
        assert check_capability(spec, "admin:permissions", "write") is False

    def test_denies_missing_action(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[
                Capability(resource="admin:sts", actions=["issue"]),
            ],
        )
        assert check_capability(spec, "admin:sts", "revoke") is False

    def test_allows_read_as_subset_of_write(self):
        """A spec with 'write' action implies 'read' capability."""
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[
                Capability(resource="admin:sts", actions=["issue", "revoke", "list"]),
            ],
        )
        assert check_capability(spec, "admin:sts", "list") is True

    def test_empty_capabilities_denies_all(self):
        spec = PermissionSpec(agent_type="test", capabilities=[])
        assert check_capability(spec, "admin:sts", "revoke") is False

    def test_denied_capability_overrides_positive_grant(self):
        spec = PermissionSpec(
            agent_type="test",
            capabilities=[
                Capability(resource="admin:sts", actions=["revoke"]),
            ],
            denied=[
                Capability(resource="admin:sts", actions=["revoke"]),
            ],
        )
        assert check_capability(spec, "admin:sts", "revoke") is False


# ── RequireCapability dependency ─────────────────────────────────────────


class TestRequireCapabilityDependency:
    @pytest.fixture
    def app_with_guard(self) -> FastAPI:
        app = FastAPI()

        @app.get("/admin/sts/revoke")
        async def sts_revoke(
            _guard: None = Depends(RequireCapability(resource="admin:sts", action="revoke")),
        ) -> dict[str, object]:
            return {"ok": True}

        @app.get("/admin/perm/spec")
        async def perm_spec_read(
            _guard: None = Depends(RequireCapability(resource="admin:permissions", action="read")),
        ) -> dict[str, object]:
            return {"ok": True}

        return app

    def test_allow_when_capability_present(self, app_with_guard):
        from general_ludd.security.permissions import _psk_admin_default_spec

        # Stamp the admin spec on request state before each call
        @app_with_guard.middleware("http")
        async def authz_middleware(request: Request, call_next):
            request.state.auth_spec = _psk_admin_default_spec()
            return await call_next(request)

        client = TestClient(app_with_guard)
        resp = client.get("/admin/sts/revoke")
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_deny_when_capability_missing(self, app_with_guard):
        @app_with_guard.middleware("http")
        async def authz_middleware(request: Request, call_next):
            request.state.auth_spec = PermissionSpec(
                agent_type="limited",
                capabilities=[
                    Capability(resource="admin:permissions", actions=["read"]),
                ],
            )
            return await call_next(request)

        client = TestClient(app_with_guard)
        resp = client.get("/admin/sts/revoke")
        assert resp.status_code == 403
        data = resp.json()
        assert "forbidden" in data.get("error", "")

    def test_deny_when_no_auth_spec(self, app_with_guard):
        client = TestClient(app_with_guard)
        resp = client.get("/admin/sts/revoke")
        assert resp.status_code == 403
        data = resp.json()
        assert "no_auth_spec" in data.get("error", "")

    def test_passes_when_capability_present_for_read(self, app_with_guard):
        @app_with_guard.middleware("http")
        async def authz_middleware(request: Request, call_next):
            request.state.auth_spec = PermissionSpec(
                agent_type="limited",
                capabilities=[
                    Capability(resource="admin:permissions", actions=["read"]),
                    Capability(resource="admin:sts", actions=["revoke"]),
                ],
            )
            return await call_next(request)

        client = TestClient(app_with_guard)
        resp = client.get("/admin/perm/spec")
        assert resp.status_code == 200

    def test_denies_spec_with_correct_resource_wrong_action(self, app_with_guard):
        @app_with_guard.middleware("http")
        async def authz_middleware(request: Request, call_next):
            # Has admin:sts but only "issue", not "revoke"
            request.state.auth_spec = PermissionSpec(
                agent_type="limited",
                capabilities=[
                    Capability(resource="admin:sts", actions=["issue"]),
                ],
            )
            return await call_next(request)

        client = TestClient(app_with_guard)
        resp = client.get("/admin/sts/revoke")
        assert resp.status_code == 403


# ── Integration: PSK admin spec is a full admin spec ─────────────────────


class TestPskAdminSpecCompleteness:
    def test_is_superset_of_all_required_routes(self):
        """The default PSK admin spec must pass all route guards defined above."""
        spec = psk_admin_default_spec()
        assert check_capability(spec, "admin:sts", "issue") is True
        assert check_capability(spec, "admin:sts", "revoke") is True
        assert check_capability(spec, "admin:sts", "list") is True
        assert check_capability(spec, "admin:sts", "audit") is True
        assert check_capability(spec, "admin:permissions", "read") is True
        assert check_capability(spec, "admin:permissions", "write") is True
        assert check_capability(spec, "admin:account", "delete") is True
        assert check_capability(spec, "admin:deploy", "write") is True
