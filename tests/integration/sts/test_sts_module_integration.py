"""Integration tests for the STS module (minter, store, revoker, reviver, injector).

Covers P1-P4 components: TokenStore, TokenMinter, TokenRevoker idempotency,
TokenReviver + audit, SubagentTokenInjector, and full round-trip cycle.
All tests use mocked SecretsManager and mocked SQLAlchemy session factories.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.db.models import AgentTokenModel
from general_ludd.permissions.tool_permissions import (
    CapabilityLattice,
    ToolAction,
)
from general_ludd.security.permissions import (
    Capability,
    PermissionSpec,
    PermissionSpecParser,
    PermissionSubject,
)
from general_ludd.sts.audit import StsAuditPipeline
from general_ludd.sts.injector import SubagentTokenInjector
from general_ludd.sts.minter import TokenMinter
from general_ludd.sts.narrowing import CapabilityNarrowing
from general_ludd.sts.reviver import TokenRevivalError, TokenReviver
from general_ludd.sts.revoker import TokenRevoker
from general_ludd.sts.store import TokenStore


def _session_factory(with_row: Any | None = None) -> Any:
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
    sf.begin = MagicMock()
    sf.begin.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.begin.return_value.__aexit__ = AsyncMock()
    return sf


def _mock_secrets_manager() -> MagicMock:
    mgr = MagicMock()
    creds = MagicMock()
    creds.role_id = "role-test-1"
    creds.secret_id = "secret-test-1"
    mgr.setup_approle.return_value = creds
    mgr.rotate_approle_secret_id.return_value = "fresh-secret-1"
    return mgr


def _token_record(**overrides: Any) -> AgentTokenModel:
    kwargs: dict[str, Any] = {
        "token_id": "tok-agent-1",
        "agent_id": "agent-1",
        "parent_agent_id": "parent-1",
        "role_name": "agent-agent-1",
        "role_id": "role-test-1",
        "scope_hash": "",
        "scope_actions": "[]",
        "created_at": datetime(2026, 7, 14, tzinfo=UTC),
        "expires_at": datetime(2026, 7, 15, tzinfo=UTC),
        "revoked_at": None,
        "hydration_count": 0,
    }
    kwargs.update(overrides)
    return AgentTokenModel(**kwargs)


# ---------------------------------------------------------------------------
# TokenStore integration tests (5 tests)
# ---------------------------------------------------------------------------


class TestTokenStore:
    async def test_store_then_get_round_trips(self):
        sf = _session_factory(with_row=_token_record())
        store = TokenStore(sf)
        record = _token_record()
        await store.store(record)
        retrieved = await store.get("agent-1")
        assert retrieved is not None
        assert retrieved.token_id == "tok-agent-1"
        assert retrieved.agent_id == "agent-1"
        assert retrieved.hydration_count == 0

    async def test_get_nonexistent_returns_none(self):
        sf = _session_factory(with_row=None)
        store = TokenStore(sf)
        result = await store.get("ghost-agent")
        assert result is None

    async def test_revoke_sets_revoked_at(self):
        record = _token_record()
        sf = _session_factory(with_row=record)
        store = TokenStore(sf)
        await store.revoke("tok-agent-1")
        assert record.revoked_at is not None
        assert record.revoked_at.tzinfo is not None

    async def test_increment_hydration_bumps_counter(self):
        record = _token_record()
        sf = _session_factory(with_row=record)
        store = TokenStore(sf)
        await store.increment_hydration("agent-1")
        assert record.hydration_count == 1
        await store.increment_hydration("agent-1")
        assert record.hydration_count == 2

    async def test_revoke_nonexistent_does_not_raise(self):
        sf = _session_factory(with_row=None)
        store = TokenStore(sf)
        await store.revoke("tok-nonexistent")


# ---------------------------------------------------------------------------
# TokenMinter integration tests (6 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTokenMinter:
    async def test_mint_without_lattice_creates_basic_creds(self):
        secrets = _mock_secrets_manager()
        minter = TokenMinter(secrets_manager=secrets)
        creds = await minter.mint(agent_id="agent-1", parent_agent_id="parent-1")
        assert creds.role_id == "role-test-1"
        assert creds.secret_id == "secret-test-1"
        secrets.setup_approle.assert_called_once_with("agent-agent-1")

    async def test_mint_with_lattice_narrows_scope(self):
        secrets = _mock_secrets_manager()
        minter = TokenMinter(secrets_manager=secrets)
        lattice = CapabilityLattice()
        creds = await minter.mint(
            agent_id="agent-1",
            parent_agent_id="parent-1",
            parent_lattice=lattice,
            child_actions={ToolAction.READ, ToolAction.WRITE},
            parent_role="admin",
        )
        assert creds.role_id == "role-test-1"
        secrets.setup_approle.assert_called_once()

    async def test_mint_with_lattice_drops_unauthorized_actions(self):
        secrets = _mock_secrets_manager()
        minter = TokenMinter(secrets_manager=secrets)
        lattice = CapabilityLattice()
        creds = await minter.mint(
            agent_id="agent-1",
            parent_agent_id="parent-1",
            parent_lattice=lattice,
            child_actions={ToolAction.DELETE},
            parent_role="reader",
        )
        assert creds.role_id == "role-test-1"
        secrets.setup_approle.assert_called_once()

    async def test_mint_records_audit_event(self):
        sf = _session_factory(with_row=MagicMock())
        pipeline = StsAuditPipeline(sf)
        pipeline._append_event = AsyncMock()
        secrets = _mock_secrets_manager()
        minter = TokenMinter(secrets_manager=secrets, audit_pipeline=pipeline)
        await minter.mint(agent_id="agent-1", parent_agent_id="parent-1")
        pipeline._append_event.assert_called_once()

    async def test_render_policy_returns_valid_hcl(self):
        minter = TokenMinter(secrets_manager=_mock_secrets_manager())
        hcl = minter.render_policy(actions={ToolAction.READ}, role_name="test")
        assert isinstance(hcl, str)
        assert "test" in hcl
        assert "read" in hcl

    async def test_render_policy_empty_actions_returns_empty(self):
        minter = TokenMinter(secrets_manager=_mock_secrets_manager())
        hcl = minter.render_policy(actions=[], role_name="test")
        assert hcl == ""


# ---------------------------------------------------------------------------
# TokenRevoker integration tests (4 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTokenRevoker:
    async def test_revoke_idempotent_already_revoked_noop(self):
        record = _token_record(revoked_at=datetime(2026, 7, 14, tzinfo=UTC))
        sf = _session_factory(with_row=record)
        store = TokenStore(sf)
        secrets = MagicMock()
        revoker = TokenRevoker(secrets, store)
        await revoker.revoke("agent-1")
        secrets._client.auth.approle.delete_role.assert_not_called()

    async def test_revoke_idempotent_missing_record_noop(self):
        sf = _session_factory(with_row=None)
        store = TokenStore(sf)
        secrets = MagicMock()
        revoker = TokenRevoker(secrets, store)
        await revoker.revoke("ghost-agent")
        secrets._client.auth.approle.delete_role.assert_not_called()

    async def test_revoke_destroys_approle_and_marks_revoked(self):
        record = _token_record()
        sf = _session_factory(with_row=record)
        store = TokenStore(sf)
        secrets = MagicMock()
        secrets._client = MagicMock()
        revoker = TokenRevoker(secrets, store)
        await revoker.revoke("agent-1")
        secrets._client.auth.approle.delete_role.assert_called_once_with("agent-agent-1")
        assert record.revoked_at is not None

    async def test_revoke_records_audit_event(self):
        record = _token_record()
        sf = _session_factory(with_row=record)
        store = TokenStore(sf)
        secrets = MagicMock()
        secrets._client = MagicMock()
        pipeline = StsAuditPipeline(sf)
        pipeline.record_revoke = AsyncMock()
        revoker = TokenRevoker(secrets, store, audit_pipeline=pipeline)
        await revoker.revoke("agent-1")
        pipeline.record_revoke.assert_called_once_with(
            token_id="tok-agent-1",
            agent_id="agent-1",
            parent_agent_id="parent-1",
        )


# ---------------------------------------------------------------------------
# TokenReviver integration tests (5 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestTokenReviver:
    async def test_revive_nonexistent_role_raises(self):
        sf = _session_factory(with_row=None)
        store = TokenStore(sf)
        secrets = _mock_secrets_manager()
        reviver = TokenReviver(secrets, store)
        with pytest.raises(TokenRevivalError, match="No token record"):
            await reviver.revive("ghost-agent")

    async def test_revive_revoked_token_raises(self):
        record = _token_record(revoked_at=datetime(2026, 7, 14, tzinfo=UTC))
        sf = _session_factory(with_row=record)
        store = TokenStore(sf)
        secrets = _mock_secrets_manager()
        reviver = TokenReviver(secrets, store)
        with pytest.raises(TokenRevivalError, match="revoked"):
            await reviver.revive("agent-1")

    async def test_revive_rotates_secret_and_increments_hydration(self):
        record = _token_record()
        sf = _session_factory(with_row=record)
        store = TokenStore(sf)
        secrets = _mock_secrets_manager()
        reviver = TokenReviver(secrets, store)
        creds = await reviver.revive("agent-1")
        secrets.rotate_approle_secret_id.assert_called_once_with("agent-agent-1")
        assert creds.secret_id == "fresh-secret-1"
        assert creds.role_id == "role-test-1"
        assert record.hydration_count == 1

    async def test_revive_records_audit_event(self):
        record = _token_record()
        sf = _session_factory(with_row=record)
        store = TokenStore(sf)
        secrets = _mock_secrets_manager()
        pipeline = StsAuditPipeline(sf)
        pipeline.record_revive = AsyncMock()
        reviver = TokenReviver(secrets, store, audit_pipeline=pipeline)
        await reviver.revive("agent-1")
        pipeline.record_revive.assert_called_once_with(
            token_id="tok-agent-1",
            agent_id="agent-1",
            parent_agent_id="parent-1",
        )

    async def test_revive_secrets_rotation_failure_raises(self):
        record = _token_record()
        sf = _session_factory(with_row=record)
        store = TokenStore(sf)
        secrets = MagicMock()
        secrets.rotate_approle_secret_id.side_effect = RuntimeError("OpenBao down")
        reviver = TokenReviver(secrets, store)
        with pytest.raises(TokenRevivalError, match="RuntimeError"):
            await reviver.revive("agent-1")


# ---------------------------------------------------------------------------
# SubagentTokenInjector integration tests (3 tests)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestSubagentTokenInjector:
    async def test_enrich_persists_record_and_sets_env_vars(self):
        sf = _session_factory()
        store = TokenStore(sf)
        secrets = _mock_secrets_manager()
        minter = TokenMinter(secrets_manager=secrets)
        dispatcher = MagicMock()
        injector = SubagentTokenInjector(minter, store, dispatcher)
        task = MagicMock()
        task.task_id = "agent-enrich-1"
        task.invoker_name = "parent-1"
        task.parent_task_id = None
        task.env = {}
        await injector.enrich(task)
        assert task.env["GLUDD_STS_ROLE_ID"] == "role-test-1"
        assert task.env["GLUDD_STS_SECRET_ID"] == "secret-test-1"

    async def test_enrich_falls_back_to_parent_task_id(self):
        sf = _session_factory()
        store = TokenStore(sf)
        secrets = _mock_secrets_manager()
        minter = TokenMinter(secrets_manager=secrets)
        dispatcher = MagicMock()
        injector = SubagentTokenInjector(minter, store, dispatcher)
        task = MagicMock()
        task.task_id = "agent-enrich-2"
        task.invoker_name = None
        task.parent_task_id = "parent-task-2"
        task.env = {}
        await injector.enrich(task)
        assert task.env["GLUDD_STS_ROLE_ID"] == "role-test-1"

    async def test_env_vars_returns_sts_dict(self):
        sf = _session_factory()
        store = TokenStore(sf)
        secrets = _mock_secrets_manager()
        minter = TokenMinter(secrets_manager=secrets)
        injector = SubagentTokenInjector(minter, store, MagicMock())
        result = await injector.env_vars(agent_id="agent-1", parent_agent_id="parent-1")
        assert result["GLUDD_STS_ROLE_ID"] == "role-test-1"
        assert result["GLUDD_STS_SECRET_ID"] == "secret-test-1"
        assert result["GLUDD_STS_TOKEN_ID"] == "tok-agent-1"


# ---------------------------------------------------------------------------
# CapabilityNarrowing validate_narrowing (5 tests)
# ---------------------------------------------------------------------------


class TestCapabilityNarrowingValidate:
    def test_valid_subset_passes(self):
        parent = {ToolAction.READ, ToolAction.WRITE, ToolAction.EXECUTE}
        child = {ToolAction.READ, ToolAction.WRITE}
        result = CapabilityNarrowing.validate_narrowing(parent, child)
        assert result is True

    def test_child_exceeding_parent_fails(self):
        parent = {ToolAction.READ}
        child = {ToolAction.READ, ToolAction.WRITE}
        result = CapabilityNarrowing.validate_narrowing(parent, child)
        assert result is False

    def test_child_equals_parent_passes(self):
        parent = {ToolAction.READ, ToolAction.WRITE}
        child = {ToolAction.READ, ToolAction.WRITE}
        result = CapabilityNarrowing.validate_narrowing(parent, child)
        assert result is True

    def test_child_empty_passes(self):
        parent = {ToolAction.READ, ToolAction.WRITE}
        child: set[ToolAction] = set()
        result = CapabilityNarrowing.validate_narrowing(parent, child)
        assert result is True

    def test_mixed_string_and_enum_passes(self):
        parent = {ToolAction.READ, ToolAction.WRITE}
        child: set[object] = {"read", ToolAction.WRITE}
        result = CapabilityNarrowing.validate_narrowing(parent, child)
        assert result is True


# ---------------------------------------------------------------------------
# Full round-trip: minter + store + get + revoke (1 test)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class TestMinterStoreRoundTrip:
    async def test_mint_store_get_revoke_cycle(self):
        record = _token_record(agent_id="agent-rt-1", token_id="tok-agent-rt-1")
        sf = _session_factory(with_row=record)
        store = TokenStore(sf)
        secrets = _mock_secrets_manager()
        minter = TokenMinter(secrets_manager=secrets)
        secrets._client = MagicMock()
        creds = await minter.mint(agent_id="agent-rt-1", parent_agent_id="parent-rt-1")
        db_record = AgentTokenModel(
            token_id="tok-agent-rt-1",
            agent_id="agent-rt-1",
            parent_agent_id="parent-rt-1",
            role_name="agent-agent-rt-1",
            role_id=creds.role_id,
            scope_hash="",
        )
        await store.store(db_record)
        retrieved = await store.get("agent-rt-1")
        assert retrieved is not None
        assert retrieved.token_id == "tok-agent-rt-1"
        assert retrieved.revoked_at is None
        revoker = TokenRevoker(secrets, store)
        await revoker.revoke("agent-rt-1")
        assert record.revoked_at is not None


# ---------------------------------------------------------------------------
# PermissionSpec intersection yields STS_TOKEN subject (2 tests)
# ---------------------------------------------------------------------------


class TestPermissionSpecIntersection:
    def test_intersection_yields_sts_token_subject(self):
        parent = PermissionSpec(
            agent_type="primary",
            capabilities=[
                Capability(
                    resource="file:tmp",
                    actions=["read", "write"],
                    constraints={"path_prefix": "/tmp/gludd/"},
                )
            ],
        )
        child = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(
                    resource="file:tmp",
                    actions=["read"],
                    constraints={"path_prefix": "/tmp/gludd/sub/"},
                )
            ],
        )
        inter = PermissionSpecParser.intersection(parent, child)
        assert inter.subject == PermissionSubject.STS_TOKEN
        assert len(inter.capabilities) == 1
        assert inter.capabilities[0].actions == ["read"]

    def test_intersection_drops_capability_not_in_parent(self):
        parent = PermissionSpec(
            agent_type="primary",
            capabilities=[
                Capability(
                    resource="file:tmp",
                    actions=["read"],
                    constraints={"path_prefix": "/tmp/gludd/"},
                )
            ],
        )
        child = PermissionSpec(
            agent_type="subagent",
            capabilities=[
                Capability(
                    resource="secret:openbao",
                    actions=["read"],
                    constraints={"openbao_paths": ["secret/data/*"]},
                )
            ],
        )
        inter = PermissionSpecParser.intersection(parent, child)
        assert inter.subject == PermissionSubject.STS_TOKEN
        assert len(inter.capabilities) == 0
