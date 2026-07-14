"""Structural tests for routers/_util.py — get_session_factory."""

from __future__ import annotations

from unittest.mock import MagicMock

from fastapi import FastAPI

from general_ludd.routers._util import get_session_factory


class TestGetSessionFactory:
    def test_returns_none_when_not_set(self):
        app = FastAPI()
        assert get_session_factory(app) is None

    def test_returns_factory_when_set(self):
        app = FastAPI()
        mock_factory = object()
        app.state._session_factory = mock_factory
        assert get_session_factory(app) is mock_factory

    def test_returns_factory_when_set_on_mock(self):
        app = MagicMock()
        mock_factory = MagicMock()
        app.state._session_factory = mock_factory
        assert get_session_factory(app) is mock_factory

    def test_missing_state_attribute(self):
        app = MagicMock(spec=FastAPI)
        del app.state
        with __import__("pytest").raises(AttributeError):
            get_session_factory(app)
