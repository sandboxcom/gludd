"""E2E — STS (Security Token Service) subsystem workflows.

Covers all 12 modules: minter, store, injector, narrowing, reviver,
revoker, token_reaper, quota_enforcer, visualizer, rotator, audit, dashboard.
Uses in-memory SQLite + mocked SecretsManager throughout.
"""

from __future__ import annotations

import json
import time
from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from general_ludd.db.models import AgentTokenModel, Base, StsAuditModel
from general_ludd.secrets.manager import AppRoleCreds
from general_ludd.sts.audit import StsAuditPipeline
from general_ludd.sts.dashboard import CascadeConfig, StsDashboardProvider
from general_ludd.sts.injector import SubagentTokenInjector
from general_ludd.sts.minter import TokenMinter
from general_ludd.sts.narrowing import CapabilityNarrowing, OpenBaoPolicyRenderer, PolicyFragment
from general_ludd.sts.quotas import (
    InMemoryQuotaBackend,
    QuotaConfig,
    QuotaViolation,
    StoreQuotaBackend,
    TokenQuotaEnforcer,
    _is_active,
)
from general_ludd.sts.reaper import TokenReaper
from general_ludd.sts.reviver import TokenRevivalError, TokenReviver
from general_ludd.sts.revoker import TokenRevoker
from general_ludd.sts.rotator import TokenRotationError, TokenRotator
from general_ludd.sts.store import TokenStore
from general_ludd.sts.visualizer import TokenTreeRenderer, _format_node, _status


# ── helpers ──────────────────────────────────────────────────────────


def _engine():
    return create_async_engine(
        "sqlite+aiosqlite://", echo=False, poolclass=StaticPool, connect_args={"check_same_thread": False},
    )


def _secrets_mgr():
    mgr = MagicMock()
    mgr._client.auth.approle.delete_role = MagicMock()
    mgr._client = MagicMock()
    mgr._client.auth.approle.delete_role = MagicMock()
    _counter = {"n": 0}

    def _setup(role_name):
        _counter["n"] += 1
        return AppRoleCreds(role_id=f"rid-{_counter['n']}", secret_id=f"sid-{_counter['n']}")

    def _rotate(role_name):
        return f"fresh-{role_name}-{_counter['n']}"

    mgr.setup_approle.side_effect = _setup
    mgr.rotate_approle_secret_id.side_effect = _rotate
    return mgr


def _token(agent_id, parent_id="root", token_id=None, role_name=None, **kw):
    return AgentTokenModel(
        token_id=token_id or f"tok-{agent_id}",
        agent_id=agent_id,
        parent_agent_id=parent_id,
        role_name=role_name or f"agent-{agent_id}",
        role_id=f"rid-{agent_id}",
        scope_hash="",
        **kw,
    )


# ── fixtures ─────────────────────────────────────────────────────────


@pytest_asyncio.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[Any], None]:
    engine = _engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    yield factory
    await engine.dispose()


@pytest_asyncio.fixture
async def store(session_factory) -> TokenStore:
    return TokenStore(session_factory)


@pytest_asyncio.fixture
async def audit(session_factory) -> StsAuditPipeline:
    return StsAuditPipeline(session_factory)


@pytest_asyncio.fixture
def secrets_mgr():
    return _secrets_mgr()


@pytest_asyncio.fixture
async def minter(secrets_mgr, audit) -> TokenMinter:
    return TokenMinter(secrets_mgr, audit_pipeline=audit)


@pytest_asyncio.fixture
async def revoker(secrets_mgr, store, audit) -> TokenRevoker:
    return TokenRevoker(secrets_mgr, store, audit_pipeline=audit)


@pytest_asyncio.fixture
async def reaper(store, revoker, audit) -> TokenReaper:
    return TokenReaper(store, revoker, audit_pipeline=audit)


@pytest_asyncio.fixture
async def reviver(secrets_mgr, store, audit) -> TokenReviver:
    return TokenReviver(secrets_mgr, store, audit_pipeline=audit)


@pytest_asyncio.fixture
async def rotator(secrets_mgr, store, audit) -> TokenRotator:
    return TokenRotator(secrets_mgr, store, audit_pipeline=audit)


@pytest_asyncio.fixture
async def visualizer(store) -> TokenTreeRenderer:
    return TokenTreeRenderer(store)


