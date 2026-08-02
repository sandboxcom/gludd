"""Integration tests for STS daemon endpoints (mint, validate, revoke).

Tests the FastAPI endpoints registered by routers/sts.py against a mocked
TokenStore + TokenMinter + TokenRevoker pipeline. Does not require OpenBao.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from general_ludd.daemon import create_daemon_app
from general_ludd.db.models import AgentTokenModel
from general_ludd.sts.reaper import TokenReaper
from general_ludd.sts.revoker import TokenRevoker
from general_ludd.sts.store import TokenStore


def _token_record(
    token_id: str = "tok-agent-1",
    agent_id: str = "agent-1",
    parent_agent_id: str = "root",
    role_name: str = "agent-agent-1",
    role_id: str = "role-1",
    revoked_at: datetime | None = None,
) -> AgentTokenModel:
    return AgentTokenModel(
        token_id=token_id,
        agent_id=agent_id,
        parent_agent_id=parent_agent_id,
        role_name=role_name,
        role_id=role_id,
        scope_hash="",
        created_at=datetime(2026, 7, 15, tzinfo=UTC),
        expires_at=None,
        revoked_at=revoked_at,
        hydration_count=0,
    )


class TestStsEndpoints:
    """STS endpoint integration tests with mocked backend."""

    @pytest.fixture
    def client(self) -> TestClient:
        import os

        os.environ["GLUDD_PSK_DISABLE"] = "1"
        app = create_daemon_app(tick_interval=0.1)
        app.state._no_auth = True
        app.state._allow_no_auth = True

        store = AsyncMock(spec=TokenStore)
        revoker = AsyncMock(spec=TokenRevoker)

        secrets_mgr = MagicMock()
        creds = MagicMock()
        creds.role_id = "role-mock-1"
        creds.secret_id = "secret-mock-1"
        secrets_mgr.setup_approle.return_value = creds
        app.state._secrets_resolver = secrets_mgr

        reaper = MagicMock(spec=TokenReaper)
        reaper._store = store
        reaper._revoker = revoker

        app.state.daemon_state["_sts_reaper"] = reaper

        return TestClient(app, raise_server_exceptions=False)

    # ------------------------------------------------------------------ mint --

    def test_mint_creates_token_and_returns_record(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/sts/mint",
            json={"agent_id": "agent-1", "parent_agent_id": "root"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["token_id"] == "tok-agent-1"
        assert data["agent_id"] == "agent-1"
        assert data["role_id"] == "role-mock-1"
        assert "created_at" in data

    def test_mint_requires_agent_id(self, client: TestClient) -> None:
        resp = client.post("/admin/sts/mint", json={"parent_agent_id": "root"})
        assert resp.status_code == 422

    def test_mint_returns_503_when_secrets_not_wired(self, client: TestClient) -> None:
        client.app.state._secrets_resolver = None
        resp = client.post(
            "/admin/sts/mint",
            json={"agent_id": "agent-2", "parent_agent_id": "root"},
        )
        assert resp.status_code == 503

    # -------------------------------------------------------------- validate --

    def test_validate_valid_token(self, client: TestClient) -> None:
        store = client.app.state.daemon_state["_sts_reaper"]._store
        store.get = AsyncMock(
            return_value=_token_record(
                agent_id="agent-1",
                token_id="tok-agent-1",
                revoked_at=None,
            )
        )
        resp = client.get("/admin/sts/validate/agent-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["token_id"] == "tok-agent-1"
        assert data["revoked"] is False

    def test_validate_revoked_token(self, client: TestClient) -> None:
        store = client.app.state.daemon_state["_sts_reaper"]._store
        store.get = AsyncMock(
            return_value=_token_record(
                agent_id="agent-2",
                token_id="tok-agent-2",
                revoked_at=datetime(2026, 7, 15, tzinfo=UTC),
            )
        )
        resp = client.get("/admin/sts/validate/agent-2")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert data["revoked"] is True
        assert data["revoked_at"] is not None

    def test_validate_missing_token(self, client: TestClient) -> None:
        store = client.app.state.daemon_state["_sts_reaper"]._store
        store.get = AsyncMock(return_value=None)
        resp = client.get("/admin/sts/validate/ghost-agent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert data["token_id"] == ""

    # --------------------------------------------------------------- revoke --

    def test_revoke_completed_succeeds(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/sts/revoke/agent-1",
            json={"terminal_state": "completed"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "revoked"
        revoker = client.app.state.daemon_state["_sts_reaper"]._revoker
        revoker.revoke.assert_awaited_once_with("agent-1", terminal_state="completed")

    def test_revoke_defaults_to_completed(self, client: TestClient) -> None:
        resp = client.post("/admin/sts/revoke/agent-1", json={})
        assert resp.status_code == 200
        revoker = client.app.state.daemon_state["_sts_reaper"]._revoker
        revoker.revoke.assert_awaited_once_with("agent-1", terminal_state="completed")

    def test_revoke_bad_terminal_state_returns_400(self, client: TestClient) -> None:
        revoker = client.app.state.daemon_state["_sts_reaper"]._revoker
        revoker.revoke.side_effect = ValueError("unsupported STS terminal state")
        resp = client.post(
            "/admin/sts/revoke/agent-1",
            json={"terminal_state": "bogus"},
        )
        assert resp.status_code == 400

    # ---------------------------------------------------------- get / list --

    def test_get_single_token(self, client: TestClient) -> None:
        store = client.app.state.daemon_state["_sts_reaper"]._store
        store.get = AsyncMock(return_value=_token_record(agent_id="agent-1", token_id="tok-agent-1"))
        resp = client.get("/admin/sts/tokens/agent-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == "agent-1"
        assert data["token_id"] == "tok-agent-1"
        assert data["hydration_count"] == 0

    def test_get_token_not_found(self, client: TestClient) -> None:
        store = client.app.state.daemon_state["_sts_reaper"]._store
        store.get = AsyncMock(return_value=None)
        resp = client.get("/admin/sts/tokens/ghost-agent")
        assert resp.status_code == 404

    def test_list_all_tokens(self, client: TestClient) -> None:
        store = client.app.state.daemon_state["_sts_reaper"]._store
        store.list_all = AsyncMock(
            return_value=[
                _token_record("tok-a", "agent-a"),
                _token_record("tok-b", "agent-b"),
            ]
        )
        resp = client.get("/admin/sts/tokens")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        assert data[0]["token_id"] == "tok-a"
        assert data[1]["token_id"] == "tok-b"

    def test_list_empty(self, client: TestClient) -> None:
        store = client.app.state.daemon_state["_sts_reaper"]._store
        store.list_all = AsyncMock(return_value=[])
        resp = client.get("/admin/sts/tokens")
        assert resp.status_code == 200
        assert resp.json() == []

    # ---------------------------------------------------------------- 503 ---

    def test_validate_returns_503_when_store_not_wired(self, client: TestClient) -> None:
        client.app.state.daemon_state["_sts_reaper"] = None
        resp = client.get("/admin/sts/validate/agent-1")
        assert resp.status_code == 503

    def test_revoke_returns_503_when_revoker_not_wired(self, client: TestClient) -> None:
        client.app.state.daemon_state["_sts_reaper"] = None
        resp = client.post("/admin/sts/revoke/agent-1", json={})
        assert resp.status_code == 503
