"""Deep STS token lifecycle tests.

Edge cases and integrity checks across minter, store, narrowing, revoker,
reaper, and reviver — covering expiration boundaries, concurrency, chained
narrowing, cascade revocation, hibernation revive, and store corruption
recovery.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.db.models import AgentTokenModel
from general_ludd.permissions.tool_permissions import CapabilityLattice, ToolAction
from general_ludd.secrets.manager import AppRoleCreds
from general_ludd.sts.minter import TokenMinter
from general_ludd.sts.narrowing import (
    CapabilityNarrowing,
    OpenBaoPolicyRenderer,
)
from general_ludd.sts.reaper import TokenReaper
from general_ludd.sts.reviver import TokenRevivalError, TokenReviver
from general_ludd.sts.revoker import TokenRevoker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _token(**overrides) -> AgentTokenModel:
    defaults: dict = {
        "token_id": "tok-agent-1",
        "agent_id": "agent-1",
        "parent_agent_id": "parent-0",
        "role_name": "agent-agent-1",
        "role_id": "rid-1",
        "scope_hash": "abc123",
        "scope_actions": "[]",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "expires_at": datetime(2026, 1, 2, tzinfo=UTC),
        "revoked_at": None,
        "hydration_count": 0,
    }
    defaults.update(overrides)
    return AgentTokenModel(**defaults)


def _mock_sf():
    return MagicMock()


def _token_store_with(records: list[AgentTokenModel]):
    """Return a TokenStore-like mock whose ``get()`` returns *records* by agent_id."""
    store = MagicMock()

    async def _get(agent_id: str):
        for r in records:
            if r.agent_id == agent_id:
                return r
        return None

    async def _increment_hydration(agent_id: str) -> None:
        for r in records:
            if r.agent_id == agent_id:
                r.hydration_count += 1
                return

    store.get = _get
    store.increment_hydration = _increment_hydration
    return store


# ---------------------------------------------------------------------------
# 1. Token expiration edge cases
# ---------------------------------------------------------------------------


class TestExpirationBoundary:
    @pytest.mark.asyncio
    async def test_expires_exactly_at_boundary_is_expired(self):
        now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
        tok = _token(
            agent_id="a1",
            token_id="t1",
            expires_at=datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC),
            revoked_at=None,
        )
        store = MagicMock()
        store.list_expired = AsyncMock(return_value=[tok])
        revoker = MagicMock()
        revoker.revoke = AsyncMock()

        reaper = TokenReaper(store, revoker)
        count = await reaper.reap_expired(now=now)

        assert count == 1
        revoker.revoke.assert_awaited_once_with("a1")

    @pytest.mark.asyncio
    async def test_expires_one_microsecond_before_is_expired(self):
        now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
        tok = _token(
            agent_id="a2",
            token_id="t2",
            expires_at=now - timedelta(microseconds=1),
            revoked_at=None,
        )
        store = MagicMock()
        store.list_expired = AsyncMock(return_value=[tok])
        revoker = MagicMock()
        revoker.revoke = AsyncMock()
        reaper = TokenReaper(store, revoker)

        count = await reaper.reap_expired(now=now)
        assert count == 1

    @pytest.mark.asyncio
    async def test_expires_one_microsecond_after_is_not_expired(self):
        now = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
        _token(
            agent_id="a3",
            token_id="t3",
            expires_at=now + timedelta(microseconds=1),
            revoked_at=None,
        )
        store = MagicMock()
        store.list_expired = AsyncMock(return_value=[])
        revoker = MagicMock()
        reaper = TokenReaper(store, revoker)

        count = await reaper.reap_expired(now=now)
        assert count == 0

    def test_distant_future_expires_at_preserved(self):
        tok = _token(expires_at=datetime(2099, 12, 31, 23, 59, 59, tzinfo=UTC))
        assert tok.expires_at is not None
        assert tok.expires_at.year == 2099

    def test_no_expiry_token_has_none_expires_at(self):
        tok = _token(expires_at=None)
        assert tok.expires_at is None

    def test_expires_at_timezone_agnostic_comparison(self):
        plus5 = timezone(timedelta(hours=5))
        now_utc = datetime(2026, 6, 15, 12, 0, 0, tzinfo=UTC)
        expires_plus5 = datetime(2026, 6, 15, 7, 0, 0, tzinfo=plus5)
        assert expires_plus5 < now_utc


# ---------------------------------------------------------------------------
# 2. Concurrent mint operations
# ---------------------------------------------------------------------------


class _CountingSM:
    """SecretsManager fake that tracks call order and concurrency."""

    def __init__(self) -> None:
        self.call_order: list[str] = []
        self.concurrent_count = 0
        self.max_concurrent = 0

    def setup_approle(self, role_name: str) -> AppRoleCreds:
        self.concurrent_count += 1
        self.max_concurrent = max(self.max_concurrent, self.concurrent_count)
        self.call_order.append(role_name)
        self.concurrent_count -= 1
        return AppRoleCreds(role_id=role_name, secret_id=f"secret-{role_name}")


class TestConcurrentMint:
    @pytest.mark.asyncio
    async def test_concurrent_mints_produce_unique_tokens(self):
        sm = _CountingSM()
        minter = TokenMinter(sm)

        async def mint_one(idx: int) -> AppRoleCreds:
            return await minter.mint(
                agent_id=f"agent-{idx}",
                parent_agent_id="parent-0",
            )

        creds = await asyncio.gather(*(mint_one(i) for i in range(10)))

        role_ids = {c.role_id for c in creds}
        assert len(role_ids) == 10
        secret_ids = {c.secret_id for c in creds}
        assert len(secret_ids) == 10

    @pytest.mark.asyncio
    async def test_concurrent_mints_maintain_call_ordering(self):
        sm = _CountingSM()
        minter = TokenMinter(sm)

        await asyncio.gather(
            *(
                minter.mint(
                    agent_id=f"agent-{i}",
                    parent_agent_id="parent-0",
                )
                for i in range(5)
            )
        )

        assert len(sm.call_order) == 5
        for i in range(5):
            assert f"agent-agent-{i}" in sm.call_order

    @pytest.mark.asyncio
    async def test_concurrent_mints_with_narrowing_do_not_cross_contaminate(self):
        count_sm = _CountingSM()
        lattice = CapabilityLattice()

        minter = TokenMinter(count_sm)

        admin_creds, reader_creds = await asyncio.gather(
            minter.mint(
                agent_id="agent-admin",
                parent_agent_id="p1",
                parent_lattice=lattice,
                child_actions={ToolAction.READ, ToolAction.WRITE, ToolAction.EXECUTE, ToolAction.DELETE},
                parent_role="admin",
            ),
            minter.mint(
                agent_id="agent-reader",
                parent_agent_id="p1",
                parent_lattice=lattice,
                child_actions={ToolAction.READ, ToolAction.WRITE},
                parent_role="reader",
            ),
        )

        assert admin_creds.role_id == "agent-agent-admin"
        assert reader_creds.role_id == "agent-agent-reader"
        assert len(count_sm.call_order) == 2


# ---------------------------------------------------------------------------
# 3. Token narrowing chain integrity
# ---------------------------------------------------------------------------


class TestNarrowingChainIntegrity:
    def test_three_level_chain_inherits_correctly(self):
        chain = {
            "owner": frozenset({"admin", "coder"}),
            "admin": frozenset({"writer"}),
            "coder": frozenset(),
            "writer": frozenset(),
        }
        lattice = CapabilityLattice(chain=chain)
        narrowing = CapabilityNarrowing(lattice)

        parent_all = lattice.all_actions("owner")
        assert isinstance(parent_all, frozenset)
        child_actions = {"read", "execute", "delete"}
        result = narrowing.narrow(child_actions, parent_role="owner")
        result_set = set(result)
        assert result_set.issubset(parent_all)
        assert result_set == child_actions & parent_all

    def test_narrowing_at_every_level_is_idempotent(self):
        chain = {
            "super": frozenset({"admin"}),
            "admin": frozenset({"coder"}),
            "coder": frozenset({"writer"}),
            "writer": frozenset(),
        }
        lattice = CapabilityLattice(chain=chain)

        for role in ("super", "admin", "coder", "writer"):
            narrowing = CapabilityNarrowing(lattice)
            all_acts = lattice.all_actions(role)
            result = narrowing.narrow(all_acts, parent_role=role)
            assert result == all_acts, f"idempotent fail for role={role}"

    def test_narrowing_result_never_exceeds_parent_actions(self):
        chain = {"top": frozenset({"mid"}), "mid": frozenset({"low"}), "low": frozenset()}
        lattice = CapabilityLattice(chain=chain)

        for parent_role in ("top", "mid", "low"):
            narrowing = CapabilityNarrowing(lattice)
            parent_all = lattice.all_actions(parent_role)
            for child_role in ("top", "mid", "low"):
                child_all = lattice.all_actions(child_role)
                narrowed = narrowing.narrow(child_all, parent_role=parent_role)
                assert narrowed.issubset(parent_all), (
                    f"parent={parent_role} child={child_role}: {narrowed} not subset of {parent_all}"
                )

    def test_deeply_nested_custom_chain_no_privilege_escalation(self):
        chain = {
            "root": frozenset({"l1a", "l1b"}),
            "l1a": frozenset({"l2a"}),
            "l1b": frozenset({"l2b"}),
            "l2a": frozenset({"leaf"}),
            "l2b": frozenset(),
            "leaf": frozenset(),
        }
        lattice = CapabilityLattice(chain=chain)
        narrowing = CapabilityNarrowing(lattice)
        parent_all = lattice.all_actions("root")
        child_all = lattice.all_actions("leaf")

        narrowed = narrowing.narrow(child_all, parent_role="root")
        assert narrowed == child_all
        assert narrowed.issubset(parent_all)

    def test_disjoint_trees_produce_empty_intersection(self):
        chain = {"north": frozenset(), "south": frozenset()}
        lattice = CapabilityLattice(chain=chain)
        narrowing = CapabilityNarrowing(lattice)

        result = narrowing.narrow(lattice.all_actions("north"), parent_role="south")
        assert result == set()

    def test_narrowing_with_circular_chain_still_terminates(self):
        chain = {"a": frozenset({"b"}), "b": frozenset({"a"})}
        lattice = CapabilityLattice(chain=chain)
        narrowing = CapabilityNarrowing(lattice)
        result = narrowing.narrow({ToolAction.READ}, parent_role="a")
        assert isinstance(result, set)


# ---------------------------------------------------------------------------
# 4. Reaper cleanup of expired tokens
# ---------------------------------------------------------------------------


class TestReaperCleanupDeep:
    @pytest.mark.asyncio
    async def test_reaper_handles_mixed_live_and_expired(self):
        now = datetime(2026, 6, 15, tzinfo=UTC)
        _token(agent_id="live", token_id="tl", expires_at=now + timedelta(days=1), revoked_at=None)
        t2 = _token(agent_id="expired", token_id="te", expires_at=now - timedelta(days=1), revoked_at=None)
        _token(agent_id="revoked", token_id="tr", expires_at=now - timedelta(days=2), revoked_at=now)

        store = MagicMock()
        store.list_expired = AsyncMock(return_value=[t2])
        store.list_children = AsyncMock(return_value=[])
        revoker = MagicMock()
        revoker.revoke = AsyncMock()

        reaper = TokenReaper(store, revoker)
        count = await reaper.reap_expired(now=now)

        assert count == 1
        revoker.revoke.assert_awaited_once_with("expired")

    @pytest.mark.asyncio
    async def test_reaper_zero_expired_no_revoke_calls(self):
        store = MagicMock()
        store.list_expired = AsyncMock(return_value=[])
        revoker = MagicMock()
        revoker.revoke = AsyncMock()

        reaper = TokenReaper(store, revoker)
        count = await reaper.reap_expired()

        assert count == 0
        revoker.revoke.assert_not_called()

    @pytest.mark.asyncio
    async def test_reaper_audit_emit_failure_does_not_block_reap_count(self):
        now = datetime.now(UTC)
        tok = _token(agent_id="a1", token_id="t1", expires_at=now - timedelta(days=1))
        store = MagicMock()
        store.list_expired = AsyncMock(return_value=[tok])
        store.list_children = AsyncMock(return_value=[])
        revoker = MagicMock()
        revoker.revoke = AsyncMock()
        audit = MagicMock()
        audit.record_expire = AsyncMock(side_effect=RuntimeError("audit failure"))

        reaper = TokenReaper(store, revoker, audit_pipeline=audit)
        count = await reaper.reap_expired(now=now)

        assert count == 1
        revoker.revoke.assert_awaited_once_with("a1")

    @pytest.mark.asyncio
    async def test_reaper_revokes_single_token_and_counts_correctly(self):
        now = datetime.now(UTC)
        tok = _token(agent_id="lonely", token_id="tlonely", expires_at=now - timedelta(minutes=5))
        store = MagicMock()
        store.list_expired = AsyncMock(return_value=[tok])
        store.list_children = AsyncMock(return_value=[])
        revoker = MagicMock()
        revoker.revoke = AsyncMock()

        reaper = TokenReaper(store, revoker)
        count = await reaper.reap_expired(now=now)

        assert count == 1


# ---------------------------------------------------------------------------
# 5. Cascade revocation — parent revoke to children
# ---------------------------------------------------------------------------


@dataclass
class _TreeRecord:
    agent_id: str
    token_id: str
    parent_agent_id: str | None = None
    revoked_at: datetime | None = None


class _TreeStore:
    def __init__(self, children: dict[str, list[_TreeRecord]] | None = None) -> None:
        self._children = children or {}

    async def list_children(self, parent_id: str) -> list[_TreeRecord]:
        return self._children.get(parent_id, [])


class _TreeRevoker:
    def __init__(self, fail_for: set[str] | None = None) -> None:
        self.revoked: list[str] = []
        self._fail_for = fail_for or set()

    async def revoke(self, agent_id: str) -> None:
        if agent_id in self._fail_for:
            raise RuntimeError(f"revoke failed for {agent_id}")
        self.revoked.append(agent_id)


class TestCascadeDeep:
    @pytest.mark.asyncio
    async def test_cascade_three_generations_deep(self):
        store = _TreeStore(
            children={
                "root": [_TreeRecord("c1", "tc1")],
                "c1": [_TreeRecord("g1", "tg1")],
                "g1": [_TreeRecord("gg1", "tgg1")],
            }
        )
        revoker = _TreeRevoker()
        reaper = TokenReaper(store, revoker)

        total = await reaper.cascade_revoke("root")
        assert total == 3
        assert set(revoker.revoked) == {"c1", "g1", "gg1"}

    @pytest.mark.asyncio
    async def test_cascade_wide_fan_out(self):
        children = {"root": [_TreeRecord(f"c{i}", f"tc{i}") for i in range(1, 11)]}
        for i in range(1, 11):
            children[f"c{i}"] = [_TreeRecord(f"gc{i}", f"tgc{i}")]
        store = _TreeStore(children=children)
        revoker = _TreeRevoker()
        reaper = TokenReaper(store, revoker)

        total = await reaper.cascade_revoke("root")
        assert total == 20

    @pytest.mark.asyncio
    async def test_cascade_respects_already_revoked_in_middle_of_tree(self):
        store = _TreeStore(
            children={
                "root": [
                    _TreeRecord("c1", "tc1", revoked_at=None),
                ],
                "c1": [
                    _TreeRecord("g1", "tg1", revoked_at=datetime.now(UTC)),
                    _TreeRecord("g2", "tg2", revoked_at=None),
                ],
                "g1": [_TreeRecord("gg1", "tgg1", revoked_at=None)],
            }
        )
        revoker = _TreeRevoker()
        reaper = TokenReaper(store, revoker)

        total = await reaper.cascade_revoke("root")
        assert total == 2
        assert set(revoker.revoked) == {"c1", "g2"}

    @pytest.mark.asyncio
    async def test_cascade_with_empty_child_list(self):
        store = _TreeStore(
            children={
                "root": [
                    _TreeRecord("c1", "tc1"),
                ],
                "c1": [],
            }
        )
        revoker = _TreeRevoker()
        reaper = TokenReaper(store, revoker)

        total = await reaper.cascade_revoke("root")
        assert total == 1
        assert revoker.revoked == ["c1"]

    @pytest.mark.asyncio
    async def test_cascade_isolated_leaf_revokes_nothing(self):
        store = _TreeStore(children={})
        revoker = _TreeRevoker()
        reaper = TokenReaper(store, revoker)

        total = await reaper.cascade_revoke("leaf-only")
        assert total == 0


# ---------------------------------------------------------------------------
# 6. Hibernation and revive edge cases
# ---------------------------------------------------------------------------


class TestReviveDeep:
    @pytest.mark.asyncio
    async def test_revive_increments_hydration_count(self):
        sm = MagicMock()
        sm.rotate_approle_secret_id.return_value = "fresh-secret"

        tok = _token(agent_id="a1", hydration_count=2, revoked_at=None)
        store = _token_store_with([tok])
        reviver = TokenReviver(sm, store)

        creds = await reviver.revive("a1")

        assert creds.role_id == tok.role_id
        assert creds.secret_id == "fresh-secret"
        assert tok.hydration_count == 3

    @pytest.mark.asyncio
    async def test_revive_revoked_token_raises(self):
        sm = MagicMock()
        tok = _token(
            agent_id="a1",
            revoked_at=datetime(2026, 6, 15, tzinfo=UTC),
        )
        store = _token_store_with([tok])
        reviver = TokenReviver(sm, store)

        with pytest.raises(TokenRevivalError, match="revoked token"):
            await reviver.revive("a1")

    @pytest.mark.asyncio
    async def test_revive_nonexistent_agent_raises(self):
        sm = MagicMock()
        store = _token_store_with([])
        reviver = TokenReviver(sm, store)

        with pytest.raises(TokenRevivalError, match="No token record"):
            await reviver.revive("missing")

    @pytest.mark.asyncio
    async def test_revive_rotation_failure_raises(self):
        sm = MagicMock()
        sm.rotate_approle_secret_id.side_effect = RuntimeError("openbao down")
        tok = _token(agent_id="a1", revoked_at=None)
        store = _token_store_with([tok])
        reviver = TokenReviver(sm, store)

        with pytest.raises(TokenRevivalError, match="RuntimeError"):
            await reviver.revive("a1")

    @pytest.mark.asyncio
    async def test_revive_preserves_same_role_id(self):
        sm = MagicMock()
        sm.rotate_approle_secret_id.return_value = "rotated-secret"
        tok = _token(agent_id="a1", role_id="original-rid", revoked_at=None)
        store = _token_store_with([tok])
        reviver = TokenReviver(sm, store)

        creds = await reviver.revive("a1")
        assert creds.role_id == "original-rid"
        assert creds.secret_id == "rotated-secret"

    @pytest.mark.asyncio
    async def test_revive_then_revoke_then_revive_fails(self):
        sm = MagicMock()
        tok = _token(
            agent_id="a1",
            revoked_at=datetime(2026, 6, 15, tzinfo=UTC),
        )
        store = _token_store_with([tok])
        reviver = TokenReviver(sm, store)

        with pytest.raises(TokenRevivalError, match="revoked token"):
            await reviver.revive("a1")

    @pytest.mark.asyncio
    async def test_multiple_revives_increment_hydration_repeatedly(self):
        sm = MagicMock()
        sm.rotate_approle_secret_id.side_effect = [
            "secret-1",
            "secret-2",
            "secret-3",
        ]
        tok = _token(agent_id="a1", hydration_count=0, revoked_at=None)
        store = _token_store_with([tok])
        reviver = TokenReviver(sm, store)

        creds1 = await reviver.revive("a1")
        assert tok.hydration_count == 1
        assert creds1.secret_id == "secret-1"

        creds2 = await reviver.revive("a1")
        assert tok.hydration_count == 2
        assert creds2.secret_id == "secret-2"

        creds3 = await reviver.revive("a1")
        assert tok.hydration_count == 3
        assert creds3.secret_id == "secret-3"

    @pytest.mark.asyncio
    async def test_revive_records_audit(self):
        sm = MagicMock()
        sm.rotate_approle_secret_id.return_value = "fresh"
        audit = MagicMock()
        audit.record_revive = AsyncMock()

        tok = _token(agent_id="a1", token_id="tok-a1", parent_agent_id="parent-0", revoked_at=None)
        store = _token_store_with([tok])
        reviver = TokenReviver(sm, store, audit_pipeline=audit)

        await reviver.revive("a1")

        audit.record_revive.assert_awaited_once()
        kwargs = audit.record_revive.call_args.kwargs
        assert kwargs["token_id"] == "tok-a1"
        assert kwargs["agent_id"] == "a1"
        assert kwargs["parent_agent_id"] == "parent-0"


# ---------------------------------------------------------------------------
# 7. Token store corruption recovery
# ---------------------------------------------------------------------------


class TestStoreCorruptionRecovery:
    def test_token_with_empty_string_scope_hash_is_valid(self):
        tok = _token(scope_hash="")
        assert tok.scope_hash == ""

    def test_token_with_null_expires_at_is_valid(self):
        tok = _token(expires_at=None)
        assert tok.expires_at is None
        assert tok.revoked_at is None

    def test_token_with_zero_hydration_count_is_valid(self):
        tok = _token(hydration_count=0)
        assert tok.hydration_count == 0

    def test_token_with_empty_scope_actions_is_valid(self):
        tok = _token(scope_actions="[]")
        assert tok.scope_actions == "[]"

    def test_token_role_name_not_matching_agent_id_still_works(self):
        tok = _token(role_name="agent-agent-other", agent_id="agent-1")
        assert tok.role_name != f"agent-{tok.agent_id}"

    @pytest.mark.asyncio
    async def test_revoker_noops_on_missing_record(self):
        sm = MagicMock()
        sm._client = MagicMock()
        store = _token_store_with([])
        revoker = TokenRevoker(sm, store)

        await revoker.revoke("nonexistent")
        sm._client.auth.approle.delete_role.assert_not_called()

    @pytest.mark.asyncio
    async def test_revoker_noops_on_already_revoked_record(self):
        sm = MagicMock()
        sm._client = MagicMock()
        tok = _token(
            agent_id="a1",
            token_id="tok-a1",
            revoked_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
        store = _token_store_with([tok])
        revoker = TokenRevoker(sm, store)

        await revoker.revoke("a1")

        sm._client.auth.approle.delete_role.assert_not_called()

    def test_revoker_rejects_invalid_terminal_state(self):
        sm = MagicMock()
        sm._client = MagicMock()
        tok = _token(agent_id="a1", token_id="tok-a1", revoked_at=None)
        store = _token_store_with([tok])
        revoker = TokenRevoker(sm, store)

        with pytest.raises(ValueError, match="unsupported"):
            asyncio.run(revoker.revoke("a1", terminal_state="bogus"))


# ---------------------------------------------------------------------------
# 8. End-to-end lifecycle simulation: mint → narrow → store → expire → reap
# ---------------------------------------------------------------------------


class _LifecycleStore:
    def __init__(self) -> None:
        self.tokens: dict[str, AgentTokenModel] = {}

    async def store(self, record: AgentTokenModel) -> None:
        self.tokens[record.agent_id] = record

    async def get(self, agent_id: str) -> AgentTokenModel | None:
        return self.tokens.get(agent_id)

    async def revoke(self, token_id: str) -> None:
        for tok in self.tokens.values():
            if tok.token_id == token_id:
                tok.revoked_at = datetime.now(UTC)
                return

    async def list_expired(self, now: datetime) -> list[AgentTokenModel]:
        return [
            t for t in self.tokens.values() if t.expires_at is not None and t.expires_at < now and t.revoked_at is None
        ]

    async def list_children(self, parent_id: str) -> list[AgentTokenModel]:
        return [t for t in self.tokens.values() if t.parent_agent_id == parent_id]

    async def list_all(self) -> list[AgentTokenModel]:
        return list(self.tokens.values())

    async def increment_hydration(self, agent_id: str) -> None:
        tok = self.tokens.get(agent_id)
        if tok is not None:
            tok.hydration_count += 1


class TestFullLifecycle:
    @pytest.mark.asyncio
    async def test_mint_store_expire_reap_cycle(self):
        lc_store = _LifecycleStore()
        sm = MagicMock()
        sm.setup_approle.side_effect = lambda name: AppRoleCreds(role_id=name, secret_id=f"sec-{name}")
        sm._client = MagicMock()

        minter = TokenMinter(sm)
        now = datetime(2026, 1, 1, tzinfo=UTC)
        expiry = now + timedelta(minutes=5)

        parent_creds = await minter.mint(agent_id="parent-a", parent_agent_id="root-0")
        parent_tok = _token(
            token_id="tok-parent-a",
            agent_id="parent-a",
            parent_agent_id="root-0",
            role_name="agent-parent-a",
            role_id=parent_creds.role_id,
            created_at=now,
            expires_at=expiry,
            revoked_at=None,
        )
        await lc_store.store(parent_tok)

        lattice = CapabilityLattice()
        child_creds = await minter.mint(
            agent_id="child-b",
            parent_agent_id="parent-a",
            parent_lattice=lattice,
            child_actions={ToolAction.READ},
            parent_role="admin",
        )
        child_tok = _token(
            token_id="tok-child-b",
            agent_id="child-b",
            parent_agent_id="parent-a",
            role_name="agent-child-b",
            role_id=child_creds.role_id,
            created_at=now,
            expires_at=expiry,
            revoked_at=None,
        )
        await lc_store.store(child_tok)

        assert len(lc_store.tokens) == 2

        future = expiry + timedelta(minutes=1)
        expired = await lc_store.list_expired(future)
        assert len(expired) == 2

        reaper = TokenReaper(lc_store, TokenRevoker(sm, lc_store))
        count = await reaper.reap_expired(now=future)

        assert count == 2
        parent_from_store = await lc_store.get("parent-a")
        child_from_store = await lc_store.get("child-b")
        assert parent_from_store is not None
        assert parent_from_store.revoked_at is not None
        assert child_from_store is not None
        assert child_from_store.revoked_at is not None

    @pytest.mark.asyncio
    async def test_lifecycle_parent_revoke_cascades_to_children(self):
        lc_store = _LifecycleStore()
        sm = MagicMock()
        sm.setup_approle.return_value = AppRoleCreds(role_id="r1", secret_id="s1")
        sm._client = MagicMock()

        now = datetime(2026, 1, 1, tzinfo=UTC)
        far_future = now + timedelta(days=365)

        grandparent = _token(agent_id="gp", token_id="tok-gp", parent_agent_id="root", expires_at=far_future)
        parent = _token(agent_id="p", token_id="tok-p", parent_agent_id="gp", expires_at=far_future)
        child1 = _token(agent_id="c1", token_id="tok-c1", parent_agent_id="p", expires_at=far_future)
        child2 = _token(agent_id="c2", token_id="tok-c2", parent_agent_id="p", expires_at=far_future)

        for tok in (grandparent, parent, child1, child2):
            await lc_store.store(tok)

        revoker = TokenRevoker(sm, lc_store)
        reaper = TokenReaper(lc_store, revoker)
        revoker.set_cascade_hook(reaper.cascade_revoke)

        await revoker.revoke("gp", terminal_state="completed")

        for aid in ("gp", "p", "c1", "c2"):
            tok = await lc_store.get(aid)
            assert tok is not None
            assert tok.revoked_at is not None, f"{aid} was not revoked"

    @pytest.mark.asyncio
    async def test_lifecycle_mint_revive_revoke_sequence(self):
        lc_store = _LifecycleStore()
        sm = MagicMock()
        sm.setup_approle.return_value = AppRoleCreds(role_id="rid-X", secret_id="initial")
        sm.rotate_approle_secret_id.return_value = "revived-secret"
        sm._client = MagicMock()

        minter = TokenMinter(sm)
        await minter.mint(agent_id="agent-x", parent_agent_id="root")

        tok = _token(
            token_id="tok-x",
            agent_id="agent-x",
            parent_agent_id="root",
            role_name="agent-agent-x",
            role_id="rid-X",
            revoked_at=None,
            hydration_count=0,
        )
        await lc_store.store(tok)

        reviver = TokenReviver(sm, lc_store)
        creds = await reviver.revive("agent-x")
        assert creds.secret_id == "revived-secret"

        fetched = await lc_store.get("agent-x")
        assert fetched is not None
        assert fetched.hydration_count == 1

        revoker = TokenRevoker(sm, lc_store)
        await revoker.revoke("agent-x", terminal_state="completed")

        revoked = await lc_store.get("agent-x")
        assert revoked is not None
        assert revoked.revoked_at is not None

        with pytest.raises(TokenRevivalError, match="revoked token"):
            await reviver.revive("agent-x")


# ---------------------------------------------------------------------------
# 9. Policy rendering edge cases
# ---------------------------------------------------------------------------


class TestPolicyRendererEdgeCases:
    def test_duplicate_actions_merge_verbs(self):
        hcl = OpenBaoPolicyRenderer.render(["read", "read", "read"], role_name="dupes")
        assert "read" in hcl
        assert hcl.count("read") == 1

    def test_overwrite_and_write_merge_capabilities(self):
        hcl = OpenBaoPolicyRenderer.render(["write", "overwrite"], role_name="merged")
        assert "create" in hcl or "update" in hcl

    def test_unknown_action_defaults_to_verb_as_is(self):
        hcl = OpenBaoPolicyRenderer.render(["custom_action"], role_name="unknown")
        assert "custom_action" in hcl

    def test_execute_verb_maps_to_sudo(self):
        hcl = OpenBaoPolicyRenderer.render(["execute"], role_name="sudo-role")
        assert "sudo" in hcl

    def test_disjoint_paths_produce_multiple_path_blocks(self):
        hcl = OpenBaoPolicyRenderer.render(["read", "execute"], role_name="multi")
        assert hcl.count('path "') == 2


# ---------------------------------------------------------------------------
# 10. Narrowing validation edge cases
# ---------------------------------------------------------------------------


class TestNarrowingValidationDeep:
    def test_validate_child_equals_parent(self):
        parent = {ToolAction.READ, ToolAction.WRITE}
        child = {ToolAction.READ, ToolAction.WRITE}
        assert CapabilityNarrowing.validate_narrowing(parent, child) is True

    def test_validate_child_superset_of_parent_is_escalation(self):
        parent = {ToolAction.READ}
        child = {ToolAction.READ, ToolAction.WRITE, ToolAction.DELETE}
        assert CapabilityNarrowing.validate_narrowing(parent, child) is False

    def test_validate_mixed_string_and_toolaction(self):
        parent = {ToolAction.READ}
        child = {"read"}
        assert CapabilityNarrowing.validate_narrowing(parent, child) is True

    def test_validate_empty_child_allowed(self):
        parent = {ToolAction.READ, ToolAction.WRITE}
        child: set[object] = set()
        assert CapabilityNarrowing.validate_narrowing(parent, child) is True
