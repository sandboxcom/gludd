"""Unit tests for StsDashboardProvider — NF.7 audit dashboard data provider."""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from general_ludd.sts.dashboard import CascadeConfig, StsDashboardProvider


def _make_token(
    token_id: str,
    agent_id: str,
    parent_agent_id: str = "root",
    revoked_at: datetime | None = None,
    expires_at: datetime | None = None,
    scope_actions: list[str] | None = None,
) -> MagicMock:
    row = MagicMock()
    row.token_id = token_id
    row.agent_id = agent_id
    row.parent_agent_id = parent_agent_id
    row.revoked_at = revoked_at
    row.expires_at = expires_at
    row.scope_actions = json.dumps(scope_actions or [])
    return row


def _make_audit_row(
    token_id: str,
    issuer: str,
    subject: str,
    events: list[dict],
) -> MagicMock:
    row = MagicMock()
    row.token_id = token_id
    row.issuer_agent_id = issuer
    row.subject_agent_id = subject
    row.events = json.dumps(events)
    row.use_count = len(events)
    return row


def _session_factory_with_audit(audit_rows: list) -> MagicMock:
    session = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = audit_rows
    session.execute = AsyncMock(return_value=result)
    sf = MagicMock()
    sf.return_value.__aenter__ = AsyncMock(return_value=session)
    sf.return_value.__aexit__ = AsyncMock()
    return sf


def _mock_store(tokens: list) -> MagicMock:
    store = MagicMock()
    store.list_all = AsyncMock(return_value=tokens)
    return store


class TestCascadeConfig:
    def test_defaults(self) -> None:
        cfg = CascadeConfig()
        assert cfg.window_seconds == 60
        assert cfg.min_group_size == 2

    def test_custom(self) -> None:
        cfg = CascadeConfig(window_seconds=30, min_group_size=3)
        assert cfg.window_seconds == 30
        assert cfg.min_group_size == 3


class TestInit:
    def test_stores_dependencies(self) -> None:
        store = _mock_store([])
        sf = _session_factory_with_audit([])
        provider = StsDashboardProvider(store=store, session_factory=sf)
        assert provider._store is store
        assert provider._session_factory is sf

    def test_default_cascade_config(self) -> None:
        provider = StsDashboardProvider(
            store=_mock_store([]), session_factory=_session_factory_with_audit([])
        )
        assert isinstance(provider._cascade_config, CascadeConfig)
        assert provider._cascade_config.window_seconds == 60


@pytest.mark.asyncio
class TestActiveTokenCount:
    async def test_zero_when_no_tokens(self) -> None:
        provider = StsDashboardProvider(
            store=_mock_store([]), session_factory=_session_factory_with_audit([])
        )
        snap = await provider.snapshot()
        assert snap["active_token_count"] == 0

    async def test_counts_only_active(self) -> None:
        now = datetime.now(UTC)
        tokens = [
            _make_token("t1", "a1"),  # active
            _make_token("t2", "a2", revoked_at=now),  # revoked
            _make_token("t3", "a3", expires_at=now - timedelta(hours=1)),  # expired
            _make_token("t4", "a4", expires_at=now + timedelta(hours=1)),  # active
        ]
        provider = StsDashboardProvider(
            store=_mock_store(tokens), session_factory=_session_factory_with_audit([])
        )
        snap = await provider.snapshot()
        assert snap["active_token_count"] == 2


@pytest.mark.asyncio
class TestActionCounts:
    async def test_mint_revoke_expire_counts(self) -> None:
        audit = [
            _make_audit_row(
                "t1",
                "issuer",
                "a1",
                [
                    {"action": "mint", "timestamp": time.time()},
                    {"action": "use", "timestamp": time.time()},
                ],
            ),
            _make_audit_row(
                "t2",
                "issuer",
                "a2",
                [
                    {"action": "mint", "timestamp": time.time()},
                    {"action": "revoke", "timestamp": time.time()},
                ],
            ),
            _make_audit_row(
                "t3",
                "issuer",
                "a3",
                [
                    {"action": "mint", "timestamp": time.time()},
                    {"action": "expire", "timestamp": time.time()},
                ],
            ),
        ]
        provider = StsDashboardProvider(
            store=_mock_store([]), session_factory=_session_factory_with_audit(audit)
        )
        snap = await provider.snapshot()
        assert snap["mint_count"] == 3
        assert snap["revoke_count"] == 1
        assert snap["expire_count"] == 1
        # 'use' is not surfaced as a top-level rate metric
        assert "use_count" not in snap

    async def test_empty_audit_returns_zero_counts(self) -> None:
        provider = StsDashboardProvider(
            store=_mock_store([]), session_factory=_session_factory_with_audit([])
        )
        snap = await provider.snapshot()
        assert snap["mint_count"] == 0
        assert snap["revoke_count"] == 0
        assert snap["expire_count"] == 0


