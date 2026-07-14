"""Structural tests for general_ludd.routers._util."""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.routers._util import get_session_factory


class TestGetSessionFactory:
    def test_returns_none_when_no_factory_set(self) -> None:
        app = FastAPI()
        result = get_session_factory(app)
        assert result is None

    def test_returns_none_when_state_has_no_session_factory(self) -> None:
        app = FastAPI()
        app.state.other_attr = "value"
        result = get_session_factory(app)
        assert result is None

    def test_returns_session_factory_when_set(self) -> None:
        app = FastAPI()
        factory = async_sessionmaker[AsyncSession]()
        app.state._session_factory = factory
        result = get_session_factory(app)
        assert result is factory

    def test_result_is_async_sessionmaker(self) -> None:
        app = FastAPI()
        factory = async_sessionmaker[AsyncSession]()
        app.state._session_factory = factory
        result = get_session_factory(app)
        assert isinstance(result, async_sessionmaker)

    def test_getattr_fallback_returns_none_for_unset(self) -> None:
        app = FastAPI()
        val = getattr(app.state, "_session_factory", None)
        assert val is None
