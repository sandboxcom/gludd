"""Structural tests for routers/security.py — STS token lifecycle + permission-spec endpoints."""

from __future__ import annotations

import logging

from fastapi import FastAPI


class TestModuleImports:
    def test_module_can_be_imported(self):
        import general_ludd.routers.security

        assert general_ludd.routers.security is not None

    def test_register_is_callable(self):
        from general_ludd.routers.security import register

        assert callable(register)

    def test_logger_exists(self):
        from general_ludd.routers.security import logger

        assert isinstance(logger, logging.Logger)
        assert logger.name == "general_ludd.routers.security"


class TestHelperFunctions:
    def test_get_issuer_exists(self):
        from general_ludd.routers.security import _get_issuer

        assert callable(_get_issuer)

    def test_get_issuer_creates_on_missing_state(self):
        from general_ludd.routers.security import _get_issuer

        app = FastAPI()
        issuer = _get_issuer(app)
        assert issuer is not None
        assert app.state._sts_issuer is issuer

    def test_get_audit_log_exists(self):
        from general_ludd.routers.security import _get_audit_log

        assert callable(_get_audit_log)

    def test_get_audit_log_creates_on_missing_state(self):
        from general_ludd.routers.security import _get_audit_log

        app = FastAPI()
        audit_log = _get_audit_log(app)
        assert audit_log is not None
        assert app.state._sts_audit_log is audit_log

    def test_get_issuer_spec_exists(self):
        from general_ludd.routers.security import _get_issuer_spec

        assert callable(_get_issuer_spec)

    def test_get_human_spec_exists(self):
        from general_ludd.routers.security import _get_human_spec

        assert callable(_get_human_spec)

    def test_get_esc_store_exists(self):
        from general_ludd.routers.security import _get_esc_store

        assert callable(_get_esc_store)

    def test_esc_counter_exists(self):
        from general_ludd.routers.security import _esc_counter

        assert callable(_esc_counter)

    def test_caps_to_yaml_exists(self):
        from general_ludd.routers.security import _caps_to_yaml

        assert callable(_caps_to_yaml)

    def test_caps_from_yaml_exists(self):
        from general_ludd.routers.security import _caps_from_yaml

        assert callable(_caps_from_yaml)

    def test_spec_from_caps_yaml_exists(self):
        from general_ludd.routers.security import _spec_from_caps_yaml

        assert callable(_spec_from_caps_yaml)

    def test_is_strict_subset_of_both_exists(self):
        from general_ludd.routers.security import _is_strict_subset_of_both

        assert callable(_is_strict_subset_of_both)

    def test_perms_dir_exists(self):
        from general_ludd.routers.security import _perms_dir

        assert callable(_perms_dir)

    def test_find_esc_exists_as_local_function(self):
        from general_ludd.routers.security import register

        app = FastAPI()
        register(app, {})


class TestCapsHelpers:
    def test_caps_to_yaml_produces_string(self):
        from general_ludd.routers.security import _caps_to_yaml

        caps: list[dict[str, object]] = [
            {"resource": "s3", "actions": ["read", "write"], "constraints": {}}
        ]
        result = _caps_to_yaml(caps)
        assert isinstance(result, str)
        assert "s3" in result

    def test_caps_from_yaml_roundtrip(self):
        from general_ludd.routers.security import _caps_from_yaml, _caps_to_yaml

        caps: list[dict[str, object]] = [
            {"resource": "s3", "actions": ["read"], "constraints": {"region": "us-east-1"}}
        ]
        yaml_str = _caps_to_yaml(caps)
        parsed = _caps_from_yaml(yaml_str)
        assert len(parsed) == 1
        assert parsed[0].resource == "s3"
        assert "read" in parsed[0].actions

    def test_caps_from_yaml_handles_empty(self):
        from general_ludd.routers.security import _caps_from_yaml

        parsed = _caps_from_yaml("[]")
        assert parsed == []

    def test_is_strict_subset_returns_false_for_empty(self):
        from general_ludd.routers.security import _is_strict_subset_of_both
        from general_ludd.security.permissions import (
            PermissionSpec,
        )

        empty_spec = PermissionSpec(agent_type="a", capabilities=[])
        result = _is_strict_subset_of_both([], empty_spec, empty_spec)
        assert result is False


