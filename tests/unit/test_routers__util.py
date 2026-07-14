"""Structural tests for routers/_util.py — get_session_factory."""

from __future__ import annotations

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from general_ludd.routers._util import get_session_factory


class TestGetSessionFactory:
    def test_none_when_no_state_attr(self) -> None:
        app = FastAPI()
        assert get_session_factory(app) is None

    def test_none_when_attr_not_set(self) -> None:
        app = FastAPI()
        app.state.other = "val"
        assert get_session_factory(app) is None

    def test_returns_factory_when_set(self) -> None:
        app = FastAPI()
        factory = async_sessionmaker[AsyncSession]()
        app.state._session_factory = factory
        assert get_session_factory(app) is factory

    def test_result_is_async_sessionmaker(self) -> None:
        app = FastAPI()
        factory = async_sessionmaker[AsyncSession]()
        app.state._session_factory = factory
        assert isinstance(get_session_factory(app), async_sessionmaker)

    def test_factory_none_not_in_state(self) -> None:
        app = FastAPI()
        assert getattr(app.state, "_session_factory", None) is None

    def test_module_exists_and_imports(self) -> None:
        import general_ludd.routers._util as mod

        assert mod is not None
