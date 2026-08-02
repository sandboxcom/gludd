"""Unit tests for TokenRevoker — destroy AppRole, mark revoked, cascade."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.db.models import AgentTokenModel
from general_ludd.sts.audit import StsAuditPipeline
from general_ludd.sts.revoker import TokenRevoker
from general_ludd.sts.store import TokenStore


def _make_secrets_manager():
    sm = MagicMock()
    sm._client = MagicMock()
    sm._client.auth.approle.delete_role = MagicMock()
    sm._client.sys.delete_policy = MagicMock()
    return sm


def _make_token_store(agent_id="agent-1", token_record=None):
    store = MagicMock(spec=TokenStore)
    if token_record is None:
        token_record = _make_token(agent_id=agent_id)
    store.get = AsyncMock(return_value=token_record)
    store.revoke = AsyncMock()
    return store, token_record


def _make_audit_pipeline():
    audit = MagicMock(spec=StsAuditPipeline)
    audit.record_revoke = AsyncMock()
    return audit


def _make_token(**overrides) -> AgentTokenModel:
    defaults: dict = {
        "token_id": "tok-agent-1",
        "agent_id": "agent-1",
        "parent_agent_id": "parent-xyz",
        "role_name": "agent-agent-1",
        "role_id": "test-role-id",
        "scope_hash": "abc123",
        "scope_actions": "[]",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "expires_at": datetime(2026, 1, 2, tzinfo=UTC),
        "revoked_at": None,
        "hydration_count": 0,
    }
    defaults.update(overrides)
    return AgentTokenModel(**defaults)


class TestTokenRevokerInit:
    def test_stores_secrets_manager(self):
        sm = _make_secrets_manager()
        store, _ = _make_token_store()
        revoker = TokenRevoker(sm, store)
        assert revoker._secrets_manager is sm

    def test_stores_token_store(self):
        sm = _make_secrets_manager()
        store, _ = _make_token_store()
        revoker = TokenRevoker(sm, store)
        assert revoker._token_store is store

    def test_stores_audit_pipeline(self):
        sm = _make_secrets_manager()
        store, _ = _make_token_store()
        audit = _make_audit_pipeline()
        revoker = TokenRevoker(sm, store, audit_pipeline=audit)
        assert revoker._audit_pipeline is audit

    def test_audit_pipeline_none_by_default(self):
        sm = _make_secrets_manager()
        store, _ = _make_token_store()
        revoker = TokenRevoker(sm, store)
        assert revoker._audit_pipeline is None


class TestTokenRevokerRevoke:
    @pytest.mark.asyncio
    async def test_revoke_marks_token_revoked(self):
        sm = _make_secrets_manager()
        store, _token = _make_token_store()
        revoker = TokenRevoker(sm, store)

        await revoker.revoke("agent-1")

        store.revoke.assert_awaited_once_with("tok-agent-1")

    @pytest.mark.asyncio
    async def test_revoke_destroys_approle(self):
        sm = _make_secrets_manager()
        store, _token = _make_token_store()
        revoker = TokenRevoker(sm, store)

        await revoker.revoke("agent-1")

        sm._client.auth.approle.delete_role.assert_called_once_with("agent-agent-1")

    @pytest.mark.asyncio
    async def test_revoke_deletes_scoped_policy(self):
        sm = _make_secrets_manager()
        store, _token = _make_token_store()
        revoker = TokenRevoker(sm, store)

        await revoker.revoke("agent-1")

        sm._client.sys.delete_policy.assert_called_once()

    @pytest.mark.asyncio
    async def test_revoke_idempotent_on_already_revoked(self):
        sm = _make_secrets_manager()
        token = _make_token(revoked_at=datetime(2026, 1, 2, tzinfo=UTC))
        store = MagicMock(spec=TokenStore)
        store.get = AsyncMock(return_value=token)
        store.revoke = AsyncMock()
        revoker = TokenRevoker(sm, store)

        await revoker.revoke("agent-1")

        store.revoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_revoke_noop_on_missing_token(self):
        sm = _make_secrets_manager()
        store = MagicMock(spec=TokenStore)
        store.get = AsyncMock(return_value=None)
        store.revoke = AsyncMock()
        revoker = TokenRevoker(sm, store)

        await revoker.revoke("nonexistent")

        store.revoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_revoke_raises_on_invalid_terminal_state(self):
        sm = _make_secrets_manager()
        store, _ = _make_token_store()
        revoker = TokenRevoker(sm, store)

        with pytest.raises(ValueError, match="unsupported STS terminal state"):
            await revoker.revoke("agent-1", terminal_state="bogus")

    @pytest.mark.asyncio
    async def test_revoke_records_audit_when_pipeline_present(self):
        sm = _make_secrets_manager()
        store, _token = _make_token_store()
        audit = _make_audit_pipeline()
        revoker = TokenRevoker(sm, store, audit_pipeline=audit)

        await revoker.revoke("agent-1")

        audit.record_revoke.assert_awaited_once()
        call_kwargs = audit.record_revoke.call_args.kwargs
        assert call_kwargs["token_id"] == "tok-agent-1"
        assert call_kwargs["agent_id"] == "agent-1"

    @pytest.mark.asyncio
    async def test_revoke_no_audit_without_pipeline(self):
        sm = _make_secrets_manager()
        store, _token = _make_token_store()
        revoker = TokenRevoker(sm, store, audit_pipeline=None)

        await revoker.revoke("agent-1")

        # Should not raise; no audit call


class TestTokenRevokerCascadeHook:
    @pytest.mark.asyncio
    async def test_set_cascade_hook_late_binds(self):
        sm = _make_secrets_manager()
        store, _ = _make_token_store()
        revoker = TokenRevoker(sm, store)

        hook = AsyncMock()
        revoker.set_cascade_hook(hook)

        assert revoker._cascade_hook is hook

    @pytest.mark.asyncio
    async def test_revoke_invokes_cascade_hook(self):
        sm = _make_secrets_manager()
        store, _token = _make_token_store()
        revoker = TokenRevoker(sm, store)
        hook = AsyncMock()
        revoker.set_cascade_hook(hook)

        await revoker.revoke("agent-1")

        hook.assert_awaited_once_with("agent-1")

    @pytest.mark.asyncio
    async def test_revoke_handles_cascade_hook_failure_gracefully(self):
        sm = _make_secrets_manager()
        store, _token = _make_token_store()
        revoker = TokenRevoker(sm, store)
        hook = AsyncMock(side_effect=RuntimeError("cascade failed"))
        revoker.set_cascade_hook(hook)

        await revoker.revoke("agent-1")

        hook.assert_awaited_once()
        store.revoke.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_revoke_no_cascade_without_hook(self):
        sm = _make_secrets_manager()
        store, _token = _make_token_store()
        revoker = TokenRevoker(sm, store)

        await revoker.revoke("agent-1")

        store.revoke.assert_awaited_once()


class TestTokenRevokerValidTerminalStates:
    @pytest.mark.asyncio
    async def test_all_valid_terminal_states(self):
        sm = _make_secrets_manager()
        store, _token = _make_token_store()
        revoker = TokenRevoker(sm, store)

        valid_states = [
            "cancelled",
            "cascade",
            "completed",
            "expired",
            "failed",
            "rotated",
            "timed_out",
        ]
        for state in valid_states:
            await revoker.revoke("agent-1", terminal_state=state)
            assert store.revoke.call_count > 0  # Each call invokes revoke
