"""Integration tests for the STS TokenReaper daemon wiring.

Exercises the full pipeline built by :func:`daemon._build_sts_reaper` —
``TokenStore`` (real SQLite-backed queries), ``TokenRevoker`` (real revoke
flow with a mocked OpenBao client), ``TokenReaper`` (real sweep + cascade),
and ``StsAuditPipeline`` (real audit row writes). The only mock is the
OpenBao/SecretsManager client itself, since these tests do not assume a
running OpenBao instance.

Covers:
- End-to-end reaper sweep: mint tokens, expire some, run the reaper phase,
  verify the expired tokens are revoked and live tokens are untouched.
- Cascade revocation: revoke a parent and verify every child in its
  delegation subtree is also revoked (capability non-escalation rule).
- Audit trail: verify the reaper emits ``expire`` and ``revoke`` audit
  events that persist as ``StsAuditModel`` rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.daemon import _build_sts_reaper
from general_ludd.db.models import AgentTokenModel, Base, StsAuditModel


def _make_engine():
    return create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


def _mock_secrets_manager() -> MagicMock:
    mgr = MagicMock()
    mgr._client = MagicMock()
    return mgr


def _token_row(
    token_id: str,
    agent_id: str,
    parent_agent_id: str = "root",
    expires_at: datetime | None = None,
    revoked_at: datetime | None = None,
) -> AgentTokenModel:
    return AgentTokenModel(
        token_id=token_id,
        agent_id=agent_id,
        parent_agent_id=parent_agent_id,
        role_name=f"agent-{agent_id}",
        role_id=f"role-{agent_id}",
        scope_hash="",
        scope_actions="[]",
        expires_at=expires_at,
        revoked_at=revoked_at,
    )


def _audit_row(token_id: str, subject_agent_id: str, issuer_agent_id: str = "root") -> StsAuditModel:
    import time

    return StsAuditModel(
        token_id=token_id,
        issuer_agent_id=issuer_agent_id,
        subject_agent_id=subject_agent_id,
        spec_yaml="",
        issued_at=time.time(),
        expires_at=time.time() + 3600,
        events="[]",
        use_count=0,
    )


@pytest_asyncio.fixture
async def session_factory():
    engine = _make_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        yield factory
    finally:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await engine.dispose()


@pytest_asyncio.fixture
async def reaper_pipeline(session_factory):
    """Build the full STS reaper stack via the daemon factory.

    Returns a tuple of (reaper, store, revoker, audit_pipeline, secrets).
    Only OpenBao itself is mocked.
    """
    secrets = _mock_secrets_manager()
    reaper = _build_sts_reaper(
        session_factory=session_factory,
        secrets_resolver=secrets,
    )
    store = reaper._store
    revoker = reaper._revoker
    audit = reaper._audit_pipeline
    return reaper, store, revoker, audit, secrets


async def _seed_rows(session_factory, *rows: Any) -> None:
    async with session_factory.begin() as session:
        for row in rows:
            session.add(row)


async def _fetch_all_tokens(session_factory) -> list[AgentTokenModel]:
    from sqlalchemy import select

    async with session_factory() as session:
        result = await session.execute(select(AgentTokenModel))
        return list(result.scalars().all())


async def _fetch_audit_events(session_factory, token_id: str) -> list[dict[str, Any]]:
    import json as _json

    from sqlalchemy import select

    async with session_factory() as session:
        result = await session.execute(
            select(StsAuditModel).where(StsAuditModel.token_id == token_id)
        )
        row = result.scalar_one_or_none()
        if row is None:
            return []
        try:
            return _json.loads(row.events)
        except Exception:
            return []


class TestReapExpiredEndToEnd:
    """End-to-end: mint tokens → expire some → trigger reaper → verify."""

    @pytest.mark.asyncio
    async def test_reap_revokes_only_expired_live_tokens(
        self, session_factory, reaper_pipeline
    ):
        reaper, _store, _revoker, _audit, secrets = reaper_pipeline
        now = datetime.now(UTC)

        expired_a = _token_row(
            "tok-a", "agent-a",
            expires_at=now - timedelta(minutes=10),
        )
        expired_b = _token_row(
            "tok-b", "agent-b",
            expires_at=now - timedelta(minutes=1),
        )
        live = _token_row(
            "tok-live", "agent-live",
            expires_at=now + timedelta(hours=1),
        )
        already_revoked = _token_row(
            "tok-old", "agent-old",
            expires_at=now - timedelta(hours=1),
            revoked_at=now - timedelta(hours=2),
        )
        await _seed_rows(
            session_factory, expired_a, expired_b, live, already_revoked,
        )
        for tok in (expired_a, expired_b, live, already_revoked):
            await _seed_rows(
                session_factory,
                _audit_row(tok.token_id, tok.agent_id, tok.parent_agent_id),
            )

        reaped = await reaper.reap_expired(now=now)

        assert reaped == 2
        rows = {r.token_id: r for r in await _fetch_all_tokens(session_factory)}
        assert rows["tok-a"].revoked_at is not None
        assert rows["tok-b"].revoked_at is not None
        assert rows["tok-live"].revoked_at is None
        assert rows["tok-old"].revoked_at is not None

        destroy_calls = secrets._client.auth.approle.delete_role.call_args_list
        destroyed_roles = {c.args[0] for c in destroy_calls}
        assert destroyed_roles == {"agent-agent-a", "agent-agent-b"}

    @pytest.mark.asyncio
    async def test_reap_is_idempotent_across_runs(self, session_factory, reaper_pipeline):
        reaper, *_ = reaper_pipeline
        now = datetime.now(UTC)
        expired = _token_row("tok-x", "agent-x", expires_at=now - timedelta(minutes=5))
        await _seed_rows(session_factory, expired)
        await _seed_rows(
            session_factory, _audit_row(expired.token_id, expired.agent_id)
        )

        first = await reaper.reap_expired(now=now)
        second = await reaper.reap_expired(now=now)

        assert first == 1
        assert second == 0
        rows = await _fetch_all_tokens(session_factory)
        assert len(rows) == 1
        assert rows[0].revoked_at is not None


class TestCascadeRevoke:
    """Revoke a parent → all descendants in the delegation subtree are revoked."""

    @pytest.mark.asyncio
    async def test_cascade_revokes_full_subtree(
        self, session_factory, reaper_pipeline
    ):
        reaper, _store, _revoker, _audit, _secrets = reaper_pipeline
        parent = _token_row("tok-parent", "agent-parent", parent_agent_id="human-1")
        child1 = _token_row("tok-c1", "agent-c1", parent_agent_id="agent-parent")
        child2 = _token_row("tok-c2", "agent-c2", parent_agent_id="agent-parent")
        grandchild = _token_row("tok-g1", "agent-g1", parent_agent_id="agent-c1")
        sibling_tree = _token_row(
            "tok-other", "agent-other", parent_agent_id="human-2"
        )
        await _seed_rows(
            session_factory, parent, child1, child2, grandchild, sibling_tree,
        )

        revoked_total = await reaper.cascade_revoke("agent-parent")

        assert revoked_total >= 2
        rows = {r.agent_id: r for r in await _fetch_all_tokens(session_factory)}
        assert rows["agent-parent"].revoked_at is None
        assert rows["agent-c1"].revoked_at is not None
        assert rows["agent-c2"].revoked_at is not None
        assert rows["agent-g1"].revoked_at is not None
        assert rows["agent-other"].revoked_at is None

    @pytest.mark.asyncio
    async def test_cascade_skips_already_revoked_subtree(
        self, session_factory, reaper_pipeline
    ):
        reaper, *_ = reaper_pipeline
        now = datetime.now(UTC)
        live_child = _token_row("tok-lc", "agent-lc", parent_agent_id="agent-root")
        dead_child = _token_row(
            "tok-dc", "agent-dc", parent_agent_id="agent-root",
            revoked_at=now - timedelta(hours=1),
        )
        grandchild_of_dead = _token_row(
            "tok-gd", "agent-gd", parent_agent_id="agent-dc",
        )
        await _seed_rows(
            session_factory, live_child, dead_child, grandchild_of_dead,
        )

        revoked_total = await reaper.cascade_revoke("agent-root")

        assert revoked_total == 1
        rows = {r.agent_id: r for r in await _fetch_all_tokens(session_factory)}
        assert rows["agent-lc"].revoked_at is not None
        assert rows["agent-gd"].revoked_at is None

    @pytest.mark.asyncio
    async def test_revoker_cascade_hook_fires_on_parent_revoke(
        self, session_factory, reaper_pipeline
    ):
        _reaper, _store, revoker, _audit, _secrets = reaper_pipeline
        parent = _token_row("tok-p", "agent-p", parent_agent_id="human-1")
        child = _token_row("tok-c", "agent-c", parent_agent_id="agent-p")
        await _seed_rows(session_factory, parent, child)

        assert revoker._cascade_hook is not None

        await revoker.revoke("agent-p")

        rows = {r.agent_id: r for r in await _fetch_all_tokens(session_factory)}
        assert rows["agent-p"].revoked_at is not None
        assert rows["agent-c"].revoked_at is not None


class TestAuditTrail:
    """The reaper generates audit events that persist as StsAuditModel rows."""

    @pytest.mark.asyncio
    async def test_reap_emits_expire_events(self, session_factory, reaper_pipeline):
        reaper, *_ = reaper_pipeline
        now = datetime.now(UTC)
        expired = _token_row(
            "tok-e1", "agent-e1", parent_agent_id="parent-e1",
            expires_at=now - timedelta(minutes=5),
        )
        await _seed_rows(session_factory, expired)
        await _seed_rows(
            session_factory, _audit_row(expired.token_id, expired.agent_id,
                                        issuer_agent_id="parent-e1"),
        )

        await reaper.reap_expired(now=now)

        events = await _fetch_audit_events(session_factory, "tok-e1")
        actions = [e.get("action") for e in events]
        assert "expire" in actions
        expire_event = next(e for e in events if e.get("action") == "expire")
        assert expire_event["agent_id"] == "agent-e1"
        assert expire_event["parent_agent_id"] == "parent-e1"

    @pytest.mark.asyncio
    async def test_cascade_revokes_emit_revoke_events(
        self, session_factory, reaper_pipeline
    ):
        reaper, _store, _revoker, _audit, _secrets = reaper_pipeline
        parent = _token_row("tok-cp", "agent-cp", parent_agent_id="human-1")
        child = _token_row("tok-cc", "agent-cc", parent_agent_id="agent-cp")
        await _seed_rows(session_factory, parent, child)
        await _seed_rows(
            session_factory,
            _audit_row(parent.token_id, parent.agent_id, "human-1"),
            _audit_row(child.token_id, child.agent_id, "agent-cp"),
        )

        await reaper.cascade_revoke("agent-cp")

        child_events = await _fetch_audit_events(session_factory, "tok-cc")
        actions = [e.get("action") for e in child_events]
        assert "revoke" in actions
        revoke_event = next(e for e in child_events if e.get("action") == "revoke")
        assert revoke_event["agent_id"] == "agent-cc"
        assert revoke_event["parent_agent_id"] == "agent-cp"

    @pytest.mark.asyncio
    async def test_reap_without_audit_row_still_completes(
        self, session_factory, reaper_pipeline
    ):
        reaper, *_ = reaper_pipeline
        now = datetime.now(UTC)
        expired = _token_row(
            "tok-noaudit", "agent-noaudit",
            expires_at=now - timedelta(minutes=1),
        )
        await _seed_rows(session_factory, expired)

        reaped = await reaper.reap_expired(now=now)

        assert reaped == 1
        rows = await _fetch_all_tokens(session_factory)
        assert rows[0].revoked_at is not None


class TestReaperPhaseWiring:
    """EventLoop._phase_reap_expired_sts_tokens reads daemon_state['_sts_reaper']."""

    @pytest.mark.asyncio
    async def test_phase_runs_reaper_from_daemon_state(
        self, session_factory, reaper_pipeline
    ):
        from general_ludd.event_loop.loop import EventLoop

        reaper, *_ = reaper_pipeline
        now = datetime.now(UTC)
        expired = _token_row(
            "tok-phase", "agent-phase",
            expires_at=now - timedelta(minutes=5),
        )
        await _seed_rows(session_factory, expired)

        daemon_state: dict[str, Any] = {"_sts_reaper": reaper}
        loop = EventLoop(
            config={"sts_reap_interval_ticks": 1},
            daemon_state=daemon_state,
        )
        loop._total_ticks = 0

        await loop._phase_reap_expired_sts_tokens()

        assert loop._tick_metrics.get("sts_tokens_reaped") == 1
        rows = await _fetch_all_tokens(session_factory)
        assert rows[0].revoked_at is not None

    @pytest.mark.asyncio
    async def test_phase_skips_when_no_reaper_wired(self, session_factory):
        from general_ludd.event_loop.loop import EventLoop

        loop = EventLoop(
            config={"sts_reap_interval_ticks": 1},
            daemon_state={},
        )
        loop._total_ticks = 0

        await loop._phase_reap_expired_sts_tokens()

        assert "sts_tokens_reaped" not in loop._tick_metrics

    @pytest.mark.asyncio
    async def test_phase_respects_interval_gate(self, session_factory, reaper_pipeline):
        from general_ludd.event_loop.loop import EventLoop

        reaper, *_ = reaper_pipeline
        daemon_state: dict[str, Any] = {"_sts_reaper": reaper}
        loop = EventLoop(
            config={"sts_reap_interval_ticks": 60},
            daemon_state=daemon_state,
        )
        loop._total_ticks = 5

        await loop._phase_reap_expired_sts_tokens()

        assert "sts_tokens_reaped" not in loop._tick_metrics
