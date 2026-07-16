"""NF.7 — End-to-end STS token revocation cascade.

Exercises the complete delegation-subtree revocation flow:

    parent token revoked
      → cascade_hook fires
        → child token revoked
          → grandchild token revoked

Verifies the capability non-escalation rule (spec §2): when a parent's
token dies, every descendant minted under it MUST be revoked too —
otherwise a subagent could outlive its delegator and retain capabilities
that were only valid transitively.

Only OpenBao itself is mocked (``SecretsManager._client``). Everything
else is real: ``TokenMinter`` (AppRole setup), ``TokenStore`` (SQLite
persistence), ``TokenRevoker`` (destroy + mark revoked), ``TokenReaper``
(cascade traversal), and ``StsAuditPipeline`` (event recording).

The cascade is wired by ``daemon._build_sts_reaper`` via
``revoker.set_cascade_hook(reaper.cascade_revoke)`` — the same wiring
the production daemon lifespan uses.
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.daemon import _build_sts_reaper
from general_ludd.db.models import AgentTokenModel, Base, StsAuditModel
from general_ludd.sts.minter import TokenMinter


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


def _make_engine():
    return create_async_engine(
        "sqlite+aiosqlite://",
        echo=False,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


def _mock_secrets_manager() -> MagicMock:
    """Mock SecretsManager with a connected OpenBao client.

    ``setup_approle`` returns unique creds per role_name. The AppRole
    ``delete_role`` call is a no-op mock — we only verify it was called.
    """
    mgr = MagicMock()
    mgr._client = MagicMock()

    def _setup_approle(role_name: str) -> Any:
        creds = MagicMock()
        creds.role_id = f"role-id-{role_name}"
        creds.secret_id = f"secret-{role_name}"
        return creds

    mgr.setup_approle.side_effect = _setup_approle
    return mgr


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
async def cascade_stack(session_factory):
    """Build the full STS revocation stack via the daemon factory.

    Returns (reaper, store, revoker, audit_pipeline, minter, secrets).
    The cascade hook is wired by ``_build_sts_reaper`` so that
    ``revoker.revoke(parent)`` automatically fans out through the
    entire delegation subtree via ``reaper.cascade_revoke``.
    """
    secrets = _mock_secrets_manager()
    reaper = _build_sts_reaper(
        session_factory=session_factory,
        secrets_resolver=secrets,
    )
    store = reaper._store
    revoker = reaper._revoker
    audit = reaper._audit_pipeline
    minter = TokenMinter(secrets_manager=secrets, audit_pipeline=audit)
    return reaper, store, revoker, audit, minter, secrets


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


async def _mint_and_store(
    minter: TokenMinter,
    store: Any,
    session_factory: Any,
    agent_id: str,
    parent_agent_id: str,
    expires_in_hours: int = 1,
) -> AgentTokenModel:
    """Mint AppRole creds via the real TokenMinter and persist a token record.

    Mirrors the production dispatch flow: minter.mint() creates the AppRole
    in OpenBao, then the injector/store persists the AgentTokenModel row
    that links agent_id → parent_agent_id → role_name.
    """
    creds = await minter.mint(agent_id=agent_id, parent_agent_id=parent_agent_id)
    role_name = f"agent-{agent_id}"
    record = AgentTokenModel(
        token_id=f"tok-{agent_id}",
        agent_id=agent_id,
        parent_agent_id=parent_agent_id,
        role_name=role_name,
        role_id=creds.role_id,
        scope_hash="",
        scope_actions="[]",
        expires_at=datetime.now(UTC) + timedelta(hours=expires_in_hours),
    )
    await store.store(record)

    await _seed_audit_row(session_factory, f"tok-{agent_id}", agent_id, parent_agent_id)
    return record


async def _seed_audit_row(
    session_factory: Any,
    token_id: str,
    subject_agent_id: str,
    issuer_agent_id: str,
) -> None:
    """Create a StsAuditModel row so the audit pipeline can append events."""
    import time

    audit_row = StsAuditModel(
        token_id=token_id,
        issuer_agent_id=issuer_agent_id,
        subject_agent_id=subject_agent_id,
        spec_yaml="",
        issued_at=time.time(),
        expires_at=time.time() + 3600,
        events="[]",
        use_count=0,
    )
    async with session_factory.begin() as session:
        session.add(audit_row)


async def _fetch_token(session_factory: Any, agent_id: str) -> AgentTokenModel | None:
    async with session_factory() as session:
        result = await session.execute(
            select(AgentTokenModel).where(AgentTokenModel.agent_id == agent_id)
        )
        return result.scalar_one_or_none()


async def _fetch_all_tokens(session_factory: Any) -> dict[str, AgentTokenModel]:
    async with session_factory() as session:
        result = await session.execute(select(AgentTokenModel))
        return {row.agent_id: row for row in result.scalars().all()}


async def _fetch_audit_events(
    session_factory: Any, token_id: str
) -> list[dict[str, Any]]:
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


# ------------------------------------------------------------------
# NF.7: Parent → Child → Grandchild revocation cascade
# ------------------------------------------------------------------


class TestNF7RevocationCascade:
    """End-to-end: revoke a parent and verify the entire delegation subtree
    (children, grandchildren) is cascade-revoked.
    """

    @pytest.mark.asyncio
    async def test_revoke_parent_cascades_to_child_and_grandchild(
        self, session_factory, cascade_stack
    ):
        """Mint parent → child → grandchild, revoke parent, verify all revoked.

        Delegation tree:
            human-admin (root, NOT revoked)
              └─ parent-agent (revoked by revoke())
                   └─ child-agent (revoked by cascade)
                        └─ grandchild-agent (revoked by cascade)

        Also verifies an unrelated sibling tree is untouched.
        """
        _reaper, store, revoker, _audit, minter, secrets = cascade_stack

        parent = await _mint_and_store(
            minter, store, session_factory,
            agent_id="parent-agent", parent_agent_id="human-admin",
        )
        child = await _mint_and_store(
            minter, store, session_factory,
            agent_id="child-agent", parent_agent_id="parent-agent",
        )
        grandchild = await _mint_and_store(
            minter, store, session_factory,
            agent_id="grandchild-agent", parent_agent_id="child-agent",
        )
        unrelated = await _mint_and_store(
            minter, store, session_factory,
            agent_id="unrelated-agent", parent_agent_id="human-other",
        )

        await revoker.revoke("parent-agent")

        tokens = await _fetch_all_tokens(session_factory)
        assert tokens["parent-agent"].revoked_at is not None, (
            "parent token must be revoked by revoke()"
        )
        assert tokens["child-agent"].revoked_at is not None, (
            "child token must be cascade-revoked"
        )
        assert tokens["grandchild-agent"].revoked_at is not None, (
            "grandchild token must be cascade-revoked"
        )
        assert tokens["unrelated-agent"].revoked_at is None, (
            "unrelated sibling tree must be untouched"
        )

        destroyed_roles = {
            c.args[0]
            for c in secrets._client.auth.approle.delete_role.call_args_list
        }
        assert "agent-parent-agent" in destroyed_roles
        assert "agent-child-agent" in destroyed_roles
        assert "agent-grandchild-agent" in destroyed_roles
        assert "agent-unrelated-agent" not in destroyed_roles

    @pytest.mark.asyncio
    async def test_cascade_revokes_broad_delegation_tree(
        self, session_factory, cascade_stack
    ):
        """A parent with multiple children, each with their own grandchildren.

        Tree:
            root-agent
              ├─ child-a
              │    └─ grandchild-a1
              │    └─ grandchild-a2
              ├─ child-b
              │    └─ grandchild-b1
              └─ child-c  (leaf)

        Revoking root-agent must revoke all 6 descendants.
        """
        _reaper, store, revoker, _audit, minter, _secrets = cascade_stack

        agents = [
            ("root-agent", "human-1"),
            ("child-a", "root-agent"),
            ("grandchild-a1", "child-a"),
            ("grandchild-a2", "child-a"),
            ("child-b", "root-agent"),
            ("grandchild-b1", "child-b"),
            ("child-c", "root-agent"),
            ("outside-agent", "human-2"),
        ]
        for agent_id, parent_id in agents:
            await _mint_and_store(
                minter, store, session_factory,
                agent_id=agent_id, parent_agent_id=parent_id,
            )

        await revoker.revoke("root-agent")

        tokens = await _fetch_all_tokens(session_factory)
        for agent_id, _ in agents:
            if agent_id == "outside-agent":
                assert tokens[agent_id].revoked_at is None, (
                    f"{agent_id} must NOT be revoked (outside the subtree)"
                )
            else:
                assert tokens[agent_id].revoked_at is not None, (
                    f"{agent_id} must be cascade-revoked"
                )

    @pytest.mark.asyncio
    async def test_revoke_via_cascade_revoke_directly(
        self, session_factory, cascade_stack
    ):
        """TokenReaper.cascade_revoke traverses the full subtree even when
        the revoker's own cascade hook fires recursively.

        When cascade_revoke calls ``revoker.revoke(child)``, the revoker's
        cascade hook fires and may revoke grandchildren before the outer
        traversal reaches them — the exact count is therefore unreliable,
        but the END STATE (all descendants revoked) is the invariant.
        """
        reaper, store, _revoker, _audit, minter, _secrets = cascade_stack

        await _mint_and_store(
            minter, store, session_factory,
            agent_id="p", parent_agent_id="human",
        )
        await _mint_and_store(
            minter, store, session_factory,
            agent_id="c", parent_agent_id="p",
        )
        await _mint_and_store(
            minter, store, session_factory,
            agent_id="g", parent_agent_id="c",
        )

        revoked_count = await reaper.cascade_revoke("p")

        assert revoked_count >= 1, "cascade_revoke should revoke at least the child"
        tokens = await _fetch_all_tokens(session_factory)
        assert tokens["p"].revoked_at is None, (
            "cascade_revoke does not revoke the root itself"
        )
        assert tokens["c"].revoked_at is not None, "child must be revoked"
        assert tokens["g"].revoked_at is not None, (
            "grandchild must be revoked (by inner cascade hook or outer traversal)"
        )


# ------------------------------------------------------------------
# NF.7: Audit trail for cascade events
# ------------------------------------------------------------------


class TestNF7CascadeAuditTrail:
    """The cascade revocation records a ``revoke`` audit event for every
    token in the delegation subtree.
    """

    @pytest.mark.asyncio
    async def test_cascade_emits_revoke_audit_for_each_descendant(
        self, session_factory, cascade_stack
    ):
        """Revoke parent → verify audit trail has revoke events for parent,
        child, AND grandchild."""
        _reaper, store, revoker, _audit, minter, _secrets = cascade_stack

        await _mint_and_store(
            minter, store, session_factory,
            agent_id="audit-parent", parent_agent_id="human-audit",
        )
        await _mint_and_store(
            minter, store, session_factory,
            agent_id="audit-child", parent_agent_id="audit-parent",
        )
        await _mint_and_store(
            minter, store, session_factory,
            agent_id="audit-grandchild", parent_agent_id="audit-child",
        )

        await revoker.revoke("audit-parent")

        for agent_id, expected_parent in [
            ("audit-parent", "human-audit"),
            ("audit-child", "audit-parent"),
            ("audit-grandchild", "audit-child"),
        ]:
            events = await _fetch_audit_events(
                session_factory, f"tok-{agent_id}"
            )
            actions = [e.get("action") for e in events]
            assert "revoke" in actions, (
                f"{agent_id} must have a revoke audit event; got actions={actions}"
            )
            revoke_event = next(
                e for e in events if e.get("action") == "revoke"
            )
            assert revoke_event["agent_id"] == agent_id
            assert revoke_event["parent_agent_id"] == expected_parent

    @pytest.mark.asyncio
    async def test_cascade_audit_events_are_distinct_per_token(
        self, session_factory, cascade_stack
    ):
        """Each token's audit row receives its own revoke event — no
        cross-contamination between tokens in the cascade."""
        _reaper, store, revoker, _audit, minter, _secrets = cascade_stack

        await _mint_and_store(
            minter, store, session_factory,
            agent_id="iso-parent", parent_agent_id="human-iso",
        )
        await _mint_and_store(
            minter, store, session_factory,
            agent_id="iso-child", parent_agent_id="iso-parent",
        )

        await revoker.revoke("iso-parent")

        parent_events = await _fetch_audit_events(
            session_factory, "tok-iso-parent"
        )
        child_events = await _fetch_audit_events(
            session_factory, "tok-iso-child"
        )

        parent_revoke = [
            e for e in parent_events if e.get("action") == "revoke"
        ]
        child_revoke = [
            e for e in child_events if e.get("action") == "revoke"
        ]
        assert len(parent_revoke) == 1, (
            "parent token should have exactly one revoke event"
        )
        assert len(child_revoke) == 1, (
            "child token should have exactly one revoke event"
        )
        assert parent_revoke[0]["agent_id"] == "iso-parent"
        assert child_revoke[0]["agent_id"] == "iso-child"

    @pytest.mark.asyncio
    async def test_unrelated_token_audit_not_polluted_by_cascade(
        self, session_factory, cascade_stack
    ):
        """Revoking a subtree must not emit audit events for tokens
        outside that subtree."""
        _reaper, store, revoker, _audit, minter, _secrets = cascade_stack

        await _mint_and_store(
            minter, store, session_factory,
            agent_id="tree-root", parent_agent_id="human-1",
        )
        await _mint_and_store(
            minter, store, session_factory,
            agent_id="tree-child", parent_agent_id="tree-root",
        )
        await _mint_and_store(
            minter, store, session_factory,
            agent_id="outsider", parent_agent_id="human-2",
        )

        await revoker.revoke("tree-root")

        outsider_events = await _fetch_audit_events(
            session_factory, "tok-outsider"
        )
        actions = [e.get("action") for e in outsider_events]
        assert "revoke" not in actions, (
            "outsider token must not receive a revoke event from a cascade "
            "it is not part of"
        )

        outsider_token = await _fetch_token(session_factory, "outsider")
        assert outsider_token is not None
        assert outsider_token.revoked_at is None


# ------------------------------------------------------------------
# NF.7: Idempotency and edge cases
# ------------------------------------------------------------------


class TestNF7CascadeEdgeCases:
    """Edge cases: double-revoke, already-revoked children, empty subtree."""

    @pytest.mark.asyncio
    async def test_double_revoke_does_not_double_cascade(
        self, session_factory, cascade_stack
    ):
        """Revoking the same parent twice must not re-fire the cascade or
        emit duplicate audit events."""
        _reaper, store, revoker, _audit, minter, secrets = cascade_stack

        await _mint_and_store(
            minter, store, session_factory,
            agent_id="dbl-parent", parent_agent_id="human",
        )
        await _mint_and_store(
            minter, store, session_factory,
            agent_id="dbl-child", parent_agent_id="dbl-parent",
        )

        await revoker.revoke("dbl-parent")
        initial_delete_count = (
            len(secrets._client.auth.approle.delete_role.call_args_list)
        )
        await revoker.revoke("dbl-parent")

        final_delete_count = (
            len(secrets._client.auth.approle.delete_role.call_args_list)
        )
        assert final_delete_count == initial_delete_count, (
            "second revoke must not destroy additional AppRoles"
        )

    @pytest.mark.asyncio
    async def test_cascade_skips_already_revoked_grandchild(
        self, session_factory, cascade_stack
    ):
        """If a grandchild is already revoked before the cascade reaches it,
        the cascade prunes that subtree (no duplicate revoke, no error)."""
        reaper, store, _revoker, _audit, minter, _secrets = cascade_stack

        await _mint_and_store(
            minter, store, session_factory,
            agent_id="skip-parent", parent_agent_id="human",
        )
        await _mint_and_store(
            minter, store, session_factory,
            agent_id="skip-child", parent_agent_id="skip-parent",
        )
        await _mint_and_store(
            minter, store, session_factory,
            agent_id="skip-grandchild", parent_agent_id="skip-child",
        )

        async with session_factory.begin() as session:
            result = await session.execute(
                select(AgentTokenModel).where(
                    AgentTokenModel.agent_id == "skip-grandchild"
                )
            )
            gc = result.scalar_one()
            gc.revoked_at = datetime.now(UTC)
            session.add(gc)

        revoked = await reaper.cascade_revoke("skip-parent")

        assert revoked == 1, (
            "only skip-child should be revoked; skip-grandchild is already dead"
        )
        tokens = await _fetch_all_tokens(session_factory)
        assert tokens["skip-child"].revoked_at is not None

    @pytest.mark.asyncio
    async def test_cascade_on_leaf_parent_revokes_zero(
        self, session_factory, cascade_stack
    ):
        """cascade_revoke on a parent with no children returns 0."""
        reaper, store, _revoker, _audit, minter, _secrets = cascade_stack

        await _mint_and_store(
            minter, store, session_factory,
            agent_id="leaf-agent", parent_agent_id="human",
        )

        revoked = await reaper.cascade_revoke("leaf-agent")

        assert revoked == 0
        token = await _fetch_token(session_factory, "leaf-agent")
        assert token is not None
        assert token.revoked_at is None