@pytest.mark.asyncio
class TestScopeDistribution:
    async def test_active_token_scope_aggregation(self) -> None:
        tokens = [
            _make_token("t1", "a1", scope_actions=["read", "write"]),
            _make_token("t2", "a2", scope_actions=["read"]),
            _make_token("t3", "a3", scope_actions=["read", "execute"]),
        ]
        provider = StsDashboardProvider(
            store=_mock_store(tokens), session_factory=_session_factory_with_audit([])
        )
        snap = await provider.snapshot()
        dist = snap["scope_distribution"]
        assert dist["read"] == 3
        assert dist["write"] == 1
        assert dist["execute"] == 1

    async def test_revoked_tokens_excluded_from_scope(self) -> None:
        now = datetime.now(UTC)
        tokens = [
            _make_token("t1", "a1", scope_actions=["read"]),
            _make_token("t2", "a2", revoked_at=now, scope_actions=["write"]),
        ]
        provider = StsDashboardProvider(
            store=_mock_store(tokens), session_factory=_session_factory_with_audit([])
        )
        snap = await provider.snapshot()
        assert snap["scope_distribution"] == {"read": 1}


@pytest.mark.asyncio
class TestCascadeEvents:
    async def test_isolated_revokes_not_cascade(self) -> None:
        now = time.time()
        audit = [
            _make_audit_row(
                "t1", "root", "a1", [{"action": "revoke", "parent_agent_id": "root", "timestamp": now}],
            ),
            _make_audit_row(
                "t2", "root", "a2",
                [{"action": "revoke", "parent_agent_id": "other", "timestamp": now}],
            ),
        ]
        provider = StsDashboardProvider(
            store=_mock_store([]), session_factory=_session_factory_with_audit(audit)
        )
        snap = await provider.snapshot()
        assert snap["cascade_event_count"] == 0

    async def test_two_revokes_same_parent_within_window(self) -> None:
        now = time.time()
        audit = [
            _make_audit_row(
                "t1", "root", "a1",
                [{"action": "revoke", "parent_agent_id": "root", "timestamp": now}],
            ),
            _make_audit_row(
                "t2", "root", "a2",
                [{"action": "revoke", "parent_agent_id": "root", "timestamp": now + 5}],
            ),
        ]
        provider = StsDashboardProvider(
            store=_mock_store([]), session_factory=_session_factory_with_audit(audit)
        )
        snap = await provider.snapshot()
        assert snap["cascade_event_count"] == 2

    async def test_revokes_outside_window_not_cascade(self) -> None:
        now = time.time()
        audit = [
            _make_audit_row(
                "t1", "root", "a1",
                [{"action": "revoke", "parent_agent_id": "root", "timestamp": now}],
            ),
            _make_audit_row(
                "t2", "root", "a2",
                [{"action": "revoke", "parent_agent_id": "root", "timestamp": now + 120}],
            ),
        ]
        cfg = CascadeConfig(window_seconds=60)
        provider = StsDashboardProvider(
            store=_mock_store([]),
            session_factory=_session_factory_with_audit(audit),
            cascade_config=cfg,
        )
        snap = await provider.snapshot()
        assert snap["cascade_event_count"] == 0


@pytest.mark.asyncio
class TestQuotaUtilization:
    async def test_per_agent_utilization(self) -> None:
        tokens = [
            _make_token("t1", "a1"),
            _make_token("t2", "a1"),
            _make_token("t3", "a2"),
        ]
        quota_cfg = MagicMock()
        quota_cfg.max_tokens_per_agent = 5
        provider = StsDashboardProvider(
            store=_mock_store(tokens),
            session_factory=_session_factory_with_audit([]),
            quota_config=quota_cfg,
        )
        snap = await provider.snapshot()
        per_agent = snap["quota_utilization"]["per_agent"]
        assert per_agent["a1"]["active"] == 2
        assert per_agent["a1"]["max"] == 5
        assert per_agent["a2"]["active"] == 1
        assert per_agent["a2"]["max"] == 5

    async def test_per_agent_utilization_without_quota_config(self) -> None:
        tokens = [_make_token("t1", "a1")]
        provider = StsDashboardProvider(
            store=_mock_store(tokens),
            session_factory=_session_factory_with_audit([]),
        )
        snap = await provider.snapshot()
        assert snap["quota_utilization"]["per_agent"]["a1"]["active"] == 1
        assert "max" not in snap["quota_utilization"]["per_agent"]["a1"]


@pytest.mark.asyncio
class TestSnapshotShape:
    async def test_snapshot_has_all_required_keys(self) -> None:
        provider = StsDashboardProvider(
            store=_mock_store([]), session_factory=_session_factory_with_audit([])
        )
        snap = await provider.snapshot()
        required = {
            "active_token_count",
            "mint_count",
            "revoke_count",
            "expire_count",
            "scope_distribution",
            "cascade_event_count",
            "quota_utilization",
            "generated_at",
        }
        assert required.issubset(snap.keys())

    async def test_generated_at_is_float(self) -> None:
        provider = StsDashboardProvider(
            store=_mock_store([]), session_factory=_session_factory_with_audit([])
        )
        snap = await provider.snapshot()
        assert isinstance(snap["generated_at"], float)
