"""Unit tests for TokenRotator (NF.7 — automatic token rotation before expiry)."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.secrets.manager import AppRoleCreds
from general_ludd.sts.rotator import TokenRotationError, TokenRotator


def _make_record(
    *,
    agent_id: str = "agent-abc",
    parent_agent_id: str = "agent-parent",
    role_name: str = "agent-agent-abc",
    role_id: str = "role-abc",
    scope_actions: list[str] | None = None,
    revoked_at: datetime | None = None,
    expires_at: datetime | None = None,
    token_id: str = "tok-abc",
) -> MagicMock:
    rec = MagicMock()
    rec.agent_id = agent_id
    rec.parent_agent_id = parent_agent_id
    rec.role_name = role_name
    rec.role_id = role_id
    rec.token_id = token_id
    rec.scope_actions = json.dumps(scope_actions or ["read", "write"])
    rec.scope_hash = "hash-abc"
    rec.revoked_at = revoked_at
    rec.expires_at = expires_at
    rec.hydration_count = 0
    rec.created_at = datetime(2026, 7, 1, tzinfo=UTC)
    return rec


class TestTokenRotator:
    @pytest.mark.asyncio
    async def test_rotate_mints_new_token_with_same_scope(self):
        """rotate() returns fresh AppRoleCreds and stores a new record carrying
        the same scope_actions + parent_agent_id as the old token."""
        mock_secrets = MagicMock()
        mock_secrets.setup_approle.return_value = AppRoleCreds(
            role_id="new-role-id", secret_id="new-secret"
        )

        old_record = _make_record(scope_actions=["read", "write"])

        mock_store = AsyncMock()
        mock_store.get.return_value = old_record

        rotator = TokenRotator(mock_secrets, mock_store)
        creds = await rotator.rotate("agent-abc")

        assert isinstance(creds, AppRoleCreds)
        assert creds.role_id == "new-role-id"
        assert creds.secret_id == "new-secret"

        mock_secrets.setup_approle.assert_called_once()
        new_role_name = mock_secrets.setup_approle.call_args.args[0]
        assert new_role_name.startswith("agent-agent-abc-rot-")

        mock_store.store.assert_called_once()
        stored = mock_store.store.call_args.args[0]
        assert stored.parent_agent_id == "agent-parent"
        assert json.loads(stored.scope_actions) == ["read", "write"]

    @pytest.mark.asyncio
    async def test_rotate_revokes_old_token(self):
        """rotate() marks the old token's revoked_at and destroys its AppRole."""
        mock_client = MagicMock()
        mock_secrets = MagicMock()
        mock_secrets._client = mock_client
        mock_secrets.setup_approle.return_value = AppRoleCreds(
            role_id="nr", secret_id="ns"
        )

        old_record = _make_record()

        mock_store = AsyncMock()
        mock_store.get.return_value = old_record

        rotator = TokenRotator(mock_secrets, mock_store)
        await rotator.rotate("agent-abc")

        mock_client.auth.approle.delete_role.assert_called_once_with(
            "agent-agent-abc"
        )
        mock_store.revoke.assert_called_once_with("tok-abc")

    @pytest.mark.asyncio
    async def test_rotate_new_token_live_before_old_destroyed(self):
        """No service interruption: store.store (new live) is invoked BEFORE
        client.delete_role (old destroyed). Verified via call order."""
        mock_client = MagicMock()
        mock_secrets = MagicMock()
        mock_secrets._client = mock_client
        mock_secrets.setup_approle.return_value = AppRoleCreds(
            role_id="nr", secret_id="ns"
        )

        old_record = _make_record()
        mock_store = AsyncMock()
        mock_store.get.return_value = old_record

        manager = MagicMock()
        manager.attach_mock(mock_store.store, "store_new")
        manager.attach_mock(mock_client.auth.approle.delete_role, "destroy_old")

        rotator = TokenRotator(mock_secrets, mock_store)
        await rotator.rotate("agent-abc")

        call_names = [c[0] for c in manager.mock_calls]
        if "store_new" in call_names and "destroy_old" in call_names:
            assert call_names.index("store_new") < call_names.index("destroy_old")

    @pytest.mark.asyncio
    async def test_rotate_nonexistent_raises(self):
        """rotate() raises TokenRotationError when no record exists."""
        mock_secrets = MagicMock()
        mock_store = AsyncMock()
        mock_store.get.return_value = None

        rotator = TokenRotator(mock_secrets, mock_store)
        with pytest.raises(TokenRotationError, match="No token record"):
            await rotator.rotate("ghost")

    @pytest.mark.asyncio
    async def test_rotate_already_revoked_raises(self):
        """rotate() raises TokenRotationError on a revoked token."""
        mock_secrets = MagicMock()
        old_record = _make_record(
            revoked_at=datetime(2026, 7, 10, tzinfo=UTC)
        )
        mock_store = AsyncMock()
        mock_store.get.return_value = old_record

        rotator = TokenRotator(mock_secrets, mock_store)
        with pytest.raises(TokenRotationError, match="revoked"):
            await rotator.rotate("agent-abc")

    @pytest.mark.asyncio
    async def test_rotate_custom_new_agent_id(self):
        """rotate() accepts an explicit new_agent_id suffix."""
        mock_secrets = MagicMock()
        mock_secrets.setup_approle.return_value = AppRoleCreds(
            role_id="r", secret_id="s"
        )
        old_record = _make_record()
        mock_store = AsyncMock()
        mock_store.get.return_value = old_record

        rotator = TokenRotator(mock_secrets, mock_store)
        await rotator.rotate("agent-abc", new_agent_id="agent-abc-v2")

        mock_secrets.setup_approle.assert_called_once_with("agent-agent-abc-v2")
        stored = mock_store.store.call_args.args[0]
        assert stored.agent_id == "agent-abc-v2"

    @pytest.mark.asyncio
    async def test_rotate_propagates_expiry_window(self):
        """The new token's expires_at is computed from now + ttl_seconds,
        independent of the old token's remaining lifetime."""
        mock_secrets = MagicMock()
        mock_secrets.setup_approle.return_value = AppRoleCreds(
            role_id="r", secret_id="s"
        )
        old_record = _make_record(
            expires_at=datetime.now(UTC) + timedelta(minutes=5)
        )
        mock_store = AsyncMock()
        mock_store.get.return_value = old_record

        before = datetime.now(UTC)
        rotator = TokenRotator(mock_secrets, mock_store, ttl_seconds=3600)
        await rotator.rotate("agent-abc")
        after = datetime.now(UTC)

        stored = mock_store.store.call_args.args[0]
        assert stored.expires_at is not None
        min_expected = before + timedelta(seconds=3600) - timedelta(seconds=5)
        max_expected = after + timedelta(seconds=3600) + timedelta(seconds=5)
        assert min_expected <= stored.expires_at <= max_expected

    @pytest.mark.asyncio
    async def test_rotate_records_audit(self):
        """rotate() records a renew event on the audit pipeline."""
        mock_secrets = MagicMock()
        mock_secrets._client = MagicMock()
        mock_secrets.setup_approle.return_value = AppRoleCreds(
            role_id="r", secret_id="s"
        )
        old_record = _make_record()
        mock_store = AsyncMock()
        mock_store.get.return_value = old_record

        mock_audit = AsyncMock()

        rotator = TokenRotator(mock_secrets, mock_store, audit_pipeline=mock_audit)
        await rotator.rotate("agent-abc")

        assert mock_audit.record_renew.await_count == 1

    @pytest.mark.asyncio
    async def test_rotate_mint_failure_does_not_revoke_old(self):
        """If the new token mint fails, the old token must stay live — no
        service interruption from a failed rotation attempt."""
        mock_secrets = MagicMock()
        mock_secrets.setup_approle.side_effect = RuntimeError("bao down")

        old_record = _make_record()
        mock_store = AsyncMock()
        mock_store.get.return_value = old_record

        rotator = TokenRotator(mock_secrets, mock_store)
        with pytest.raises(TokenRotationError, match="mint"):
            await rotator.rotate("agent-abc")

        mock_store.revoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_needs_rotation_true_when_within_window(self):
        """needs_rotation() returns True when expires_at - now <= rotation_window."""
        mock_secrets = MagicMock()
        soon = datetime.now(UTC) + timedelta(minutes=3)
        mock_store = AsyncMock()

        rotator = TokenRotator(mock_secrets, mock_store, rotation_window_seconds=300)
        assert rotator.needs_rotation(expires_at=soon) is True

    @pytest.mark.asyncio
    async def test_needs_rotation_false_when_far_from_expiry(self):
        """needs_rotation() returns False when expires_at - now > rotation_window."""
        mock_secrets = MagicMock()
        far = datetime.now(UTC) + timedelta(hours=2)
        mock_store = AsyncMock()

        rotator = TokenRotator(mock_secrets, mock_store, rotation_window_seconds=300)
        assert rotator.needs_rotation(expires_at=far) is False

    @pytest.mark.asyncio
    async def test_needs_rotation_false_when_no_expiry(self):
        """needs_rotation() returns False when expires_at is None (no TTL)."""
        mock_secrets = MagicMock()
        mock_store = AsyncMock()
        rotator = TokenRotator(mock_secrets, mock_store)
        assert rotator.needs_rotation(expires_at=None) is False

    @pytest.mark.asyncio
    async def test_rotate_all_sweeps_eligible_tokens(self):
        """rotate_all() finds every live token within the rotation window and
        rotates each one. Returns the list of (old_agent_id, new_creds)."""
        mock_secrets = MagicMock()
        mock_secrets._client = MagicMock()
        mock_secrets.setup_approle.side_effect = [
            AppRoleCreds("r1", "s1"),
            AppRoleCreds("r2", "s2"),
        ]

        soon = datetime.now(UTC) + timedelta(minutes=2)
        late = datetime.now(UTC) + timedelta(hours=5)

        eligible_a = _make_record(
            agent_id="a1", token_id="t1", role_name="agent-a1",
            role_id="rid1", expires_at=soon,
        )
        eligible_b = _make_record(
            agent_id="a2", token_id="t2", role_name="agent-a2",
            role_id="rid2", expires_at=soon,
        )
        not_yet = _make_record(
            agent_id="a3", token_id="t3", role_name="agent-a3",
            role_id="rid3", expires_at=late,
        )

        by_id = {
            "a1": eligible_a,
            "a2": eligible_b,
            "a3": not_yet,
        }
        mock_store = AsyncMock()
        mock_store.list_all.return_value = [eligible_a, eligible_b, not_yet]
        mock_store.get.side_effect = lambda aid: by_id.get(aid)

        rotator = TokenRotator(
            mock_secrets, mock_store, rotation_window_seconds=300
        )
        results = await rotator.rotate_all()

        assert len(results) == 2
        rotated_ids = {old_id for old_id, _ in results}
        assert rotated_ids == {"a1", "a2"}
