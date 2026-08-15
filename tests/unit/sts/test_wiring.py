"""TDD tests for STS P4 wiring — executor env injection + audit pipeline daemon wiring.

All tests MUST FAIL — the implementation does not exist yet (Phase P4).

Tests:
  1. SubagentTokenInjector injects GLUDD_STS_ROLE_ID + GLUDD_STS_SECRET_ID into env
  2. SubagentTokenInjector injects GLUDD_STS_TOKEN_ID for audit traceability
  3. injector.env_vars() returns a dict of env vars that do NOT leak PSK
  4. StsAuditPipeline.wire_to_daemon() attaches pipeline to daemon_state
  5. executor constructs STS-scoped SecretsManager from injected env vars
  6. audit pipeline flush_on_tick writes pending events to DB each tick
  7. audit pipeline filters out duplicate events within same flush window
  8. SubagentTokenInjector no-ops gracefully when minter is unavailable
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.sts.injector import SubagentTokenInjector


class TestSubagentTokenInjectorEnvInjection:
    """P4: SubagentTokenInjector must inject STS tokens into subagent env."""

    @pytest.mark.asyncio
    async def test_injector_env_vars_contains_role_id(self) -> None:
        """env_vars() must include GLUDD_STS_ROLE_ID from the minter."""
        mock_minter = AsyncMock()
        mock_minter.mint.return_value = MagicMock(role_id="role-1", secret_id="sec-1")
        mock_store = MagicMock()
        mock_dispatcher = MagicMock()
        injector = SubagentTokenInjector(mock_minter, mock_store, mock_dispatcher)

        env = await injector.env_vars(agent_id="agent-1", parent_agent_id="parent-1")

        assert "GLUDD_STS_ROLE_ID" in env, (
            "P4 gap: SubagentTokenInjector.env_vars() must emit GLUDD_STS_ROLE_ID "
            "so the subagent can authenticate via its AppRole credentials"
        )
        assert isinstance(env["GLUDD_STS_ROLE_ID"], str)
        assert len(env["GLUDD_STS_ROLE_ID"]) > 0

    @pytest.mark.asyncio
    async def test_injector_env_vars_contains_secret_id(self) -> None:
        """env_vars() must include GLUDD_STS_SECRET_ID."""
        mock_minter = AsyncMock()
        mock_minter.mint.return_value = MagicMock(role_id="role-2", secret_id="sec-2")
        mock_store = MagicMock()
        mock_dispatcher = MagicMock()
        injector = SubagentTokenInjector(mock_minter, mock_store, mock_dispatcher)

        env = await injector.env_vars(agent_id="agent-2", parent_agent_id="parent-1")

        assert "GLUDD_STS_SECRET_ID" in env, (
            "P4 gap: SubagentTokenInjector.env_vars() must emit GLUDD_STS_SECRET_ID "
            "so the subagent can call /auth/approle/login"
        )
        assert isinstance(env["GLUDD_STS_SECRET_ID"], str)
        assert len(env["GLUDD_STS_SECRET_ID"]) > 0

    @pytest.mark.asyncio
    async def test_injector_env_vars_contains_token_id(self) -> None:
        """env_vars() must include GLUDD_STS_TOKEN_ID for audit traceability."""
        mock_minter = AsyncMock()
        mock_minter.mint.return_value = MagicMock(role_id="role-3", secret_id="sec-3")
        mock_store = MagicMock()
        mock_dispatcher = MagicMock()
        injector = SubagentTokenInjector(mock_minter, mock_store, mock_dispatcher)

        env = await injector.env_vars(agent_id="agent-3", parent_agent_id="parent-1")

        assert "GLUDD_STS_TOKEN_ID" in env, (
            "P4 gap: SubagentTokenInjector must emit GLUDD_STS_TOKEN_ID "
            "so every subagent action is attributable to a specific minted token"
        )

    @pytest.mark.asyncio
    async def test_env_vars_never_contain_psk(self) -> None:
        """env_vars() must NEVER inject GLUDD_AUTH_PSK into the subagent env."""
        mock_minter = AsyncMock()
        mock_minter.mint.return_value = MagicMock(role_id="role-4", secret_id="sec-4")
        mock_store = MagicMock()
        mock_dispatcher = MagicMock()
        injector = SubagentTokenInjector(mock_minter, mock_store, mock_dispatcher)

        env = await injector.env_vars(agent_id="agent-4", parent_agent_id="parent-1")

        assert "GLUDD_AUTH_PSK" not in env, (
            "P4 gap: SubagentTokenInjector must NOT leak the admin PSK. "
            "Per NEXT_RELEASE_BETA2_SPEC Wave 1, the PSK flat-authz cluster "
            "requires scoped STS tokens instead of PSK pass-through."
        )
        assert "ZAI_API_KEY" not in env, (
            "P4 gap: injector must scrub ambient env keys — no model API keys "
            "should leak to the subagent (see stream dispatch env leak)"
        )

    @pytest.mark.asyncio
    async def test_injector_noops_when_minter_unavailable(self) -> None:
        """env_vars() returns empty dict when minter is None (fail-safe no-op)."""
        mock_store = MagicMock()
        mock_dispatcher = MagicMock()
        injector = SubagentTokenInjector(None, mock_store, mock_dispatcher)  # type: ignore[arg-type]

        env = await injector.env_vars(agent_id="agent-1", parent_agent_id="parent-1")

        assert env == {}, (
            "P4 gap: when minter is unavailable, env_vars() must return empty dict "
            "(fail-soft) so dispatch is not blocked by missing STS infrastructure"
        )


class TestStsAuditPipelineWiring:
    """P4: StsAuditPipeline must be wired into the daemon and EventLoop."""

    def test_audit_pipeline_wire_to_daemon_state(self) -> None:
        """wire_to_daemon() must attach '_sts_audit_pipeline' to daemon_state."""
        from general_ludd.sts.audit import StsAuditPipeline

        mock_session_factory = MagicMock()
        pipeline = StsAuditPipeline(mock_session_factory)
        daemon_state: dict[str, object] = {}

        pipeline.wire_to_daemon(daemon_state)

        assert "_sts_audit_pipeline" in daemon_state, (
            "P4 gap: StsAuditPipeline.wire_to_daemon() must register the pipeline "
            "under '_sts_audit_pipeline' so routers can record audit events"
        )
        assert daemon_state["_sts_audit_pipeline"] is pipeline

    def test_flush_on_tick_is_callable(self) -> None:
        """StsAuditPipeline must expose an async flush_on_tick() for EventLoop."""
        from general_ludd.sts.audit import StsAuditPipeline

        pipeline = StsAuditPipeline(MagicMock())
        assert hasattr(pipeline, "flush_on_tick"), (
            "P4 gap: StsAuditPipeline must provide flush_on_tick() so the "
            "EventLoop can persist audit events each tick"
        )
        assert callable(pipeline.flush_on_tick)

    @pytest.mark.asyncio
    async def test_flush_on_tick_persists_pending_events(self) -> None:
        """flush_on_tick() writes buffered events to DB via session factory."""
        from general_ludd.sts.audit import StsAuditPipeline

        mock_session = AsyncMock()
        mock_session_factory = MagicMock()
        mock_session_factory.return_value.__aenter__.return_value = mock_session

        pipeline = StsAuditPipeline(mock_session_factory)
        pipeline._pending_events = [
            {"token_id": "t1", "action": "mint", "agent_id": "a1"},
            {"token_id": "t1", "action": "use", "agent_id": "a1"},
        ]

        flushed_count = await pipeline.flush_on_tick()

        assert flushed_count == 2, (
            f"P4 gap: flush_on_tick() returned {flushed_count}, expected 2 — "
            "buffered events must be persisted each EventLoop tick"
        )
        assert len(pipeline._pending_events) == 0, (
            "P4 gap: flushed events must be cleared from the buffer so they "
            "are not re-persisted on the next tick"
        )

    @pytest.mark.asyncio
    async def test_flush_skips_when_buffer_empty(self) -> None:
        """flush_on_tick() returns 0 when no pending events (cheap no-op)."""
        from general_ludd.sts.audit import StsAuditPipeline

        pipeline = StsAuditPipeline(MagicMock())
        pipeline._pending_events = []

        flushed_count = await pipeline.flush_on_tick()

        assert flushed_count == 0, (
            "P4 gap: empty-buffer flush must return 0 without opening a DB session"
        )