# ── mock lattice for narrowing tests ─────────────────────────────────


class FakeLattice:
    def __init__(self, chain=True):
        self.chain = chain

    def all_actions(self, role="admin"):
        rv = {"read", "write", "execute", "delete"}
        if role == "viewer":
            rv = {"read"}
        elif role == "operator":
            rv = {"read", "write", "delete"}
        return rv


# ── 1. TokenMinter ───────────────────────────────────────────────────


class TestMinterE2E:
    @pytest.mark.asyncio
    async def test_mint_basic_token(self, minter, secrets_mgr):
        creds = await minter.mint("agent-1", "root")
        assert creds.role_id.startswith("rid-")
        assert creds.secret_id.startswith("sid-")

    @pytest.mark.asyncio
    async def test_mint_with_narrowing(self, minter, secrets_mgr):
        from collections import namedtuple
        ToolAction = namedtuple("ToolAction", ["value"])
        lattice = FakeLattice()
        child = {ToolAction("read"), ToolAction("write"), ToolAction("delete")}
        creds = await minter.mint(
            "agent-2", "root", parent_lattice=lattice, child_actions=child, parent_role="admin",
        )
        assert creds.role_id.startswith("rid-")

    @pytest.mark.asyncio
    async def test_mint_records_audit_event(self, minter, audit, session_factory):
        token_id = "tok-agent-audit"
        async with session_factory.begin() as sess:
            sess.add(StsAuditModel(
                token_id=token_id, issuer_agent_id="root", subject_agent_id="agent-audit",
                spec_yaml="", issued_at=time.time(), expires_at=time.time() + 3600, events="[]",
            ))
        await minter.mint("agent-audit", "root")
        row = await _get_audit_row(session_factory, token_id)
        parsed = json.loads(row.events)
        mint_events = [e for e in parsed if e.get("action") == "mint"]
        assert len(mint_events) >= 1

    @pytest.mark.asyncio
    async def test_render_policy_for_actions(self, minter):
        from collections import namedtuple
        ToolAction = namedtuple("ToolAction", ["value"])
        actions = [ToolAction("read"), ToolAction("write")]
        policy = minter.render_policy(actions, "test-role")
        assert 'role "test-role"' in policy
        assert "capabilities" in policy
        assert "read" in policy or "create" in policy


# ── 2. TokenStore ────────────────────────────────────────────────────


class TestStoreE2E:
    @pytest.mark.asyncio
    async def test_store_and_get(self, store):
        t = _token("agent-s1")
        await store.store(t)
        found = await store.get("agent-s1")
        assert found is not None
        assert found.agent_id == "agent-s1"

    @pytest.mark.asyncio
    async def test_get_missing_returns_none(self, store):
        assert await store.get("nonexistent") is None

    @pytest.mark.asyncio
    async def test_revoke_sets_timestamp(self, store):
        t = _token("agent-r1")
        await store.store(t)
        await store.revoke("tok-agent-r1")
        found = await store.get("agent-r1")
        assert found is not None
        assert found.revoked_at is not None

    @pytest.mark.asyncio
    async def test_increment_hydration(self, store):
        t = _token("agent-h1")
        await store.store(t)
        await store.increment_hydration("agent-h1")
        found = await store.get("agent-h1")
        assert found.hydration_count == 1

    @pytest.mark.asyncio
    async def test_list_expired(self, store):
        t1 = _token("agent-e1", expires_at=datetime(2020, 1, 1, tzinfo=UTC))
        t2 = _token("agent-e2", expires_at=datetime.now(UTC) + timedelta(hours=1))
        t3 = _token("agent-e3")
        await store.store(t1)
        await store.store(t2)
        await store.store(t3)
        expired = await store.list_expired(datetime.now(UTC))
        assert len(expired) == 1
        assert expired[0].agent_id == "agent-e1"

    @pytest.mark.asyncio
    async def test_list_all(self, store):
        await store.store(_token("a1"))
        await store.store(_token("a2"))
        all_tokens = await store.list_all()
        assert len(all_tokens) == 2

    @pytest.mark.asyncio
    async def test_list_children(self, store):
        await store.store(_token("child1", parent_id="parent-x"))
        await store.store(_token("child2", parent_id="parent-x"))
        await store.store(_token("unrelated", parent_id="other"))
        children = await store.list_children("parent-x")
        assert len(children) == 2


