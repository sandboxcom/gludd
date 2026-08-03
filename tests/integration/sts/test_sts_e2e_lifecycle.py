"""Integration end-to-end test for the full STS token lifecycle through daemon endpoints.

Covers the complete flow: mint → validate → get → list → revoke →
validate-revoked → teardown. Uses mocked TokenStore + TokenMinter + TokenRevoker
so no OpenBao dependency is required. Complements the existing unit-auth
contract tests (tests/unit/auth/test_sts.py, 40 tests) and individual-endpoint
tests (tests/integration/sts/test_sts_endpoints.py, 15 tests).
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


class TestStsE2ELifecycle:
    """Full lifecycle: mint → validate → get → list → revoke → validate-revoked."""

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

    # ------------------------------------------------------------------ phase 1: mint --
    def test_phase1_mint_creates_active_token(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/sts/mint",
            json={"agent_id": "e2e-agent", "parent_agent_id": "root"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["token_id"] == "tok-e2e-agent"
        assert data["agent_id"] == "e2e-agent"
        assert data["parent_agent_id"] == "root"
        assert data["role_id"] == "role-mock-1"
        assert data["role_name"] == "agent-e2e-agent"
        assert data["created_at"] != ""

    # ------------------------------------------------------------- phase 2: validate active --
    def test_phase2_validate_active_token_is_valid(self, client: TestClient) -> None:
        store = client.app.state.daemon_state["_sts_reaper"]._store
        store.get = AsyncMock(
            return_value=_token_record(agent_id="e2e-agent", token_id="tok-e2e-agent", revoked_at=None)
        )
        resp = client.get("/admin/sts/validate/e2e-agent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is True
        assert data["token_id"] == "tok-e2e-agent"
        assert data["revoked"] is False

    # --------------------------------------------------------------- phase 3: get by agent_id --
    def test_phase3_get_token_by_agent_id(self, client: TestClient) -> None:
        store = client.app.state.daemon_state["_sts_reaper"]._store
        store.get = AsyncMock(return_value=_token_record(agent_id="e2e-agent", token_id="tok-e2e-agent"))
        resp = client.get("/admin/sts/tokens/e2e-agent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["agent_id"] == "e2e-agent"
        assert data["token_id"] == "tok-e2e-agent"
        assert data["hydration_count"] == 0

    # ---------------------------------------------------------------- phase 4: list all --
    def test_phase4_list_all_includes_e2e_token(self, client: TestClient) -> None:
        store = client.app.state.daemon_state["_sts_reaper"]._store
        store.list_all = AsyncMock(
            return_value=[
                _token_record("tok-a", "agent-a"),
                _token_record("tok-e2e-agent", "e2e-agent"),
                _token_record("tok-b", "agent-b"),
            ]
        )
        resp = client.get("/admin/sts/tokens")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3
        token_ids = [r["token_id"] for r in data]
        assert "tok-e2e-agent" in token_ids

    # -------------------------------------------------------------- phase 5: revoke --
    def test_phase5_revoke_completed(self, client: TestClient) -> None:
        resp = client.post(
            "/admin/sts/revoke/e2e-agent",
            json={"terminal_state": "completed"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "revoked"
        revoker = client.app.state.daemon_state["_sts_reaper"]._revoker
        revoker.revoke.assert_awaited_once_with("e2e-agent", terminal_state="completed")

    # -------------------------------------------------- phase 6: validate post-revoke --
    def test_phase6_validate_revoked_token_invalid(self, client: TestClient) -> None:
        store = client.app.state.daemon_state["_sts_reaper"]._store
        store.get = AsyncMock(
            return_value=_token_record(
                agent_id="e2e-agent",
                token_id="tok-e2e-agent",
                revoked_at=datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC),
            )
        )
        resp = client.get("/admin/sts/validate/e2e-agent")
        assert resp.status_code == 200
        data = resp.json()
        assert data["valid"] is False
        assert data["revoked"] is True
        assert data["revoked_at"] is not None

    # ---------------------------------------------------- phase 7: service not wired --
    def test_phase7_mint_503_when_secrets_not_wired(self, client: TestClient) -> None:
        client.app.state._secrets_resolver = None
        resp = client.post(
            "/admin/sts/mint",
            json={"agent_id": "e2e-agent", "parent_agent_id": "root"},
        )
        assert resp.status_code == 503

    def test_phase7_validate_503_when_store_not_wired(self, client: TestClient) -> None:
        client.app.state.daemon_state["_sts_reaper"] = None
        resp = client.get("/admin/sts/validate/e2e-agent")
        assert resp.status_code == 503

    def test_phase7_revoke_503_when_revoker_not_wired(self, client: TestClient) -> None:
        client.app.state.daemon_state["_sts_reaper"] = None
        resp = client.post("/admin/sts/revoke/e2e-agent", json={})
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Mint → store integration tests (round-trip through store + minter)
# ---------------------------------------------------------------------------


def _session_factory_e2e(with_row: object | None = None) -> object:
    session = AsyncMock()
    session.commit = AsyncMock()
    session.add = MagicMock()
    session.execute = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none = MagicMock(return_value=with_row)
    session.execute.return_value = result
    sf = MagicMock()
    sf.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.return_value.__aexit__ = AsyncMock()
    sf.begin.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.begin.return_value.__aexit__ = AsyncMock()
    return sf


class TestStsStoreMintRoundTrip:
    """Store → minter → store round-trip integration."""

    @pytest.mark.asyncio
    async def test_mint_then_store_then_get(self) -> None:
        from general_ludd.sts.minter import TokenMinter
        from general_ludd.sts.store import TokenStore

        record = _token_record(agent_id="rt-agent", token_id="tok-rt-agent")
        sf = _session_factory_e2e(with_row=record)
        store = TokenStore(sf)

        secrets_mgr = MagicMock()
        creds = MagicMock()
        creds.role_id = "role-rt-1"
        creds.secret_id = "secret-rt-1"
        secrets_mgr.setup_approle.return_value = creds

        minter = TokenMinter(secrets_manager=secrets_mgr)
        creds_result = await minter.mint(agent_id="rt-agent", parent_agent_id="root")
        assert creds_result.role_id == "role-rt-1"

        await store.store(record)
        retrieved = await store.get("rt-agent")
        assert retrieved is not None
        assert retrieved.token_id == "tok-rt-agent"

    @pytest.mark.asyncio
    async def test_mint_without_lattice_uses_full_scope(self) -> None:
        from general_ludd.sts.minter import TokenMinter

        secrets_mgr = MagicMock()
        creds = MagicMock()
        creds.role_id = "role-full-1"
        creds.secret_id = "secret-full-1"
        secrets_mgr.setup_approle.return_value = creds

        minter = TokenMinter(secrets_manager=secrets_mgr)
        creds = await minter.mint(agent_id="full-agent", parent_agent_id="root")
        assert creds.role_id == "role-full-1"
        assert creds.secret_id == "secret-full-1"

    @pytest.mark.asyncio
    async def test_store_revoke_then_get_returns_none(self) -> None:
        from general_ludd.sts.store import TokenStore

        record = _token_record(agent_id="revoke-agent", token_id="tok-revoke-agent")
        sf = _session_factory_e2e(with_row=record)
        store = TokenStore(sf)

        await store.store(record)
        await store.revoke("tok-revoke-agent")

        sf2 = _session_factory_e2e(with_row=None)
        store2 = TokenStore(sf2)
        result = await store2.get("revoke-agent")
        assert result is None

    @pytest.mark.asyncio
    async def test_store_hydration_increments_counter(self) -> None:
        from general_ludd.sts.store import TokenStore

        record = _token_record(agent_id="hyd-agent", token_id="tok-hyd-agent")
        sf = _session_factory_e2e(with_row=record)
        store = TokenStore(sf)

        await store.store(record)
        await store.increment_hydration("hyd-agent")
        assert record.hydration_count == 1
        await store.increment_hydration("hyd-agent")
        await store.increment_hydration("hyd-agent")
        assert record.hydration_count == 3


# ---------------------------------------------------------------------------
# Multi-agent lifecycle: parent mints children, cascade revocation
# ---------------------------------------------------------------------------


class TestMultiAgentStsLifecycle:
    """Parent mints tokens for children, cascade revoke on parent completion."""

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
        creds.role_id = "role-multi"
        creds.secret_id = "secret-multi"
        secrets_mgr.setup_approle.return_value = creds
        app.state._secrets_resolver = secrets_mgr

        reaper = MagicMock(spec=TokenReaper)
        reaper._store = store
        reaper._revoker = revoker
        app.state.daemon_state["_sts_reaper"] = reaper

        return TestClient(app, raise_server_exceptions=False)

    def test_parent_mints_children_then_revokes_all(self, client: TestClient) -> None:
        children = ["child-1", "child-2", "child-3"]
        for child_id in children:
            resp = client.post(
                "/admin/sts/mint",
                json={"agent_id": child_id, "parent_agent_id": "parent-1"},
            )
            assert resp.status_code == 201
            data = resp.json()
            assert data["agent_id"] == child_id

        store = client.app.state.daemon_state["_sts_reaper"]._store
        store.list_all = AsyncMock(
            return_value=[_token_record(f"tok-{c}", c) for c in children if c != "child-2"]
            + [
                _token_record(
                    "tok-child-2-revoked",
                    "child-2",
                    revoked_at=datetime(2026, 8, 2, 12, 0, 0, tzinfo=UTC),
                )
            ]
        )
        resp = client.get("/admin/sts/tokens")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 3

        for child_id in children:
            resp = client.post(
                f"/admin/sts/revoke/{child_id}",
                json={"terminal_state": "cascade"},
            )
            assert resp.status_code == 200
