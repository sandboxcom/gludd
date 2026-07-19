"""Tests for ``src/general_ludd/sts/visualizer.py``.

Covers _status, _format_node, and TokenTreeRenderer construction.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from general_ludd.sts.visualizer import (
    TokenTreeRenderer,
    _format_node,
    _status,
)


class FakeTokenRecord:
    def __init__(
        self,
        agent_id: str = "agent-1",
        token_id: str = "tok-1",
        revoked_at: datetime | None = None,
        expires_at: datetime | None = None,
    ):
        self.agent_id = agent_id
        self.token_id = token_id
        self.revoked_at = revoked_at
        self.expires_at = expires_at


class TestStatus:
    def test_active_token(self) -> None:
        record = FakeTokenRecord(expires_at=datetime.now(UTC) + timedelta(hours=1))
        assert _status(record) == "active"

    def test_revoked_token(self) -> None:
        record = FakeTokenRecord(revoked_at=datetime.now(UTC))
        assert _status(record) == "revoked"

    def test_expired_token(self) -> None:
        record = FakeTokenRecord(expires_at=datetime.now(UTC) - timedelta(hours=1))
        assert _status(record) == "expired"

    def test_revoked_takes_priority_over_expired(self) -> None:
        record = FakeTokenRecord(
            revoked_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) - timedelta(hours=1),
        )
        assert _status(record) == "revoked"

    def test_no_expiry_no_revoked_is_active(self) -> None:
        record = FakeTokenRecord(expires_at=None, revoked_at=None)
        assert _status(record) == "active"

    def test_custom_now_for_expiry_check(self) -> None:
        record = FakeTokenRecord(expires_at=datetime(2026, 1, 1, tzinfo=UTC))
        fake_now = datetime(2026, 6, 1, tzinfo=UTC)
        assert _status(record, now=fake_now) == "expired"


class TestFormatNode:
    def test_format_active(self) -> None:
        record = FakeTokenRecord(
            agent_id="agent-1",
            token_id="tok-1",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        result = _format_node(record)
        assert "agent-1" in result
        assert "tok-1" in result
        assert "(active)" in result

    def test_format_revoked(self) -> None:
        record = FakeTokenRecord(revoked_at=datetime.now(UTC))
        result = _format_node(record)
        assert "(revoked)" in result

    def test_format_expired(self) -> None:
        record = FakeTokenRecord(expires_at=datetime.now(UTC) - timedelta(hours=1))
        result = _format_node(record)
        assert "(expired)" in result

    def test_format_missing_agent_id(self) -> None:
        record = object()
        result = _format_node(record)
        assert "?" in result
        assert "(active)" in result


class TestTokenTreeRenderer:
    def test_construct_with_store(self) -> None:
        from unittest.mock import MagicMock

        store = MagicMock()
        renderer = TokenTreeRenderer(store)
        assert renderer._store is store