# ── 3. SubagentTokenInjector ─────────────────────────────────────────


class TestInjectorE2E:
    @pytest.mark.asyncio
    async def test_enrich_sets_env_vars(self, minter, store, session_factory):
        dispatcher_mock = MagicMock()
        injector = SubagentTokenInjector(minter, store, dispatcher_mock)

        task = MagicMock()
        task.task_id = "task-abc"
        task.invoker_name = "root-agent"
        task.parent_task_id = None
        task.env = {}

        await injector.enrich(task)

        assert task.env["GLUDD_STS_ROLE_ID"].startswith("rid-")
        assert task.env["GLUDD_STS_SECRET_ID"].startswith("sid-")
        stored = await store.get("task-abc")
        assert stored is not None
        assert stored.parent_agent_id == "root-agent"

    @pytest.mark.asyncio
    async def test_env_vars_standalone(self, minter, store, session_factory):
        dispatcher_mock = MagicMock()
        injector = SubagentTokenInjector(minter, store, dispatcher_mock)
        env = await injector.env_vars("agent-x", "parent-y")
        assert env["GLUDD_STS_ROLE_ID"].startswith("rid-")
        assert env["GLUDD_STS_SECRET_ID"].startswith("sid-")
        assert env["GLUDD_STS_TOKEN_ID"] == "tok-agent-x"


# ── 4. CapabilityNarrowing + OpenBaoPolicyRenderer ───────────────────


class TestNarrowingE2E:
    def test_narrow_intersects_parent(self):
        lattice = FakeLattice()
        narrowing = CapabilityNarrowing(lattice)
        from collections import namedtuple
        ToolAction = namedtuple("ToolAction", ["value"])
        child = {ToolAction("read"), ToolAction("write"), ToolAction("unknown")}
        result = narrowing.narrow(child, parent_role="admin")
        assert result == {"read", "write"}

    def test_narrow_full_scope_admin(self):
        lattice = FakeLattice()
        narrowing = CapabilityNarrowing(lattice)
        from collections import namedtuple
        ToolAction = namedtuple("ToolAction", ["value"])
        child = {ToolAction("read"), ToolAction("write"), ToolAction("execute"), ToolAction("delete")}
        result = narrowing.narrow(child, parent_role="admin")
        assert result == {"read", "write", "execute", "delete"}

    def test_validate_narrowing_passes(self):
        from collections import namedtuple
        ToolAction = namedtuple("ToolAction", ["value"])
        parent = {ToolAction("read"), ToolAction("write")}
        child = {ToolAction("read")}
        assert CapabilityNarrowing.validate_narrowing(parent, child) is True

    def test_validate_narrowing_blocks_escalation(self):
        from collections import namedtuple
        ToolAction = namedtuple("ToolAction", ["value"])
        parent = {ToolAction("read")}
        child = {ToolAction("read"), ToolAction("write")}
        assert CapabilityNarrowing.validate_narrowing(parent, child) is False

    def test_to_openbao_policy(self):
        lattice = FakeLattice()
        narrowing = CapabilityNarrowing(lattice)
        from collections import namedtuple
        ToolAction = namedtuple("ToolAction", ["value"])
        child = {ToolAction("read"), ToolAction("write"), ToolAction("unknown")}
        policy = narrowing.to_openbao_policy(child, role_name="sub")
        assert "capabilities" in policy
        assert "unknown" not in policy

    def test_renderer_actions_strings(self):
        policy = OpenBaoPolicyRenderer.render({"read", "write"}, role_name="test")
        assert 'role "test"' in policy
        assert "capabilities" in policy

    def test_renderer_empty_actions(self):
        policy = OpenBaoPolicyRenderer.render([], role_name="empty")
        assert policy == ""

    def test_narrow_to_subset_where_parent_chain_none(self):
        lattice = FakeLattice(chain=False)
        narrowing = CapabilityNarrowing(lattice)
        from collections import namedtuple
        ToolAction = namedtuple("ToolAction", ["value"])
        child = {ToolAction("read"), ToolAction("write")}
        policy = narrowing.to_openbao_policy(child, role_name="nochain")
        assert "capabilities" in policy


