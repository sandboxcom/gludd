"""Structural tests for routers/sts.py."""

from general_ludd.routers.sts import (
    MintRequest,
    MintResponse,
    RevokeRequest,
    ValidateResponse,
)


class TestStsRouter:
    def test_imports(self):
        pass

    def test_mint_request(self):
        req = MintRequest(agent_id="agent-1")
        assert req.agent_id == "agent-1"
        assert req.parent_agent_id == "root"

    def test_mint_request_custom_parent(self):
        req = MintRequest(agent_id="agent-2", parent_agent_id="agent-1")
        assert req.parent_agent_id == "agent-1"

    def test_revoke_request(self):
        req = RevokeRequest(terminal_state="completed")
        assert req.terminal_state == "completed"

    def test_revoke_request_default(self):
        req = RevokeRequest()
        assert req.terminal_state == "completed"

    def test_mint_response(self):
        resp = MintResponse(
            token_id="tok-1",
            agent_id="agent-1",
            parent_agent_id="root",
            role_name="agent-agent-1",
            role_id="role-1",
            created_at="2024-01-01T00:00:00Z",
            expires_at=None,
        )
        assert resp.agent_id == "agent-1"
        assert resp.expires_at is None

    def test_validate_response(self):
        resp = ValidateResponse(
            valid=True,
            token_id="tok-1",
            agent_id="agent-1",
            revoked=False,
            revoked_at=None,
        )
        assert resp.valid is True
        assert resp.revoked is False
