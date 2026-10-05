"""Unit tests for TokenStore — persist, retrieve, revoke, hydrate, list."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from general_ludd.db.models import AgentTokenModel
from general_ludd.sts.store import TokenStore


def _make_session_factory() -> async_sessionmaker:
    return MagicMock(spec=async_sessionmaker)


def _make_token(**overrides) -> AgentTokenModel:
    defaults: dict = {
        "token_id": "tok-agent-1",
        "agent_id": "agent-1",
        "parent_agent_id": "parent-xyz",
        "role_name": "agent-agent-1",
        "role_id": "test-role-id",
        "scope_hash": "abc123",
        "scope_actions": "[]",
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
        "expires_at": datetime(2026, 1, 2, tzinfo=UTC),
        "revoked_at": None,
        "hydration_count": 0,
    }
    defaults.update(overrides)
    return AgentTokenModel(**defaults)


class TestTokenStoreInit:
    def test_stores_session_factory(self):
        sf = _make_session_factory()
        store = TokenStore(sf)
        assert store._session_factory is sf


class TestTokenStoreStore:
    @pytest.mark.asyncio
    async def test_store_calls_session_add(self):
        sf = _make_session_factory()
        session = MagicMock()
        session.add = MagicMock()
        sf.begin = MagicMock()
        sf.begin.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.begin.return_value.__aexit__ = AsyncMock(return_value=None)

        store = TokenStore(sf)
        token = _make_token()
        await store.store(token)

        sf.begin.assert_called_once()
        session.add.assert_called_once_with(token)

    @pytest.mark.asyncio
    async def test_store_awaits_async_session_add(self):
        sf = _make_session_factory()
        session = MagicMock()

        async def add(_token: AgentTokenModel) -> None:
            return None

        session.add = MagicMock(side_effect=add)
        sf.begin.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.begin.return_value.__aexit__ = AsyncMock(return_value=None)

        await TokenStore(sf).store(_make_token())

        session.add.assert_called_once()


class TestTokenStoreGet:
    @pytest.mark.asyncio
    async def test_get_returns_token_by_agent_id(self):
        sf = _make_session_factory()
        token = _make_token()
        session = MagicMock()
        session.execute = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=token)
        session.execute.return_value = result_mock
        sf.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.return_value.__aexit__ = AsyncMock(return_value=None)

        store = TokenStore(sf)
        result = await store.get("agent-1")

        assert result is token
        session.execute.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_returns_none_for_unknown_agent(self):
        sf = _make_session_factory()
        session = MagicMock()
        session.execute = AsyncMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=None)
        session.execute.return_value = result_mock
        sf.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.return_value.__aexit__ = AsyncMock(return_value=None)

        store = TokenStore(sf)
        result = await store.get("nonexistent")

        assert result is None


class TestTokenStoreRevoke:
    @pytest.mark.asyncio
    async def test_revoke_sets_revoked_at(self):
        sf = _make_session_factory()
        token = _make_token(revoked_at=None, token_id="tok-agent-1")
        # First call to execute (in _by_token_id) returns the token
        # Second call to session.add in the revoke method stores it back
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=token)
        session.execute = AsyncMock(return_value=result_mock)
        session.add = MagicMock()

        sf.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.return_value.__aexit__ = AsyncMock(return_value=None)
        sf.begin = MagicMock()
        sf.begin.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.begin.return_value.__aexit__ = AsyncMock(return_value=None)

        store = TokenStore(sf)
        await store.revoke("tok-agent-1")

        assert token.revoked_at is not None

    @pytest.mark.asyncio
    async def test_revoke_does_nothing_for_unknown_token(self):
        sf = _make_session_factory()
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=result_mock)

        sf.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.return_value.__aexit__ = AsyncMock(return_value=None)

        store = TokenStore(sf)
        await store.revoke("unknown-tok")

        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_revoke_awaits_async_session_add(self):
        sf = _make_session_factory()
        token = _make_token()
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = token
        session.execute = AsyncMock(return_value=result_mock)

        async def add(_token: AgentTokenModel) -> None:
            return None

        session.add = MagicMock(side_effect=add)
        sf.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.return_value.__aexit__ = AsyncMock(return_value=None)
        sf.begin.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.begin.return_value.__aexit__ = AsyncMock(return_value=None)

        await TokenStore(sf).revoke(token.token_id)

        assert token.revoked_at is not None


class TestTokenStoreIncrementHydration:
    @pytest.mark.asyncio
    async def test_increment_hydration_bumps_count(self):
        sf = _make_session_factory()
        token = _make_token(hydration_count=0)
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=token)
        session.execute = AsyncMock(return_value=result_mock)
        session.add = MagicMock()

        sf.begin = MagicMock()
        sf.begin.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.begin.return_value.__aexit__ = AsyncMock(return_value=None)

        store = TokenStore(sf)
        await store.increment_hydration("agent-1")

        assert token.hydration_count == 1

    @pytest.mark.asyncio
    async def test_increment_hydration_noop_for_missing_agent(self):
        sf = _make_session_factory()
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none = MagicMock(return_value=None)
        session.execute = AsyncMock(return_value=result_mock)

        sf.begin = MagicMock()
        sf.begin.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.begin.return_value.__aexit__ = AsyncMock(return_value=None)

        store = TokenStore(sf)
        await store.increment_hydration("nonexistent")

        session.add.assert_not_called()

    @pytest.mark.asyncio
    async def test_increment_hydration_awaits_async_session_add(self):
        sf = _make_session_factory()
        token = _make_token()
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalar_one_or_none.return_value = token
        session.execute = AsyncMock(return_value=result_mock)

        async def add(_token: AgentTokenModel) -> None:
            return None

        session.add = MagicMock(side_effect=add)
        sf.begin.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.begin.return_value.__aexit__ = AsyncMock(return_value=None)

        await TokenStore(sf).increment_hydration(token.agent_id)

        assert token.hydration_count == 1


class TestTokenStoreListExpired:
    @pytest.mark.asyncio
    async def test_list_expired_returns_non_revoked_expired(self):
        sf = _make_session_factory()
        now = datetime(2026, 2, 1, tzinfo=UTC)
        expired = _make_token(
            token_id="tok-expired",
            expires_at=now - timedelta(hours=1),
            revoked_at=None,
        )
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars = MagicMock()
        result_mock.scalars().all = MagicMock(return_value=[expired])
        session.execute = AsyncMock(return_value=result_mock)

        sf.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.return_value.__aexit__ = AsyncMock(return_value=None)

        store = TokenStore(sf)
        result = await store.list_expired(now)

        assert len(result) == 1
        assert result[0].token_id == "tok-expired"

    @pytest.mark.asyncio
    async def test_list_expired_excludes_revoked(self):
        sf = _make_session_factory()
        now = datetime(2026, 2, 1, tzinfo=UTC)
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars = MagicMock()
        result_mock.scalars().all = MagicMock(return_value=[])
        session.execute = AsyncMock(return_value=result_mock)

        sf.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.return_value.__aexit__ = AsyncMock(return_value=None)

        store = TokenStore(sf)
        result = await store.list_expired(now)

        assert len(result) == 0


class TestTokenStoreListAll:
    @pytest.mark.asyncio
    async def test_list_all_returns_all_rows(self):
        sf = _make_session_factory()
        t1 = _make_token(token_id="tok-1")
        t2 = _make_token(token_id="tok-2", revoked_at=datetime(2026, 1, 3, tzinfo=UTC))
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars = MagicMock()
        result_mock.scalars().all = MagicMock(return_value=[t1, t2])
        session.execute = AsyncMock(return_value=result_mock)

        sf.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.return_value.__aexit__ = AsyncMock(return_value=None)

        store = TokenStore(sf)
        result = await store.list_all()

        assert len(result) == 2


class TestTokenStoreListChildren:
    @pytest.mark.asyncio
    async def test_list_children_returns_by_parent(self):
        sf = _make_session_factory()
        t1 = _make_token(token_id="tok-child-1")
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars = MagicMock()
        result_mock.scalars().all = MagicMock(return_value=[t1])
        session.execute = AsyncMock(return_value=result_mock)

        sf.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.return_value.__aexit__ = AsyncMock(return_value=None)

        store = TokenStore(sf)
        result = await store.list_children("parent-xyz")

        assert len(result) == 1
        assert result[0].token_id == "tok-child-1"

    @pytest.mark.asyncio
    async def test_list_children_returns_empty_for_no_children(self):
        sf = _make_session_factory()
        session = MagicMock()
        result_mock = MagicMock()
        result_mock.scalars = MagicMock()
        result_mock.scalars().all = MagicMock(return_value=[])
        session.execute = AsyncMock(return_value=result_mock)

        sf.return_value.__aenter__ = AsyncMock(return_value=session)
        sf.return_value.__aexit__ = AsyncMock(return_value=None)

        store = TokenStore(sf)
        result = await store.list_children("lonely-parent")

        assert result == []