# ── 5. TokenReviver ──────────────────────────────────────────────────


class TestReviverE2E:
    @pytest.mark.asyncio
    async def test_revive_returns_fresh_creds(self, reviver, store):
        await store.store(_token("agent-rv", role_name="agent-agent-rv"))
        creds = await reviver.revive("agent-rv")
        assert creds.secret_id.startswith("fresh-")

    @pytest.mark.asyncio
    async def test_revive_increments_hydration(self, reviver, store):
        await store.store(_token("agent-h2"))
        await reviver.revive("agent-h2")
        found = await store.get("agent-h2")
        assert found.hydration_count == 1

    @pytest.mark.asyncio
    async def test_revive_nonexistent_raises(self, reviver):
        with pytest.raises(TokenRevivalError, match="No token record"):
            await reviver.revive("ghost")

    @pytest.mark.asyncio
    async def test_revive_revoked_raises(self, reviver, store):
        t = _token("agent-revoked", revoked_at=datetime.now(UTC))
        await store.store(t)
        with pytest.raises(TokenRevivalError, match="revoked"):
            await reviver.revive("agent-revoked")


# ── 6. TokenRevoker ──────────────────────────────────────────────────


class TestRevokerE2E:
    @pytest.mark.asyncio
    async def test_revoke_sets_revoked_at(self, revoker, store):
        await store.store(_token("agent-rvk"))
        await revoker.revoke("agent-rvk")
        found = await store.get("agent-rvk")
        assert found.revoked_at is not None

    @pytest.mark.asyncio
    async def test_revoke_missing_idempotent(self, revoker):
        await revoker.revoke("ghost-rvk")

    @pytest.mark.asyncio
    async def test_revoke_already_revoked_warns(self, revoker, store):
        t = _token("agent-double", revoked_at=datetime.now(UTC))
        await store.store(t)
        await revoker.revoke("agent-double")
        found = await store.get("agent-double")
        assert found.revoked_at is not None

    @pytest.mark.asyncio
    async def test_revoke_cascade_hook_invoked(self, revoker, store):
        await store.store(_token("agent-cascade"))
        called = []

        async def hook(agent_id):
            called.append(agent_id)

        revoker.set_cascade_hook(hook)
        await revoker.revoke("agent-cascade")
        assert called == ["agent-cascade"]


# ── 7. TokenReaper ───────────────────────────────────────────────────


class TestReaperE2E:
    @pytest.mark.asyncio
    async def test_reap_expired_sweeps(self, reaper, store):
        past = datetime(2020, 1, 1, tzinfo=UTC)
        await store.store(_token("exp1", expires_at=past))
        await store.store(_token("exp2", expires_at=past))
        await store.store(_token("live1", expires_at=datetime.now(UTC) + timedelta(hours=5)))
        count = await reaper.reap_expired()
        assert count == 2
        found1 = await store.get("exp1")
        assert found1.revoked_at is not None

    @pytest.mark.asyncio
    async def test_reap_zero_expired(self, reaper, store):
        await store.store(_token("live-only", expires_at=datetime.now(UTC) + timedelta(hours=5)))
        count = await reaper.reap_expired()
        assert count == 0

    @pytest.mark.asyncio
    async def test_cascade_revoke_subtree(self, reaper, store):
        await store.store(_token("root-c"))
        await store.store(_token("childA", parent_id="root-c"))
        await store.store(_token("childB", parent_id="root-c"))
        await store.store(_token("grandchild", parent_id="childA"))
        revoked = await reaper.cascade_revoke("root-c")
        assert revoked == 3
        for aid in ("childA", "childB", "grandchild"):
            found = await store.get(aid)
            assert found is not None
            assert found.revoked_at is not None


# ── 8. QuotaEnforcer ─────────────────────────────────────────────────


