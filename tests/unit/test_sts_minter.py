"""Unit tests for TokenMinter — mint, narrowing, audit pipeline, and policy rendering."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.permissions.tool_permissions import CapabilityLattice, ToolAction
from general_ludd.secrets.manager import AppRoleCreds
from general_ludd.sts.minter import TokenMinter


def _make_secrets_manager(role_id: str = "test-role-id", secret_id: str = "test-secret-id"):
    sm = MagicMock()
    sm.setup_approle.return_value = AppRoleCreds(role_id=role_id, secret_id=secret_id)
    return sm


def _make_audit_pipeline():
    audit = MagicMock()
    audit.record_mint = AsyncMock()
    return audit


class TestTokenMinterInit:
    def test_stores_secrets_manager(self):
        sm = _make_secrets_manager()
        minter = TokenMinter(sm)
        assert minter._secrets_manager is sm

    def test_stores_audit_pipeline(self):
        sm = _make_secrets_manager()
        audit = _make_audit_pipeline()
        minter = TokenMinter(sm, audit_pipeline=audit)
        assert minter._audit_pipeline is audit

    def test_audit_pipeline_none_by_default(self):
        minter = TokenMinter(_make_secrets_manager())
        assert minter._audit_pipeline is None


class TestTokenMinterMint:
    @pytest.mark.asyncio
    async def test_mint_calls_setup_approle_with_correct_role_name(self):
        sm = _make_secrets_manager()
        minter = TokenMinter(sm)
        creds = await minter.mint(agent_id="agent-abc", parent_agent_id="parent-xyz")
        sm.setup_approle.assert_called_once_with("agent-agent-abc")
        assert creds.role_id == "test-role-id"
        assert creds.secret_id == "test-secret-id"

    @pytest.mark.asyncio
    async def test_mint_returns_approles_creds(self):
        sm = _make_secrets_manager(role_id="r1", secret_id="s1")
        minter = TokenMinter(sm)
        creds = await minter.mint(agent_id="a1", parent_agent_id="p1")
        assert isinstance(creds, AppRoleCreds)
        assert creds.role_id == "r1"
        assert creds.secret_id == "s1"

    @pytest.mark.asyncio
    async def test_mint_without_narrowing_does_not_call_lattice(self):
        sm = _make_secrets_manager()
        minter = TokenMinter(sm)
        await minter.mint(agent_id="a1", parent_agent_id="p1", scope=None)
        sm.setup_approle.assert_called_once()

    @pytest.mark.asyncio
    async def test_mint_with_narrowing_intersects_actions(self):
        sm = _make_secrets_manager()
        minter = TokenMinter(sm)
        lattice = CapabilityLattice()
        child_actions = {ToolAction.READ, ToolAction.WRITE, ToolAction.DELETE}

        await minter.mint(
            agent_id="agent-narrowed",
            parent_agent_id="p1",
            parent_lattice=lattice,
            child_actions=child_actions,
            parent_role="reader",
        )

        sm.setup_approle.assert_called_once_with("agent-agent-narrowed")

    @pytest.mark.asyncio
    async def test_mint_with_full_admin_narrowing_passes_all(self):
        sm = _make_secrets_manager()
        minter = TokenMinter(sm)
        lattice = CapabilityLattice()
        child_actions = {
            ToolAction.READ,
            ToolAction.WRITE,
            ToolAction.CREATE,
            ToolAction.OVERWRITE,
            ToolAction.EXECUTE,
            ToolAction.DELETE,
        }

        await minter.mint(
            agent_id="agent-full",
            parent_agent_id="admin-parent",
            parent_lattice=lattice,
            child_actions=child_actions,
            parent_role="admin",
        )

        sm.setup_approle.assert_called_once()

    @pytest.mark.asyncio
    async def test_mint_with_narrowing_records_audit(self):
        sm = _make_secrets_manager()
        audit = _make_audit_pipeline()
        minter = TokenMinter(sm, audit_pipeline=audit)
        lattice = CapabilityLattice()

        await minter.mint(
            agent_id="agent-audit",
            parent_agent_id="p-audit",
            parent_lattice=lattice,
            child_actions={ToolAction.READ},
            parent_role="admin",
        )

        audit.record_mint.assert_awaited_once()
        call_kwargs = audit.record_mint.call_args.kwargs
        assert call_kwargs["token_id"] == "tok-agent-audit"
        assert call_kwargs["issuer_agent_id"] == "p-audit"
        assert call_kwargs["subject_agent_id"] == "agent-audit"

    @pytest.mark.asyncio
    async def test_mint_without_audit_pipeline_does_not_record(self):
        sm = _make_secrets_manager()
        minter = TokenMinter(sm, audit_pipeline=None)
        lattice = CapabilityLattice()

        await minter.mint(
            agent_id="agent-noaudit",
            parent_agent_id="p1",
            parent_lattice=lattice,
            child_actions={ToolAction.READ},
        )

        creds = sm.setup_approle.return_value
        assert creds.role_id == "test-role-id"

    @pytest.mark.asyncio
    async def test_mint_without_narrowing_still_logs_audit(self):
        sm = _make_secrets_manager()
        audit = _make_audit_pipeline()
        minter = TokenMinter(sm, audit_pipeline=audit)

        await minter.mint(agent_id="a1", parent_agent_id="p1")

        audit.record_mint.assert_awaited_once()
        call_kwargs = audit.record_mint.call_args.kwargs
        assert call_kwargs["scope_actions"] is None


class TestTokenMinterRenderPolicy:
    def test_render_policy_delegates_to_renderer(self):
        sm = _make_secrets_manager()
        minter = TokenMinter(sm)
        hcl = minter.render_policy({ToolAction.READ, ToolAction.WRITE}, role_name="my-agent")
        assert isinstance(hcl, str)
        assert len(hcl) > 0
        assert "my-agent" in hcl
        assert "read" in hcl

    def test_render_policy_empty_actions(self):
        sm = _make_secrets_manager()
        minter = TokenMinter(sm)
        hcl = minter.render_policy([], role_name="empty")
        assert hcl == ""

    def test_render_policy_string_actions(self):
        sm = _make_secrets_manager()
        minter = TokenMinter(sm)
        hcl = minter.render_policy(["read", "execute"], role_name="string-agent")
        assert "read" in hcl
        assert "sudo" in hcl
