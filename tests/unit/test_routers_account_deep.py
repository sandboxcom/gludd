"""Deep tests for routers/account.py — all 5 endpoints, error paths, models."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from general_ludd.routers.account import (
    BackupRequest,
    CreateAccountRequest,
    DeleteRequest,
    register,
)
from general_ludd.security.permissions import Capability, PermissionSpec


def _authorize_admin(app: FastAPI) -> None:
    """Attach a real admin capability so endpoint tests exercise the guard."""
    spec = PermissionSpec(
        agent_type="test-admin",
        capabilities=[
            Capability(
                resource="admin:account",
                actions=["backup", "delete", "create", "cleanup"],
            )
        ],
    )

    @app.middleware("http")
    async def _attach_auth_spec(request: Request, call_next: Any) -> Any:
        request.state.auth_spec = spec
        return await call_next(request)


# ── Request model validation ────────────────────────────────────────────────


class TestRequestModels:
    def test_backup_request_valid(self):
        req: BackupRequest = BackupRequest(user_id="u1")
        assert req.user_id == "u1"

    def test_backup_request_empty_user_id_rejected(self):
        with pytest.raises(ValidationError):
            BackupRequest(user_id="")

    def test_delete_request_confirm_false_default(self):
        req: DeleteRequest = DeleteRequest(user_id="u1")
        assert req.confirm is False
        assert req.user_id == "u1"

    def test_delete_request_confirm_true(self):
        req: DeleteRequest = DeleteRequest(user_id="u1", confirm=True)
        assert req.confirm is True

    def test_delete_request_user_id_required(self):
        with pytest.raises(ValidationError):
            DeleteRequest()

    def test_create_account_request_ephemeral_default_false(self):
        req: CreateAccountRequest = CreateAccountRequest(provider="aws")
        assert req.ephemeral is False
        assert req.budget == 10.0

    def test_create_account_request_ephemeral_true(self):
        req: CreateAccountRequest = CreateAccountRequest(provider="aws", ephemeral=True, budget=5.0)
        assert req.ephemeral is True
        assert req.budget == 5.0

    def test_create_account_budget_nonnegative(self):
        req: CreateAccountRequest = CreateAccountRequest(provider="aws", budget=0.0)
        assert req.budget == 0.0

    def test_create_account_negative_budget_rejected(self):
        with pytest.raises(ValidationError):
            CreateAccountRequest(provider="aws", budget=-1.0)

    def test_create_account_empty_provider_rejected(self):
        with pytest.raises(ValidationError):
            CreateAccountRequest(provider="")


# ── register() wiring ───────────────────────────────────────────────────────


class TestRegister:
    def test_register_adds_all_routes(self):
        app: FastAPI = FastAPI()
        register(app, {})
        routes: list[str] = [str(r.path) for r in app.routes]
        assert "/api/account/backup" in routes
        assert "/api/account" in routes
        assert "/api/account/policy" in routes
        assert "/api/account/create" in routes
        assert "/api/account/cleanup" in routes


# ── POST /api/account/backup ────────────────────────────────────────────────


class TestAccountBackup:
    @pytest.fixture
    def client_no_db(self) -> TestClient:
        app: FastAPI = FastAPI()
        _authorize_admin(app)
        with patch("general_ludd.routers.account._get_session_factory", return_value=None):
            register(app, {})
        return TestClient(app)

    def test_backup_no_database_503(self, client_no_db: TestClient) -> None:
        resp: Any = client_no_db.post("/api/account/backup", json={"user_id": "testuser"})
        assert resp.status_code == 503
        assert "No database available" in resp.json()["detail"]

    @pytest.fixture
    def client_with_db(self) -> TestClient:
        app: FastAPI = FastAPI()
        _authorize_admin(app)
        mock_factory: MagicMock = MagicMock()
        app.state._session_factory = mock_factory
        register(app, {})
        return TestClient(app)

    def test_backup_value_error_422(self, client_with_db: TestClient) -> None:
        with patch("general_ludd.routers.account._export_user_data") as mock_export:
            mock_export.side_effect = ValueError("no such user")
            resp: Any = client_with_db.post("/api/account/backup", json={"user_id": "baduser"})
        assert resp.status_code == 422
        assert "no such user" in resp.json()["detail"]

    def test_backup_generic_exception_500(self, client_with_db: TestClient) -> None:
        with patch("general_ludd.routers.account._export_user_data") as mock_export:
            mock_export.side_effect = RuntimeError("db down")
            resp: Any = client_with_db.post("/api/account/backup", json={"user_id": "u1"})
        assert resp.status_code == 500
        assert "backup failed" in resp.json()["detail"]


# ── DELETE /api/account ─────────────────────────────────────────────────────


class TestAccountDelete:
    @pytest.fixture
    def client_with_db(self) -> TestClient:
        app: FastAPI = FastAPI()
        _authorize_admin(app)
        mock_factory: MagicMock = MagicMock()
        app.state._session_factory = mock_factory
        register(app, {})
        return TestClient(app)

    def test_delete_requires_confirm(self, client_with_db: TestClient) -> None:
        resp: Any = client_with_db.request(
            "DELETE",
            "/api/account",
            json={"user_id": "u1", "confirm": False},
        )
        assert resp.status_code == 400
        assert "confirm=true is required" in resp.json()["detail"]

    def test_delete_no_database_503(self) -> None:
        app: FastAPI = FastAPI()
        _authorize_admin(app)
        with patch("general_ludd.routers.account._get_session_factory", return_value=None):
            register(app, {})
        client: TestClient = TestClient(app)
        resp: Any = client.request(
            "DELETE",
            "/api/account",
            json={"user_id": "u1", "confirm": True},
        )
        assert resp.status_code == 503
        assert "No database available" in resp.json()["detail"]

    def test_delete_value_error_422(self, client_with_db: TestClient) -> None:
        with patch("general_ludd.routers.account._delete_user_data") as mock_del:
            mock_del.side_effect = ValueError("user not found")
            resp: Any = client_with_db.request(
                "DELETE",
                "/api/account",
                json={"user_id": "noone", "confirm": True},
            )
        assert resp.status_code == 422
        assert "user not found" in resp.json()["detail"]

    def test_delete_generic_exception_500(self, client_with_db: TestClient) -> None:
        with patch("general_ludd.routers.account._delete_user_data") as mock_del:
            mock_del.side_effect = RuntimeError("borked")
            resp: Any = client_with_db.request(
                "DELETE",
                "/api/account",
                json={"user_id": "u1", "confirm": True},
            )
        assert resp.status_code == 500
        assert "delete failed" in resp.json()["detail"]


# ── GET /api/account/policy ─────────────────────────────────────────────────


class TestAccountPolicy:
    @pytest.fixture
    def client(self) -> TestClient:
        app: FastAPI = FastAPI()
        register(app, {})
        return TestClient(app)

    def test_policy_valid_service_returns_text(self, client: TestClient) -> None:
        resp: Any = client.get("/api/account/policy", params={"service": "aws"})
        assert resp.status_code == 200
        data: dict[str, object] = resp.json()
        assert data["service"] != ""
        assert data["policy"] != ""
        assert "notice" in data

    def test_policy_invalid_service_422(self, client: TestClient) -> None:
        resp: Any = client.get("/api/account/policy", params={"service": "nonexistent_service_xyz"})
        assert resp.status_code == 422
        assert "supported services" in resp.json()["detail"].lower()

    def test_policy_service_normalizes_display_name(self, client: TestClient) -> None:
        for svc in ("openai", "deepseek", "gcp"):
            resp: Any = client.get("/api/account/policy", params={"service": svc})
            assert resp.status_code == 200
            data: dict[str, object] = resp.json()
            assert isinstance(data["service"], str)
            assert len(data["service"]) > 0


# ── POST /api/account/create ────────────────────────────────────────────────


class TestAccountCreate:
    @pytest.fixture
    def client(self) -> TestClient:
        app: FastAPI = FastAPI()
        _authorize_admin(app)
        register(app, {})
        return TestClient(app)

    def test_create_unsupported_provider_422(self, client: TestClient) -> None:
        resp: Any = client.post(
            "/api/account/create",
            json={"provider": "linode", "ephemeral": True},
        )
        assert resp.status_code == 422
        assert "unsupported provider" in resp.json()["detail"]

    def test_create_non_ephemeral_501(self, client: TestClient) -> None:
        resp: Any = client.post(
            "/api/account/create",
            json={"provider": "aws", "ephemeral": False},
        )
        assert resp.status_code == 501
        assert "ephemeral=true" in resp.json()["detail"]

    def test_create_no_manager_wired_503(self) -> None:
        app: FastAPI = FastAPI()
        _authorize_admin(app)
        register(app, {})
        client: TestClient = TestClient(app)
        resp: Any = client.post(
            "/api/account/create",
            json={"provider": "aws", "ephemeral": True},
        )
        assert resp.status_code == 503
        assert "ephemeral account manager not wired" in resp.json()["detail"]

    def test_create_value_error_422(self, client: TestClient) -> None:
        mock_mgr: MagicMock = MagicMock()
        mock_mgr.create_account.side_effect = ValueError("budget too low")
        client.app.state._ephemeral_account_manager = mock_mgr

        resp: Any = client.post(
            "/api/account/create",
            json={"provider": "gcp", "ephemeral": True, "budget": 0.01},
        )
        assert resp.status_code == 422
        assert "budget too low" in resp.json()["detail"]

    def test_create_generic_exception_500(self, client: TestClient) -> None:
        mock_mgr: MagicMock = MagicMock()
        mock_mgr.create_account.side_effect = RuntimeError("cloud api down")
        client.app.state._ephemeral_account_manager = mock_mgr

        resp: Any = client.post(
            "/api/account/create",
            json={"provider": "azure", "ephemeral": True},
        )
        assert resp.status_code == 500
        assert "create failed" in resp.json()["detail"]

    def test_create_ephemeral_success_returns_credentials(self, client: TestClient) -> None:
        fake_creds: MagicMock = MagicMock()
        fake_creds.account_id = "acct-123"
        fake_creds.provider = "aws"
        fake_creds.access_key_id = "AKIAIOSFODNN7EXAMPLE"
        fake_creds.budget_limit = 10.0

        mock_mgr: MagicMock = MagicMock()
        mock_mgr.create_account.return_value = fake_creds
        client.app.state._ephemeral_account_manager = mock_mgr

        resp: Any = client.post(
            "/api/account/create",
            json={"provider": "aws", "ephemeral": True, "budget": 10.0},
        )
        assert resp.status_code == 200
        data: dict[str, object] = resp.json()
        assert data["account_id"] == "acct-123"
        assert data["provider"] == "aws"
        assert data["access_key_id"] == "AKIAIOSFODNN7EXAMPLE"
        assert data["ephemeral"] is True


# ── POST /api/account/cleanup ───────────────────────────────────────────────


class TestAccountCleanup:
    @pytest.fixture
    def client(self) -> TestClient:
        app: FastAPI = FastAPI()
        _authorize_admin(app)
        register(app, {})
        return TestClient(app)

    def test_cleanup_no_manager_503(self, client: TestClient) -> None:
        resp: Any = client.post("/api/account/cleanup")
        assert resp.status_code == 503
        assert "ephemeral account manager not wired" in resp.json()["detail"]

    def test_cleanup_success_returns_report(self, client: TestClient) -> None:
        mock_mgr: MagicMock = MagicMock()
        mock_mgr.cleanup_expired.return_value = {
            "deleted": ["acct-1"],
            "kept": ["acct-2"],
        }
        client.app.state._ephemeral_account_manager = mock_mgr

        resp: Any = client.post("/api/account/cleanup")
        assert resp.status_code == 200
        data: dict[str, object] = resp.json()
        assert data["deleted"] == ["acct-1"]
        assert data["kept"] == ["acct-2"]

    def test_cleanup_generic_exception_500(self, client: TestClient) -> None:
        mock_mgr: MagicMock = MagicMock()
        mock_mgr.cleanup_expired.side_effect = RuntimeError("db is gone")
        client.app.state._ephemeral_account_manager = mock_mgr

        resp: Any = client.post("/api/account/cleanup")
        assert resp.status_code == 500
        assert "cleanup failed" in resp.json()["detail"]