class TestRegister:
    def test_registers_sts_routes(self):
        from general_ludd.routers.security import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        routes = {r.path for r in app.routes}
        assert "/admin/sts/issue" in routes
        assert "/admin/sts/active" in routes
        assert "/admin/sts/revoke" in routes
        assert "/admin/sts/audit" in routes

    def test_registers_permission_routes(self):
        from general_ludd.routers.security import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        routes = {r.path for r in app.routes}
        assert "/admin/perm/spec" in routes
        assert "/admin/perm/spec/{agent_type}" in routes
        assert "/admin/perm/escalation-request" in routes
        assert "/admin/perm/escalations" in routes
        assert "/admin/perm/escalations/history" in routes
        assert "/admin/perm/escalations/{esc_id}/approve" in routes
        assert "/admin/perm/escalations/{esc_id}/deny" in routes

    def test_sts_issue_endpoint_post_registered(self):
        from general_ludd.routers.security import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        issue_route = next(
            (r for r in all_routes if r.path == "/admin/sts/issue"), None
        )
        assert issue_route is not None
        assert "POST" in issue_route.methods

    def test_sts_active_endpoint_get_registered(self):
        from general_ludd.routers.security import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        active_route = next(
            (r for r in all_routes if r.path == "/admin/sts/active"), None
        )
        assert active_route is not None
        assert "GET" in active_route.methods

    def test_sts_revoke_endpoint_post_registered(self):
        from general_ludd.routers.security import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        revoke_route = next(
            (r for r in all_routes if r.path == "/admin/sts/revoke"), None
        )
        assert revoke_route is not None
        assert "POST" in revoke_route.methods

    def test_sts_audit_endpoint_get_registered(self):
        from general_ludd.routers.security import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        audit_route = next(
            (r for r in all_routes if r.path == "/admin/sts/audit"), None
        )
        assert audit_route is not None
        assert "GET" in audit_route.methods

    def test_escalation_request_endpoint_post_registered(self):
        from general_ludd.routers.security import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        esc_route = next(
            (r for r in all_routes if r.path == "/admin/perm/escalation-request"),
            None,
        )
        assert esc_route is not None
        assert "POST" in esc_route.methods

    def test_escalations_list_endpoint_get_registered(self):
        from general_ludd.routers.security import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        esc_list_route = next(
            (r for r in all_routes if r.path == "/admin/perm/escalations"), None
        )
        assert esc_list_route is not None
        assert "GET" in esc_list_route.methods

    def test_perm_spec_get_endpoint_registered(self):
        from general_ludd.routers.security import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        spec_routes = [
            r for r in all_routes
            if r.path == "/admin/perm/spec/{agent_type}"
        ]
        assert spec_routes
        methods = {
            method
            for route in spec_routes
            for method in route.methods
        }
        assert {"GET", "PUT"} <= methods

    def test_escalation_approve_endpoint_post_registered(self):
        from general_ludd.routers.security import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        approve_route = next(
            (
                r
                for r in all_routes
                if r.path == "/admin/perm/escalations/{esc_id}/approve"
            ),
            None,
        )
        assert approve_route is not None
        assert "POST" in approve_route.methods

    def test_escalation_deny_endpoint_post_registered(self):
        from general_ludd.routers.security import register

        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        deny_route = next(
            (
                r
                for r in all_routes
                if r.path == "/admin/perm/escalations/{esc_id}/deny"
            ),
            None,
        )
        assert deny_route is not None
        assert "POST" in deny_route.methods