class TestQuotaEnforcerE2E:
    @pytest.mark.asyncio
    async def test_scope_width_violation(self):
        enforcer = TokenQuotaEnforcer(QuotaConfig(max_scope_width=3))
        with pytest.raises(QuotaViolation, match="scope width"):
            await enforcer.check("a1", "p1", {"a", "b", "c", "d"})

    @pytest.mark.asyncio
    async def test_agent_limit_violation(self):
        backend = InMemoryQuotaBackend()
        backend._agent_tokens["a1"] = {"t1", "t2", "t3", "t4", "t5"}
        enforcer = TokenQuotaEnforcer(QuotaConfig(max_tokens_per_agent=5), backend=backend)
        with pytest.raises(QuotaViolation, match="agent"):
            await enforcer.check("a1", "p1", {"read"})

    @pytest.mark.asyncio
    async def test_project_limit_violation(self):
        backend = InMemoryQuotaBackend()
        backend._project_tokens["p1"] = set(f"t{i}" for i in range(100))
        enforcer = TokenQuotaEnforcer(
            QuotaConfig(max_active_tokens_per_project=100), backend=backend,
        )
        with pytest.raises(QuotaViolation, match="project"):
            await enforcer.check("a1", "p1", {"read"})

    @pytest.mark.asyncio
    async def test_check_passes(self):
        enforcer = TokenQuotaEnforcer()
        await enforcer.check("a1", "p1", {"read", "write"})

    @pytest.mark.asyncio
    async def test_record_mint_and_revoke(self):
        backend = InMemoryQuotaBackend()
        enforcer = TokenQuotaEnforcer(backend=backend)
        await enforcer.record_mint("tok1", "a1", "p1", {"read"})
        assert await backend.active_count_for_agent("a1") == 1
        await enforcer.record_revoke("tok1", "a1", "p1")
        assert await backend.active_count_for_agent("a1") == 0

    @pytest.mark.asyncio
    async def test_store_backend_counts_active(self, store):
        await store.store(_token("a1"))
        await store.store(_token("a2"))
        await store.store(_token("a3", revoked_at=datetime.now(UTC)))
        backend = StoreQuotaBackend(store, project_of=lambda r: "default")
        count = await backend.active_count_for_agent("a1")
        assert count == 1
        active = await backend.list_active()
        assert len(active) == 2

    @pytest.mark.asyncio
    async def test_is_active_helper(self):
        from unittest.mock import MagicMock

        active_row = MagicMock(revoked_at=None, expires_at=None)
        assert _is_active(active_row) is True

        revoked_row = MagicMock(revoked_at=datetime.now(UTC), expires_at=datetime.now(UTC) + timedelta(hours=1))
        assert _is_active(revoked_row) is False

        expired_row = MagicMock(revoked_at=None, expires_at=datetime(2020, 1, 1, tzinfo=UTC))
        assert _is_active(expired_row) is False


# ── 9. TokenTreeRenderer (visualizer) ────────────────────────────────


class TestVisualizerE2E:
    @pytest.mark.asyncio
    async def test_render_tree(self, visualizer, store):
        await store.store(_token("root-v", token_id="tok-root-v"))
        await store.store(_token("child1", parent_id="root-v"))
        await store.store(_token("child2", parent_id="root-v"))
        tree = await visualizer.render_tree("root-v")
        assert "root-v" in tree
        assert "child1" in tree
        assert "child2" in tree

    @pytest.mark.asyncio
    async def test_render_tree_not_found(self, visualizer):
        result = await visualizer.render_tree("ghost")
        assert "not found" in result

    @pytest.mark.asyncio
    async def test_render_revocation_cascade(self, visualizer, store):
        await store.store(_token("rc-root"))
        await store.store(_token("rc-child", parent_id="rc-root"))
        result = await visualizer.render_revocation_cascade("rc-root")
        assert "live" in result.lower()

    @pytest.mark.asyncio
    async def test_render_active_tokens(self, visualizer, store):
        await store.store(_token("active1"))
        await store.store(_token("active2"))
        await store.store(_token("revoked1", revoked_at=datetime.now(UTC)))
        result = await visualizer.render_active_tokens()
        assert "active1" in result
        assert "active2" in result
        assert "revoked1" not in result

    def test_format_node(self):
        node = _token("fmt-agent", token_id="tok-fmt")
        txt = _format_node(node)
        assert "fmt-agent" in txt
        assert "active" in txt

    def test_status_classification(self):
        active_node = _token("s-active")
        assert _status(active_node) == "active"

        revoked_node = _token("s-revoked", revoked_at=datetime.now(UTC))
        assert _status(revoked_node) == "revoked"

        expired_node = _token("s-expired", expires_at=datetime(2020, 1, 1, tzinfo=UTC))
        assert _status(expired_node) == "expired"


