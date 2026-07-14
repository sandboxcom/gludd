"""Structural tests for routers/features.py — feature database API endpoints."""

from __future__ import annotations

import inspect

from general_ludd.routers.features import (
    _feature_to_dict,
    logger,
    register,
)


class TestModuleImports:
    def test_register_is_callable(self):
        assert callable(register)

    def test_feature_to_dict_is_callable(self):
        assert callable(_feature_to_dict)

    def test_logger_is_logger(self):
        import logging
        assert isinstance(logger, logging.Logger)
        assert logger.name == "general_ludd.routers.features"


class TestRegister:
    def test_registers_feature_routes(self):
        from fastapi import FastAPI
        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        routes = {r.path for r in app.routes}
        assert "/api/features" in routes
        assert "/api/features/{feature_id}" in routes
        assert "/api/features/verify" in routes

    def test_list_features_endpoint_registered(self):
        from fastapi import FastAPI
        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        list_route = next(
            (r for r in all_routes if r.path == "/api/features"), None
        )
        assert list_route is not None
        assert "GET" in list_route.methods

    def test_get_feature_endpoint_registered(self):
        from fastapi import FastAPI
        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        get_route = next(
            (r for r in all_routes if r.path == "/api/features/{feature_id}"), None
        )
        assert get_route is not None
        assert "GET" in get_route.methods

    def test_verify_endpoint_registered(self):
        from fastapi import FastAPI
        app = FastAPI()
        daemon_state: dict[str, object] = {}
        register(app, daemon_state)
        all_routes = [r for r in app.routes if hasattr(r, "methods")]
        verify_route = next(
            (r for r in all_routes if r.path == "/api/features/verify"), None
        )
        assert verify_route is not None
        assert "POST" in verify_route.methods


class TestFeatureToDict:
    def test_accepts_featuremodel_parameter(self):
        sig = inspect.signature(_feature_to_dict)
        assert "feat" in sig.parameters

    def test_returns_dict(self):
        from general_ludd.db.models import FeatureModel, FeatureStatus
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        feat = FeatureModel(
            id="FEAT-001",
            project_id="proj-1",
            name="test feature",
            description="a test",
            category="testing",
            status=FeatureStatus.REQUESTED,
            acceptance_criteria='["must test"]',
            evidence='["evidence_ref"]',
            verifier_kind="inline",
            requested_by="agent",
            requested_at=now,
            verified_at=None,
            last_verify_detail="{}",
        )
        result = _feature_to_dict(feat)
        assert isinstance(result, dict)
        assert result["id"] == "FEAT-001"
        assert result["project_id"] == "proj-1"
        assert result["name"] == "test feature"
        assert result["description"] == "a test"
        assert result["category"] == "testing"
        assert result["status"] == FeatureStatus.REQUESTED
        assert isinstance(result["acceptance_criteria"], list)
        assert isinstance(result["evidence"], list)
        assert result["verifier_kind"] == "inline"
        assert result["requested_by"] == "agent"
        assert result["verified_at"] is None
        assert isinstance(result["last_verify_detail"], dict)

    def test_empty_json_fields_default_correctly(self):
        from general_ludd.db.models import FeatureModel, FeatureStatus
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        feat = FeatureModel(
            id="FEAT-002",
            project_id="proj-2",
            name="empty feature",
            description="",
            category="",
            status=FeatureStatus.IN_PROGRESS,
            acceptance_criteria=None,
            evidence=None,
            verifier_kind=None,
            requested_by="agent",
            requested_at=now,
            verified_at=None,
            last_verify_detail=None,
        )
        result = _feature_to_dict(feat)
        assert result["acceptance_criteria"] == []
        assert result["evidence"] == []
        assert result["last_verify_detail"] == {}

    def test_verified_at_converts_to_string(self):
        from general_ludd.db.models import FeatureModel, FeatureStatus
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        verified = datetime.datetime(2026, 7, 1, 12, 0, 0, tzinfo=datetime.timezone.utc)
        feat = FeatureModel(
            id="FEAT-003",
            project_id="proj-3",
            name="verified feature",
            description="",
            category="testing",
            status=FeatureStatus.VERIFIED,
            acceptance_criteria=None,
            evidence=None,
            verifier_kind="inline",
            requested_by="agent",
            requested_at=now,
            verified_at=verified,
            last_verify_detail=None,
        )
        result = _feature_to_dict(feat)
        assert isinstance(result["verified_at"], str)
        assert result["verified_at"] == str(verified)

    def test_requested_at_converts_to_string(self):
        from general_ludd.db.models import FeatureModel, FeatureStatus
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        feat = FeatureModel(
            id="FEAT-004",
            project_id="proj-4",
            name="timed feature",
            description="",
            category="testing",
            status=FeatureStatus.REQUESTED,
            acceptance_criteria=None,
            evidence=None,
            verifier_kind=None,
            requested_by="agent",
            requested_at=now,
            verified_at=None,
            last_verify_detail=None,
        )
        result = _feature_to_dict(feat)
        assert isinstance(result["requested_at"], str)

    def test_unparseable_json_returns_raw_string(self):
        from general_ludd.db.models import FeatureModel, FeatureStatus
        import datetime
        now = datetime.datetime.now(datetime.timezone.utc)
        feat = FeatureModel(
            id="FEAT-005",
            project_id="proj-5",
            name="bad json feature",
            description="",
            category="testing",
            status=FeatureStatus.REQUESTED,
            acceptance_criteria="not valid json {{",
            evidence="also bad [",
            verifier_kind=None,
            requested_by="agent",
            requested_at=now,
            verified_at=None,
            last_verify_detail=None,
        )
        result = _feature_to_dict(feat)
        assert result["acceptance_criteria"] == "not valid json {{"
        assert result["evidence"] == "also bad ["


class TestRegisterSignature:
    def test_register_accepts_app_and_daemon_state(self):
        sig = inspect.signature(register)
        param_names = list(sig.parameters.keys())
        assert "app" in param_names
        assert "_daemon_state" in param_names