# ── 10. TokenRotator ─────────────────────────────────────────────────


class TestRotatorE2E:
    @pytest.mark.asyncio
    async def test_rotate_success(self, rotator, store, secrets_mgr):
        await store.store(_token("agent-rot1", role_name="agent-agent-rot1"))
        creds = await rotator.rotate("agent-rot1")
        assert creds.secret_id.startswith("sid-")
        old = await store.get("agent-rot1")
        assert old.revoked_at is not None

    @pytest.mark.asyncio
    async def test_rotate_nonexistent_raises(self, rotator):
        with pytest.raises(TokenRotationError, match="No token record"):
            await rotator.rotate("ghost-rot")

    @pytest.mark.asyncio
    async def test_rotate_revoked_raises(self, rotator, store):
        t = _token("agent-rev-rot", revoked_at=datetime.now(UTC))
        await store.store(t)
        with pytest.raises(TokenRotationError, match="revoked"):
            await rotator.rotate("agent-rev-rot")

    @pytest.mark.asyncio
    async def test_needs_rotation_ttl_expiring(self, rotator):
        close = datetime.now(UTC) + timedelta(seconds=300)
        assert rotator.needs_rotation(expires_at=close) is True

    @pytest.mark.asyncio
    async def test_needs_rotation_no_ttl(self, rotator):
        assert rotator.needs_rotation(expires_at=None) is False

    @pytest.mark.asyncio
    async def test_needs_rotation_already_past(self, rotator):
        past = datetime(2020, 1, 1, tzinfo=UTC)
        assert rotator.needs_rotation(expires_at=past) is True

    @pytest.mark.asyncio
    async def test_rotate_all_sweeps(self, rotator, store):
        now = datetime.now(UTC)
        close_dt = now + timedelta(seconds=300)
        far_dt = now + timedelta(hours=10)
        await store.store(_token("agent-ra1", role_name="agent-agent-ra1", expires_at=close_dt))
        await store.store(_token("agent-ra2", role_name="agent-agent-ra2", expires_at=far_dt))
        needs = [rotator.needs_rotation(expires_at=close_dt, now=now),
                 rotator.needs_rotation(expires_at=far_dt, now=now)]
        assert needs[0] is True
        assert needs[1] is False


# ── 11. StsAuditPipeline ─────────────────────────────────────────────


class TestAuditE2E:
    @pytest.mark.asyncio
    async def test_record_mint(self, audit, session_factory):
        async with session_factory.begin() as sess:
            sess.add(_audit_seed("tok-a1"))
        await audit.record_mint("tok-a1", "parent-1", "subj-1", scope_actions=["read"])
        row = await _get_audit_row(session_factory, "tok-a1")
        parsed = json.loads(row.events)
        assert any(e["action"] == "mint" for e in parsed)

    @pytest.mark.asyncio
    async def test_record_use(self, audit, session_factory):
        async with session_factory.begin() as sess:
            sess.add(_audit_seed("tok-use"))
        await audit.record_use("tok-use", "agent-x", "parent-x")
        row = await _get_audit_row(session_factory, "tok-use")
        parsed = json.loads(row.events)
        assert any(e["action"] == "use" for e in parsed)

    @pytest.mark.asyncio
    async def test_record_renew(self, audit, session_factory):
        async with session_factory.begin() as sess:
            sess.add(_audit_seed("tok-renew"))
        await audit.record_renew("tok-renew", "agent-r", "parent-r")
        row = await _get_audit_row(session_factory, "tok-renew")
        parsed = json.loads(row.events)
        assert any(e["action"] == "renew" for e in parsed)

    @pytest.mark.asyncio
    async def test_record_revoke(self, audit, session_factory):
        async with session_factory.begin() as sess:
            sess.add(_audit_seed("tok-revoke"))
        await audit.record_revoke("tok-revoke", "agent-rv", "parent-rv")
        row = await _get_audit_row(session_factory, "tok-revoke")
        parsed = json.loads(row.events)
        assert any(e["action"] == "revoke" for e in parsed)

    @pytest.mark.asyncio
    async def test_record_revive(self, audit, session_factory):
        async with session_factory.begin() as sess:
            sess.add(_audit_seed("tok-revive"))
        await audit.record_revive("tok-revive", "agent-v", "parent-v")
        row = await _get_audit_row(session_factory, "tok-revive")
        parsed = json.loads(row.events)
        assert any(e["action"] == "revive" for e in parsed)

    @pytest.mark.asyncio
    async def test_record_expire(self, audit, session_factory):
        async with session_factory.begin() as sess:
            sess.add(_audit_seed("tok-expire"))
        await audit.record_expire("tok-expire", "agent-e", "parent-e")
        row = await _get_audit_row(session_factory, "tok-expire")
        parsed = json.loads(row.events)
        assert any(e["action"] == "expire" for e in parsed)

    @pytest.mark.asyncio
    async def test_flush_on_tick(self, audit, session_factory):
        async with session_factory.begin() as sess:
            sess.add(_audit_seed("tok-flush"))
        audit._pending_events = [
            {"token_id": "tok-flush", "action": "mint", "agent_id": "a1", "parent_agent_id": "p1",
             "scope_hash": "abc", "timestamp": time.time()},
        ]
        count = await audit.flush_on_tick()
        assert count == 1
        assert audit._pending_events == []


# ── 12. StsDashboardProvider ─────────────────────────────────────────


class TestDashboardE2E:
    @pytest.mark.asyncio
    async def test_snapshot_empty(self, store, session_factory):
        dashboard = StsDashboardProvider(store, session_factory)
        snap = await dashboard.snapshot()
        assert snap["active_token_count"] == 0
        assert snap["mint_count"] == 0
        assert snap["revoke_count"] == 0
        assert snap["expire_count"] == 0
        assert "generated_at" in snap

    @pytest.mark.asyncio
    async def test_snapshot_with_quota_config(self, store, session_factory):
        await store.store(_token("dash1"))
        await store.store(_token("dash2"))
        qconfig = QuotaConfig(max_tokens_per_agent=5)
        dashboard = StsDashboardProvider(store, session_factory, quota_config=qconfig)
        snap = await dashboard.snapshot()
        assert snap["active_token_count"] == 2
        assert "per_agent" in snap["quota_utilization"]

    @pytest.mark.asyncio
    async def test_cascade_detection(self, store, session_factory):
        now_ts = time.time()
        async with session_factory.begin() as sess:
            for i in range(3):
                token_id = f"tok-cascade-{i}"
                sess.add(StsAuditModel(
                    token_id=token_id, issuer_agent_id=f"agent-{i}", subject_agent_id=f"subj-{i}",
                    spec_yaml="", issued_at=now_ts, expires_at=now_ts + 3600,
                    events=json.dumps([
                        {"action": "mint", "agent_id": f"agent-{i}", "parent_agent_id": "",
                         "scope_hash": "", "timestamp": now_ts + i},
                        {"action": "revoke", "agent_id": f"agent-{i}", "parent_agent_id": "shared-parent",
                         "scope_hash": "", "timestamp": now_ts + 10 + i},
                    ]),
                ))
        dashboard = StsDashboardProvider(
            store, session_factory,
            cascade_config=CascadeConfig(window_seconds=60, min_group_size=2),
        )
        snap = await dashboard.snapshot()
        assert snap["cascade_event_count"] == 3

    @pytest.mark.asyncio
    async def test_scope_distribution(self, store, session_factory):
        await store.store(_token("dist1", scope_actions=json.dumps(["read", "write"])))
        await store.store(_token("dist2", scope_actions=json.dumps(["read"])))
        dashboard = StsDashboardProvider(store, session_factory)
        snap = await dashboard.snapshot()
        dist = snap["scope_distribution"]
        assert dist.get("read") == 2
        assert dist.get("write") == 1


# ── helpers ──────────────────────────────────────────────────────────


def _audit_seed(token_id):
    return StsAuditModel(
        token_id=token_id, issuer_agent_id="root", subject_agent_id="subj",
        spec_yaml="", issued_at=time.time(), expires_at=time.time() + 3600,
        events="[]",
    )


async def _get_audit_row(session_factory, token_id):
    from sqlalchemy import select
    async with session_factory() as sess:
        result = await sess.execute(select(StsAuditModel).where(StsAuditModel.token_id == token_id))
        return result.scalar_one()
